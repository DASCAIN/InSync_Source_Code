"""Small, durable artifacts shared between ingestion steps.

Inngest step results should contain references and metrics, not an entire parsed
document. The application already requires the uploaded file to be visible to
the worker, so these artifacts live beside the existing upload job files.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document

from config import get_settings


def sha256_file(file_path: str | Path, block_size: int = 1024 * 1024) -> str:
    """Hash a file incrementally so large uploads are never loaded into memory."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def artifact_path(artifact_id: str, kind: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", artifact_id)[:160] or "ingestion"
    safe_kind = re.sub(r"[^A-Za-z0-9_.-]", "_", kind)[:40] or "data"
    directory = get_settings().UPLOADS_DIR / "jobs" / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe_id}.{safe_kind}.json"


def write_json_artifact(path: str | Path, value: Any) -> str:
    """Atomically write JSON and return the resolved artifact path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return str(target.resolve())


def read_json_artifact(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_pages_artifact(
    artifact_id: str,
    pages: list[dict[str, Any]],
    *,
    file_sha256: str,
) -> str:
    return write_json_artifact(
        artifact_path(artifact_id, "pages"),
        {
            "file_sha256": file_sha256,
            "pages": pages,
        },
    )


def read_pages_artifact(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    data = read_json_artifact(path)
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise ValueError(f"Invalid extracted-pages artifact: {path}")
    return str(data.get("file_sha256") or ""), data["pages"]


def write_documents_artifact(
    artifact_id: str,
    documents: Iterable[Document],
    *,
    file_sha256: str,
) -> str:
    serialized = [
        {"page_content": document.page_content, "metadata": document.metadata}
        for document in documents
    ]
    return write_json_artifact(
        artifact_path(artifact_id, "chunks"),
        {
            "file_sha256": file_sha256,
            "documents": serialized,
        },
    )


def read_documents_artifact(path: str | Path) -> tuple[str, list[Document]]:
    data = read_json_artifact(path)
    raw_documents = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(raw_documents, list):
        raise ValueError(f"Invalid chunk artifact: {path}")
    documents = [
        Document(
            page_content=str(item.get("page_content") or ""),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in raw_documents
        if isinstance(item, dict)
    ]
    return str(data.get("file_sha256") or ""), documents


def cleanup_artifacts(*paths: str | Path) -> None:
    if get_settings().KEEP_INGESTION_ARTIFACTS:
        return
    for path in paths:
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            # Cleanup is best-effort and must not turn a completed ingestion into
            # a failed job.
            continue
