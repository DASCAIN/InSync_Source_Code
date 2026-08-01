"""
Vector store service.
Manages Qdrant vector indices — adding documents, and querying
the vector store with metadata filtering. Also manages the metadata.json topic registry.
"""

import json
import logging
import os
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models

from config import get_settings

logger = logging.getLogger(__name__)

# Module-level singleton for embeddings and qdrant client
_embeddings: OpenAIEmbeddings | None = None
_qdrant_client: QdrantClient | None = None

COLLECTION_NAME = "insync_materials"


def get_embeddings() -> OpenAIEmbeddings:
    """Return a singleton OpenAIEmbeddings instance."""
    global _embeddings
    if _embeddings is None:
        settings = get_settings()
        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
        _embeddings = OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )
    return _embeddings


def get_qdrant_client() -> QdrantClient:
    """Return a singleton QdrantClient instance."""
    global _qdrant_client
    if _qdrant_client is None:
        url = os.getenv("QDRANT_URL")
        api_key = os.getenv("QDRANT_API_KEY")
        if url:
            logger.info("Connecting to Qdrant at %s", url)
            _qdrant_client = QdrantClient(url=url, api_key=api_key)
        else:
            settings = get_settings()
            default_path = str(settings.VECTOR_STORES_DIR / "qdrant_storage")
            path = os.getenv("QDRANT_PATH", default_path)
            logger.info("Connecting to local Qdrant at %s", path)
            _qdrant_client = QdrantClient(path=path)
    return _qdrant_client


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use as a directory or metadata name."""
    return re.sub(r"[^\w\-]", "_", name.strip().lower())


def get_vector_store() -> QdrantVectorStore:
    """Get the QdrantVectorStore instance."""
    return QdrantVectorStore(
        client=get_qdrant_client(),
        collection_name=COLLECTION_NAME,
        embedding=get_embeddings(),
    )


def add_documents(subject: str, topic: str, documents: list[Document]) -> None:
    """
    Add documents to the Qdrant vector store.

    Args:
        subject: The subject name (stored in document metadata).
        topic: The topic name (stored in document metadata).
        documents: List of LangChain Document objects to index.
    """
    if not documents:
        logger.warning("No documents provided to add_documents for %s/%s", subject, topic)
        return

    # Ensure all documents have subject and topic metadata
    for doc in documents:
        doc.metadata["subject"] = subject
        doc.metadata["topic"] = topic

    logger.info("Adding %d documents to Qdrant collection '%s'", len(documents), COLLECTION_NAME)
    
    url = os.getenv("QDRANT_URL")
    if url:
        QdrantVectorStore.from_documents(
            documents,
            get_embeddings(),
            url=url,
            collection_name=COLLECTION_NAME,
        )
    else:
        settings = get_settings()
        default_path = str(settings.VECTOR_STORES_DIR / "qdrant_storage")
        path = os.getenv("QDRANT_PATH", default_path)
        QdrantVectorStore.from_documents(
            documents,
            get_embeddings(),
            path=path,
            collection_name=COLLECTION_NAME,
        )
    
    logger.info("Successfully added documents to Qdrant.")


def retrieve_documents(
    subject: str,
    topic: str,
    query: str,
    k: int = 10,
) -> list[Document]:
    """
    Retrieve relevant documents from Qdrant.

    Uses similarity search with metadata filtering by subject and topic.
    """
    store = get_vector_store()

    # Qdrant native filter
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

    try:
        results = store.similarity_search(
            query,
            k=k,
            filter=qdrant_filter,
        )
        logger.info(
            "Retrieved %d documents for query in %s/%s",
            len(results),
            subject,
            topic,
        )
        return results
    except Exception as e:
        logger.error("Qdrant search failed: %s", str(e))
        return []

def retrieve_global_documents(query: str, k: int = 5) -> list[Document]:
    """
    Retrieve relevant documents from Qdrant globally (without subject/topic filtering).
    Used for the global AI Tutor chat.
    """
    store = get_vector_store()
    try:
        results = store.similarity_search(query, k=k)
        logger.info("Retrieved %d documents globally for query", len(results))
        return results
    except Exception as e:
        logger.error("Global Qdrant search failed: %s", str(e))
        return []


def retrieve_all_documents(subject: str, topic: str) -> list[Document]:
    """
    Retrieve all documents for a given subject and topic from Qdrant.
    Sorts by chunk_index to keep original text order.
    """
    client = get_qdrant_client()
    
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
    
    try:
        # Scroll API retrieves all matching points
        records, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=qdrant_filter,
            limit=10000,
            with_payload=True,
            with_vectors=False
        )
        
        filtered_docs = []
        for record in records:
            payload = record.payload or {}
            # LangChain Qdrant stores page_content and metadata in payload
            metadata = payload.get("metadata", {})
            page_content = payload.get("page_content", "")
            
            doc = Document(page_content=page_content, metadata=metadata)
            filtered_docs.append(doc)

        # Sort by chunk_index
        filtered_docs.sort(key=lambda doc: doc.metadata.get("chunk_index", 0))

        logger.info(
            "Retrieved all %d documents for topic '%s' in subject '%s'",
            len(filtered_docs),
            topic,
            subject,
        )
        return filtered_docs
    except Exception as e:
        logger.error("Failed to retrieve all documents from Qdrant: %s", str(e))
        return []


def get_all_topics() -> dict[str, list[str]]:
    """Read and return all registered subjects and topics."""
    settings = get_settings()
    metadata_file = settings.METADATA_FILE

    if not metadata_file.exists():
        return {}

    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.error("Error reading metadata file: %s", str(e))
        return {}


def register_topic(subject: str, topic: str) -> None:
    """Register a topic under a subject in the metadata registry."""
    settings = get_settings()
    metadata_file = settings.METADATA_FILE
    metadata_file.parent.mkdir(parents=True, exist_ok=True)

    if metadata_file.exists():
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {}
    else:
        data = {}

    if subject not in data:
        data[subject] = []

    if topic not in data[subject]:
        data[subject].append(topic)
        logger.info("Registered new topic: %s/%s", subject, topic)

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def delete_documents_by_source(source_file: str) -> None:
    """
    Delete all documents from Qdrant that match the given source file.
    """
    client = get_qdrant_client()
    
    qdrant_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.source_file",
                match=models.MatchValue(value=source_file),
            )
        ]
    )
    
    try:
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.FilterSelector(filter=qdrant_filter)
        )
        logger.info("Successfully deleted documents for source file '%s'", source_file)
    except Exception as e:
        logger.error("Failed to delete documents for source file '%s': %s", source_file, str(e))
