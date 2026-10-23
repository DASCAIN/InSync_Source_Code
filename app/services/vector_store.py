"""
Vector store service.
Manages Qdrant vector indices — adding documents, and querying
the vector store with metadata filtering. Also manages the metadata.json topic registry.
"""

import json
import hashlib
import logging
import os
import re
import uuid
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
_indexed_collections: set[str] = set()


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


def build_document_identity(
    subject: str,
    topic: str,
    documents: list[Document],
    *,
    file_sha256: str = "",
    tenant_id: str = "default",
) -> dict[str, str]:
    """Build stable identities for exact retries and logical source replacement."""
    settings = get_settings()
    source_file = str(documents[0].metadata.get("source_file") or "uploaded-material")
    if not file_sha256:
        content = "\0".join(document.page_content for document in documents)
        file_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    identity_parts = (
        str(tenant_id or "default"),
        subject,
        topic,
        source_file.casefold(),
        file_sha256,
        settings.EMBEDDING_MODEL,
        settings.INGESTION_PIPELINE_VERSION,
    )
    document_id = hashlib.sha256("\0".join(identity_parts).encode("utf-8")).hexdigest()
    source_key = hashlib.sha256(
        "\0".join((str(tenant_id or "default"), subject, topic, source_file.casefold())).encode("utf-8")
    ).hexdigest()
    return {
        "document_id": document_id,
        "document_hash": file_sha256,
        "source_key": source_key,
        "source_file": source_file,
        "tenant_id": str(tenant_id or "default"),
        "pipeline_version": settings.INGESTION_PIPELINE_VERSION,
        "embedding_model": settings.EMBEDDING_MODEL,
    }


def get_document_index_status(
    subject: str,
    topic: str,
    source_file: str,
    file_sha256: str,
    *,
    tenant_id: str = "default",
) -> dict[str, object]:
    """Check whether an exact document version is completely indexed."""
    probe = [Document(page_content="", metadata={"source_file": source_file})]
    identity = build_document_identity(
        subject,
        topic,
        probe,
        file_sha256=file_sha256,
        tenant_id=tenant_id,
    )
    try:
        client = get_qdrant_client()
        if not client.collection_exists(COLLECTION_NAME):
            return {**identity, "complete": False, "indexed_count": 0}
        _ensure_payload_indexes(client)
        qdrant_filter = _document_filter(identity["document_id"])
        records, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=qdrant_filter,
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return {**identity, "complete": False, "indexed_count": 0}
        payload = records[0].payload or {}
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        expected_count = int(metadata.get("chunk_count") or 0)
        indexed_count = int(
            client.count(
                collection_name=COLLECTION_NAME,
                count_filter=qdrant_filter,
                exact=True,
            ).count
        )
        return {
            **identity,
            "complete": expected_count > 0 and indexed_count == expected_count,
            "indexed_count": indexed_count,
            "expected_count": expected_count,
        }
    except Exception as exc:
        # A status optimization must never prevent a normal ingestion attempt.
        logger.warning("Could not check Qdrant document status: %s", exc)
        return {**identity, "complete": False, "indexed_count": 0}


def _point_ids(document_id: str, count: int) -> list[str]:
    return [
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"insync:{document_id}:{index}"))
        for index in range(count)
    ]


def _document_filter(document_id: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.document_id",
                match=models.MatchValue(value=document_id),
            )
        ]
    )


def _scroll_point_ids(client: QdrantClient, qdrant_filter: models.Filter) -> set[str]:
    point_ids: set[str] = set()
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=qdrant_filter,
            limit=256,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        point_ids.update(str(record.id) for record in records)
        if offset is None:
            break
    return point_ids


def _ensure_payload_indexes(client: QdrantClient) -> None:
    """Create indexes needed by Qdrant Cloud strict-mode filters once per process."""
    if COLLECTION_NAME in _indexed_collections:
        return
    failed = False
    for field_name in (
        "metadata.tenant_id",
        "metadata.document_id",
        "metadata.source_key",
        "metadata.subject",
        "metadata.topic",
        "metadata.source_file",
    ):
        try:
            client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        except Exception as exc:
            # Creating an existing index is harmless across concurrent workers.
            failed = True
            logger.warning("Payload index %s was not created: %s", field_name, exc)
    if not failed:
        _indexed_collections.add(COLLECTION_NAME)


