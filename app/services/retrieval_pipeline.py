"""
Hybrid retrieval pipeline service.

Implements metadata filtering, Multi-Query expansion, vector search,
BM25 search, Reciprocal Rank Fusion (RRF), Cross-Encoder reranking,
and Parent chunk expansion.
"""

import logging
import re
from pathlib import Path
from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever
from sentence_transformers import CrossEncoder
from langchain_core.prompts import ChatPromptTemplate
from qdrant_client import models

from config import get_settings
from app.services.vector_store import (
    get_vector_store,
    retrieve_all_documents,
)

logger = logging.getLogger(__name__)

_cross_encoder = None

def get_cross_encoder() -> CrossEncoder:
    """Get or load singleton Cross-Encoder model."""
    global _cross_encoder
    if _cross_encoder is None:
        logger.info("Loading Cross-Encoder model: cross-encoder/ms-marco-MiniLM-L-6-v2")
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _cross_encoder


async def generate_multi_queries(query: str, subject: str, topic: str, count: int = 3) -> list[str]:
    """Use Gemini to expand the search query into multiple alternative forms."""
    from app.services.generator import get_llm
    
    system_prompt = (
        "You are an expert AI educational search assistant. Your task is to generate {count} alternative "
        "search queries based on the user\'s input query to cover different aspects of the topic (e.g. definitions, formulas, examples, limitations, etc.).\n"
        "Keep the queries technical, focused, and concise. "
        "Output ONLY the generated alternative queries, one per line. Do NOT include numbers, bullets, formatting, or extra text."
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Subject: {subject}\nTopic: {topic}\nQuery: {query}\n\nGenerate {count} alternative queries:")
    ])
    
    try:
        llm = get_llm(temperature=0.6)
        chain = prompt | llm
        response = await chain.ainvoke({"query": query, "subject": subject, "topic": topic, "count": count})
        
        queries = [query]
        for line in response.content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Clean any numbers, bullets
            line = re.sub(r"^\d+[\.\)\s-]*", "", line).strip()
            line = re.sub(r"^[-*•\s]*", "", line).strip()
            if line:
                queries.append(line)
        
        # Remove duplicates
        unique_queries = list(dict.fromkeys(queries))
        logger.info("Expanded queries for \'%s\': %s", query, unique_queries)
        return unique_queries
    except Exception as e:
        logger.error("Failed to generate multi-queries: %s. Using original query only.", str(e))
        return [query]


def reciprocal_rank_fusion(retrieval_runs: list[list[Document]], k_rrf: int = 60) -> list[tuple[Document, float]]:
    """
    Perform Reciprocal Rank Fusion on multiple lists of retrieved documents.
    Returns sorted list of (Document, score) tuples.
    """
    scores = {}
    doc_map = {}
    
    for run in retrieval_runs:
        for rank, doc in enumerate(run, start=1):
            doc_id = (doc.page_content, doc.metadata.get("source_file", ""))
            
            if doc_id not in doc_map:
                doc_map[doc_id] = doc
                scores[doc_id] = 0.0
                
            scores[doc_id] += 1.0 / (k_rrf + rank)
            
    sorted_doc_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [(doc_map[doc_id], scores[doc_id]) for doc_id in sorted_doc_ids]


def rerank_documents(query: str, documents: list[Document], top_n: int = 10) -> list[Document]:
    """Rerank documents using Cross-Encoder model."""
    if not documents:
        return []
        
    model = get_cross_encoder()
    pairs = [[query, doc.page_content] for doc in documents]
    
    try:
        scores = model.predict(pairs)
        doc_scores = list(zip(documents, scores))
        doc_scores.sort(key=lambda x: x[1], reverse=True)
        
        reranked = [doc for doc, score in doc_scores[:top_n]]
        logger.info("Reranked %d documents down to %d.", len(documents), len(reranked))
        return reranked
    except Exception as e:
        logger.error("Cross-Encoder reranking failed: %s. Returning top documents directly.", str(e))
        return documents[:top_n]


async def retrieve_hybrid(
    subject: str,
    topic: str,
    query: str,
    k_vector: int = 15,
    k_bm25: int = 15,
    top_n_child: int = 8,
) -> list[Document]:
    """
    Execute Hybrid Retrieval with Multi-Query expansion, Vector Search,
    BM25 Search, RRF ranking, Cross-Encoder reranking, and Parent chunk expansion.
    """
    topic_docs = retrieve_all_documents(subject, topic)
    
    if not topic_docs:
        logger.warning("No documents found in store for subject/topic: %s/%s", subject, topic)
        return []
        
    try:
        bm25_retriever = BM25Retriever.from_documents(topic_docs)
        bm25_retriever.k = k_bm25
    except Exception as e:
        logger.error("Failed to initialize BM25: %s", str(e))
        bm25_retriever = None
        
    queries = await generate_multi_queries(query, subject, topic)
    
    retrieval_runs = []
    store = get_vector_store()
    qdrant_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.subject",
                match=models.MatchValue(value=subject),
            ),
            models.FieldCondition(
                key="metadata.topic",
                match=models.MatchValue(value=topic),
            ),
        ]
    )
    
    for q in queries:
        try:
            vector_results = store.similarity_search(
                q,
                k=k_vector,
                filter=qdrant_filter,
            )
            retrieval_runs.append(vector_results)
        except Exception as ve:
            logger.error("Vector similarity search failed for query '%s': %s", q, str(ve))
            
        if bm25_retriever:
            try:
                bm25_results = bm25_retriever.invoke(q)
                retrieval_runs.append(bm25_results)
            except Exception as be:
                logger.error("BM25 retrieval failed for query \'%s\': %s", q, str(be))
                
    fused_results = reciprocal_rank_fusion(retrieval_runs, k_rrf=60)
    
    top_30_child_docs = [doc for doc, score in fused_results[:30]]
    
    if not top_30_child_docs:
        logger.warning("No documents returned from RRF fusion.")
        return []
        
    top_child_docs = rerank_documents(query, top_30_child_docs, top_n=top_n_child)
    
    parent_docs = []
    seen_parents = set()
    
    for child_doc in top_child_docs:
        parent_id = child_doc.metadata.get("parent_id")
        parent_content = child_doc.metadata.get("parent_content")
        
        if parent_id and parent_content:
            if parent_id not in seen_parents:
                seen_parents.add(parent_id)
                parent_metadata = child_doc.metadata.copy()
                if "parent_content" in parent_metadata:
                    parent_metadata.pop("parent_content")
                
                p_doc = Document(
                    page_content=parent_content,
                    metadata=parent_metadata
                )
                parent_docs.append(p_doc)
        else:
            parent_docs.append(child_doc)
            
    logger.info(
        "Hybrid retrieval returned %d Parent documents from %d Child reranks.",
        len(parent_docs),
        len(top_child_docs),
    )
    return parent_docs
