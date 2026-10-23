import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.services.notes_pipeline import (
    HierarchicalNotesPipeline,
    sections_from_pages,
    strip_display_metadata,
)


class FakeCompletion:
    def __init__(self):
        self.analysis_calls = 0
        self._lock = threading.Lock()

    def __call__(self, messages, json_mode, max_tokens):
        system = messages[0]["content"]
        user = messages[1]["content"]
        if system.startswith("Extract a rich evidence card"):
            with self._lock:
                self.analysis_calls += 1
            return json.dumps({
                "summary": user[-300:],
                "concepts": ["Coverage"],
                "definitions": [],
                "formulas": [],
                "examples": [],
                "key_points": ["A source-backed point"],
            })
        if system.startswith("Design a rigorous"):
            # An empty outline exercises the deterministic, coverage-preserving fallback.
            return json.dumps({"outline": []})
        if system.startswith("Write one polished"):
            evidence = json.loads(user.split("Evidence cards:\n", 1)[1])
            return json.dumps({
                "title": evidence[0]["title"],
                "list_type": "ul",
                "items": [
                    {
                        "text": card["summary"],
                        "source_section_ids": [card["section_id"]],
                    }
                    for card in evidence
                ],
            })
        raise AssertionError(f"Unexpected prompt: {system[:80]}")


class HierarchicalNotesPipelineTests(unittest.TestCase):
    def test_legacy_source_metadata_is_removed_from_display_text(self):
        text = (
            "Update centroids to $\\mu_1 = 3$. "
            "[Source: K_Means.pdf, Page: 4-7, Section: Worked Example]"
        )

        self.assertEqual(
            strip_display_metadata(text),
            "Update centroids to $\\mu_1 = 3$.",
        )

    def test_long_source_is_bounded_without_losing_late_pages(self):
        pages = [
            {
                "page_number": page,
                "text": (f"PAGE_{page}_MARKER " + (f"page-{page} content " * 250)),
            }
            for page in range(1, 7)
        ]

        sections = sections_from_pages(pages, "chapter.pdf", max_chars=900)

        self.assertGreater(len(sections), len(pages))
        self.assertTrue(all(len(section.text) <= 900 for section in sections))
        for page in range(1, 7):
            self.assertTrue(
                any(f"PAGE_{page}_MARKER" in section.text for section in sections),
                f"page {page} was dropped",
            )

    def test_pipeline_covers_every_section_without_exposing_source_metadata(self):
        pages = [
            {"page_number": page, "text": f"# Section {page}\nEvidence from page {page}."}
            for page in range(1, 9)
        ]
        sections = sections_from_pages(pages, "chapter.pdf")
        completion = FakeCompletion()

        with tempfile.TemporaryDirectory() as temporary:
            pipeline = HierarchicalNotesPipeline(
                completion=completion,
                cache_dir=Path(temporary),
                model_name="test-model",
                max_workers=2,
            )
            result = pipeline.generate(sections, "Science", "Coverage")

        self.assertEqual(result["source_sections"], len(sections))
        self.assertEqual(result["evidence_cards"], len(sections))
        self.assertNotIn("Source:", result["markdown"])
        self.assertNotIn("chapter.pdf", result["markdown"])
        self.assertTrue(
            all(
                "Source:" not in item
                for section in result["notes"]
                for item in section["items"]
            )
        )

    def test_evidence_cards_are_reused_from_cache(self):
        sections = sections_from_pages(
            [{"page_number": 1, "text": "# Cached section\nStable source content."}],
            "cached.pdf",
        )
        completion = FakeCompletion()

        with tempfile.TemporaryDirectory() as temporary:
            pipeline = HierarchicalNotesPipeline(
                completion=completion,
                cache_dir=Path(temporary),
                model_name="test-model",
                max_workers=1,
            )
            pipeline.generate(sections, "Science", "Caching")
            first_analysis_calls = completion.analysis_calls
            pipeline.generate(sections, "Science", "Caching")

        self.assertEqual(first_analysis_calls, 1)
        self.assertEqual(completion.analysis_calls, 1)

    def test_outline_validation_deduplicates_and_restores_missing_sources(self):
        sections = sections_from_pages(
            [
                {"page_number": page, "text": f"# Topic {page}\nEvidence {page}"}
                for page in range(1, 24)
            ],
            "outline.pdf",
            max_chars=30,
        )
        cards = [
            HierarchicalNotesPipeline._fallback_card(section)
            for section in sections
        ]
        raw_outline = [{
            "title": "Incomplete outline",
            "list_type": "ul",
            "source_section_ids": [sections[0].section_id, sections[0].section_id],
        }]

        outline = HierarchicalNotesPipeline._validated_outline(raw_outline, cards)
        assigned = [
            section_id
            for item in outline
            for section_id in item["source_section_ids"]
        ]

        self.assertCountEqual(assigned, [section.section_id for section in sections])
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertTrue(
            all(len(item["source_section_ids"]) <= 10 for item in outline)
        )

    def test_outline_may_reuse_evidence_for_different_learning_sections(self):
        sections = sections_from_pages(
            [{"page_number": 1, "text": "# K-Means\nTheory, formula, and algorithm."}],
            "chapter.pdf",
        )
        cards = [HierarchicalNotesPipeline._fallback_card(sections[0])]
        source_id = sections[0].section_id

        outline = HierarchicalNotesPipeline._validated_outline(
            [
                {"title": "Theory", "list_type": "ul", "source_section_ids": [source_id]},
                {"title": "Mathematics", "list_type": "ul", "source_section_ids": [source_id]},
                {"title": "Algorithm", "list_type": "ol", "source_section_ids": [source_id]},
            ],
            cards,
        )

        self.assertEqual(len(outline), 3)
        self.assertTrue(all(item["source_section_ids"] == [source_id] for item in outline))


if __name__ == "__main__":
    unittest.main()