def _delete_superseded_source_versions(
    client: QdrantClient,
    identity: dict[str, str],
) -> None:
    """Delete older vectors for the same tenant-scoped source after a safe upsert."""
    replacement_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.source_key",
                match=models.MatchValue(value=identity["source_key"]),
            ),
        ],
        must_not=[
            models.FieldCondition(
                key="metadata.document_id",
                match=models.MatchValue(value=identity["document_id"]),
            )
        ],
    )
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.FilterSelector(filter=replacement_filter),
        wait=True,
    )


def add_documents(
    subject: str,
    topic: str,
    documents: list[Document],
    *,
    file_sha256: str = "",
    tenant_id: str = "default",
    replace_source: bool = True,
) -> dict[str, object]:
    """
    Add documents to the Qdrant vector store.

    Args:
        subject: The subject name (stored in document metadata).
        topic: The topic name (stored in document metadata).
        documents: List of LangChain Document objects to index.
    """
    if not documents:
        logger.warning("No documents provided to add_documents for %s/%s", subject, topic)
        return {"status": "empty", "indexed_count": 0}

    settings = get_settings()
    identity = build_document_identity(
        subject,
        topic,
        documents,
        file_sha256=file_sha256,
        tenant_id=tenant_id,
    )
    point_ids = _point_ids(identity["document_id"], len(documents))

    # Keep LangChain-compatible payloads while adding production ingestion metadata.
    for index, doc in enumerate(documents):
        doc.metadata["subject"] = subject
        doc.metadata["topic"] = topic
        doc.metadata.update(identity)
        doc.metadata["chunk_index"] = index
        doc.metadata["chunk_count"] = len(documents)

    client = get_qdrant_client()
    collection_exists = client.collection_exists(COLLECTION_NAME)
    existing_ids: set[str] = set()
    if collection_exists:
        _ensure_payload_indexes(client)
        existing_ids = _scroll_point_ids(client, _document_filter(identity["document_id"]))

    expected_ids = set(point_ids)
    missing_indices = [
        index for index, point_id in enumerate(point_ids) if point_id not in existing_ids
    ]

    if missing_indices:
        logger.info(
            "Embedding %d/%d missing chunks for document %s",
            len(missing_indices),
            len(documents),
            identity["document_id"][:12],
        )
        embedding_batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)
        upsert_batch_size = max(1, settings.QDRANT_UPSERT_BATCH_SIZE)
        pending_points: list[models.PointStruct] = []

        for start in range(0, len(missing_indices), embedding_batch_size):
            batch_indices = missing_indices[start:start + embedding_batch_size]
            vectors = get_embeddings().embed_documents(
                [documents[index].page_content for index in batch_indices]
            )
            if len(vectors) != len(batch_indices):
                raise RuntimeError(
                    "Embedding provider returned an unexpected number of vectors "
                    f"({len(vectors)} for {len(batch_indices)} chunks)"
                )

            if not collection_exists:
                try:
                    client.create_collection(
                        collection_name=COLLECTION_NAME,
                        vectors_config=models.VectorParams(
                            size=len(vectors[0]),
                            distance=models.Distance.COSINE,
                        ),
                    )
                except Exception:
                    if not client.collection_exists(COLLECTION_NAME):
                        raise
                collection_exists = True
                _ensure_payload_indexes(client)

            for index, vector in zip(batch_indices, vectors):
                pending_points.append(
                    models.PointStruct(
                        id=point_ids[index],
                        vector=vector,
                        payload={
                            "page_content": documents[index].page_content,
                            "metadata": documents[index].metadata,
                        },
                    )
                )

            while len(pending_points) >= upsert_batch_size:
                batch = pending_points[:upsert_batch_size]
                del pending_points[:upsert_batch_size]
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=batch,
                    wait=True,
                )

        if pending_points:
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=pending_points,
                wait=True,
            )
    else:
        logger.info(
            "Document %s is already indexed; skipping embeddings and upsert.",
            identity["document_id"][:12],
        )

    stale_ids = existing_ids - expected_ids
    if stale_ids:
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.PointIdsList(points=sorted(stale_ids)),
            wait=True,
        )

    if replace_source and collection_exists:
        _delete_superseded_source_versions(client, identity)

    indexed_count = len(missing_indices)
    logger.info(
        "Qdrant ingestion complete for %s: %d new, %d reused, %d stale removed.",
        identity["document_id"][:12],
        indexed_count,
        len(documents) - indexed_count,
        len(stale_ids),
    )
    return {
        "status": "indexed" if indexed_count else "already_indexed",
        "indexed_count": indexed_count,
        "reused_count": len(documents) - indexed_count,
        "removed_count": len(stale_ids),
        "document_id": identity["document_id"],
        "document_hash": identity["document_hash"],
    }


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
