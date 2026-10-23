import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase
from langchain_core.documents import Document

from app.services.document_processor import extract_text_from_pdf
from app.services.inngest_functions import _extract_to_artifact
from app.services.vector_store import (
    _point_ids,
    add_documents,
    build_document_identity,
    get_document_index_status,
)


class AdaptivePdfExtractionTests(SimpleTestCase):
    @patch("config.get_settings")
    @patch("app.services.document_processor.pymupdf4llm.to_markdown")
    def test_ocrs_only_pages_without_meaningful_native_text(self, to_markdown, get_settings):
        get_settings.return_value = SimpleNamespace(
            PDF_OCR_MIN_NATIVE_CHARS=40,
            PDF_OCR_BATCH_SIZE=8,
        )
        native_text = "This page already has a complete and usable native text layer."
        scanned_text = "Recognized text from a scanned page with enough useful content."
        to_markdown.side_effect = [
            [{"text": native_text}, {"text": "   "}],
            [{"text": scanned_text}],
        ]

        pages = extract_text_from_pdf("lecture.pdf")

        self.assertEqual([page["page_number"] for page in pages], [1, 2])
        self.assertEqual(pages[0]["extraction_method"], "native")
        self.assertEqual(pages[1]["extraction_method"], "ocr")
        self.assertEqual(pages[1]["text"], scanned_text)
        self.assertEqual(
            to_markdown.call_args_list,
            [
                call("lecture.pdf", page_chunks=True, use_ocr=False),
                call(
                    "lecture.pdf",
                    pages=[1],
                    page_chunks=True,
                    force_ocr=True,
                ),
            ],
        )

    @patch("config.get_settings")
    @patch("app.services.document_processor.pymupdf4llm.to_markdown")
    def test_skips_ocr_when_every_page_has_native_text(self, to_markdown, get_settings):
        get_settings.return_value = SimpleNamespace(
            PDF_OCR_MIN_NATIVE_CHARS=40,
            PDF_OCR_BATCH_SIZE=8,
        )
        to_markdown.return_value = [
            {"text": "Native text is present and sufficiently detailed on this page."},
            {"text": "This second page also contains a useful searchable text layer."},
        ]

        pages = extract_text_from_pdf("slides.pdf")

        self.assertEqual(to_markdown.call_count, 1)
        self.assertTrue(all(page["extraction_method"] == "native" for page in pages))


class QdrantIdempotencyTests(SimpleTestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            EMBEDDING_MODEL="text-embedding-3-large",
            INGESTION_PIPELINE_VERSION="rag-v2-adaptive-ocr",
            EMBEDDING_BATCH_SIZE=64,
            QDRANT_UPSERT_BATCH_SIZE=64,
        )
        self.documents = [
            Document(
                page_content="first chunk",
                metadata={"source_file": "lecture.pdf", "page": 1},
            ),
            Document(
                page_content="second chunk",
                metadata={"source_file": "lecture.pdf", "page": 2},
            ),
        ]

    @patch("app.services.vector_store.get_settings")
    def test_identity_is_scoped_and_stable(self, get_settings):
        get_settings.return_value = self.settings
        first = build_document_identity(
            "ML", "SVM", self.documents, file_sha256="abc", tenant_id="tenant-1"
        )
        retry = build_document_identity(
            "ML", "SVM", self.documents, file_sha256="abc", tenant_id="tenant-1"
        )
        another_tenant = build_document_identity(
            "ML", "SVM", self.documents, file_sha256="abc", tenant_id="tenant-2"
        )

        self.assertEqual(first["document_id"], retry["document_id"])
        self.assertNotEqual(first["document_id"], another_tenant["document_id"])
        self.assertEqual(_point_ids(first["document_id"], 2), _point_ids(retry["document_id"], 2))

    @patch("app.services.vector_store._indexed_collections", set())
    @patch("app.services.vector_store.get_qdrant_client")
    @patch("app.services.vector_store.get_settings")
    def test_complete_qdrant_document_can_skip_the_entire_pipeline(
        self, get_settings, get_client
    ):
        get_settings.return_value = self.settings
        client = MagicMock()
        client.collection_exists.return_value = True
        client.scroll.return_value = (
            [SimpleNamespace(payload={"metadata": {"chunk_count": 2}})],
            None,
        )
        client.count.return_value = SimpleNamespace(count=2)
        get_client.return_value = client

        status = get_document_index_status(
            "ML",
            "SVM",
            "lecture.pdf",
            "abc",
            tenant_id="tenant-1",
        )

        self.assertTrue(status["complete"])
        self.assertEqual(status["indexed_count"], 2)

    @patch("app.services.vector_store._indexed_collections", set())
    @patch("app.services.vector_store.get_embeddings")
    @patch("app.services.vector_store.get_qdrant_client")
    @patch("app.services.vector_store.get_settings")
    def test_retry_reuses_existing_points_without_embedding(
        self, get_settings, get_client, get_embeddings
    ):
        get_settings.return_value = self.settings
        identity = build_document_identity(
            "ML", "SVM", self.documents, file_sha256="abc", tenant_id="tenant-1"
        )
        expected_ids = _point_ids(identity["document_id"], len(self.documents))

        client = MagicMock()
        client.collection_exists.return_value = True
        client.scroll.return_value = (
            [SimpleNamespace(id=point_id) for point_id in expected_ids],
            None,
        )
        get_client.return_value = client

        result = add_documents(
            "ML",
            "SVM",
            self.documents,
            file_sha256="abc",
            tenant_id="tenant-1",
        )

        self.assertEqual(result["status"], "already_indexed")
        self.assertEqual(result["reused_count"], 2)
        get_embeddings.assert_not_called()
        client.upsert.assert_not_called()

    @patch("app.services.vector_store._indexed_collections", set())
    @patch("app.services.vector_store.get_embeddings")
    @patch("app.services.vector_store.get_qdrant_client")
    @patch("app.services.vector_store.get_settings")
    def test_missing_points_are_embedded_and_upserted_with_deterministic_ids(
        self, get_settings, get_client, get_embeddings
    ):
        get_settings.return_value = self.settings
        client = MagicMock()
        client.collection_exists.return_value = True
        client.scroll.return_value = ([], None)
        get_client.return_value = client
        embeddings = MagicMock()
        embeddings.embed_documents.return_value = [[0.1, 0.2], [0.2, 0.3]]
        get_embeddings.return_value = embeddings

        result = add_documents(
            "ML",
            "SVM",
            self.documents,
            file_sha256="abc",
            tenant_id="tenant-1",
        )

        self.assertEqual(result["status"], "indexed")
        self.assertEqual(result["indexed_count"], 2)
        points = client.upsert.call_args.kwargs["points"]
        expected_ids = _point_ids(result["document_id"], 2)
        self.assertEqual([str(point.id) for point in points], expected_ids)
        self.assertTrue(all(point.payload["metadata"]["document_id"] == result["document_id"] for point in points))


class ExtractionArtifactReuseTests(SimpleTestCase):
    @patch("app.services.inngest_functions.process_file")
    def test_plan_mode_artifact_prevents_a_second_pdf_parse(self, process_file):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "pages.json"
            artifact.write_text(
                json.dumps({
                    "file_sha256": "known-hash",
                    "pages": [{"text": "Already extracted", "page_number": 1}],
                }),
                encoding="utf-8",
            )

            result = _extract_to_artifact(
                "does-not-need-to-exist.pdf",
                "job-1",
                existing_artifact_path=str(artifact),
                known_file_hash="known-hash",
            )

        self.assertTrue(result["artifact_reused"])
        self.assertEqual(result["page_count"], 1)
        process_file.assert_not_called()
