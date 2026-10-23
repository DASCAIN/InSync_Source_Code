"""Coverage-aware, hierarchical study-note generation.

The pipeline deliberately separates whole-document coverage from detailed
generation:

1. Split every source page/parent chunk into bounded source sections.
2. Map each source section to a small, cached evidence card.
3. Build and validate an outline that assigns every evidence card once.
4. Generate each note section only from its assigned evidence cards.
5. Keep validated source IDs internal so grounding metadata is not shown in notes.

No model request receives the complete source document.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from openai import OpenAI


logger = logging.getLogger(__name__)

PIPELINE_VERSION = "hierarchical-notes-v2"
MAX_SOURCE_SECTION_CHARS = 12_000
MAX_CARD_FIELD_CHARS = 2_000
MAX_WORKERS = 3
MAX_CARDS_PER_NOTE_SECTION = 10

Completion = Callable[[list[dict[str, str]], bool, int], str]


@dataclass(frozen=True)
class SourceSection:
    section_id: str
    source_file: str
    title: str
    page_start: int
    page_end: int
    text: str


def _clean_text(value: Any) -> str:
    return re.sub(r"\n{3,}", "\n\n", str(value or "").strip())


def strip_display_metadata(value: Any) -> str:
    """Remove legacy source annotations while retaining the educational text."""
    text = str(value or "")
    text = re.sub(r"\s*\[Source:[^\]\n]*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(Ref:\s*p\.?\s*[^)]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?im)^\s*Source (?:title|pages?):[^\n]*\n?", "", text)
    return text.strip()


def _heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = re.sub(r"^#{1,6}\s+", "", line.strip()).strip()
        if line.strip().startswith("#") and candidate:
            return candidate[:160]
    return fallback


def _bounded_parts(text: str, max_chars: int) -> list[str]:
    """Split text without dropping content, preferring paragraph boundaries."""
    text = _clean_text(text)
    if not text:
        return []

    parts: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > max_chars:
            if current:
                parts.append("\n\n".join(current))
                current = []
                current_size = 0
            split_at = paragraph.rfind(" ", 0, max_chars)
            if split_at < max_chars // 2:
                split_at = max_chars
            parts.append(paragraph[:split_at].strip())
            paragraph = paragraph[split_at:].strip()
        extra = len(paragraph) + (2 if current else 0)
        if current and current_size + extra > max_chars:
            parts.append("\n\n".join(current))
            current = []
            current_size = 0
        if paragraph:
            current.append(paragraph)
            current_size += len(paragraph) + (2 if len(current) > 1 else 0)
    if current:
        parts.append("\n\n".join(current))
    return parts


def _section_id(
    source_file: str,
    page_start: int,
    page_end: int,
    text: str,
    salt: str = "",
) -> str:
    digest = hashlib.sha256(
        f"{source_file}|{page_start}|{page_end}|{salt}|{text}".encode("utf-8")
    ).hexdigest()[:16]
    return f"src-{digest}"


def sections_from_pages(
    pages: Sequence[dict[str, Any]],
    source_file: str,
    max_chars: int = MAX_SOURCE_SECTION_CHARS,
) -> list[SourceSection]:
    """Group adjacent pages into bounded logical source sections."""
    pieces: list[tuple[int, int, int, str]] = []
    for index, page in enumerate(pages):
        text = _clean_text(page.get("text"))
        if not text:
            continue
        try:
            page_number = int(page.get("page_number", index + 1))
        except (TypeError, ValueError):
            page_number = index + 1
        page_parts = _bounded_parts(text, max_chars)
        for part_index, part in enumerate(page_parts, start=1):
            pieces.append((index, page_number, part_index, part))

    sections: list[SourceSection] = []
    current: list[tuple[int, int, int, str]] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        combined = "\n\n".join(piece[3] for piece in current)
        page_start = current[0][1]
        page_end = current[-1][1]
        salt = ",".join(f"{piece[0]}-{piece[2]}" for piece in current)
        sections.append(
            SourceSection(
                section_id=_section_id(
                    source_file,
                    page_start,
                    page_end,
                    combined,
                    salt=salt,
                ),
                source_file=source_file,
                title=_heading(combined, f"Pages {page_start}-{page_end}"),
                page_start=page_start,
                page_end=page_end,
                text=combined,
            )
        )
        current = []
        current_chars = 0

    for piece in pieces:
        part = piece[3]
        projected_size = current_chars + len(part) + (2 if current else 0)
        has_major_heading = bool(re.search(r"(?m)^#{1,2}\s+", part))
        if current and (
            projected_size > max_chars
            or (has_major_heading and current_chars >= max_chars // 3)
        ):
            flush()
        current.append(piece)
        current_chars += len(part) + (2 if len(current) > 1 else 0)
    flush()
    return sections


def sections_from_documents(
    documents: Iterable[Any],
    max_chars: int = MAX_SOURCE_SECTION_CHARS,
) -> list[SourceSection]:
    """Create source sections from Qdrant/LangChain documents.

    Child chunks sharing a parent are deduplicated because their metadata already
    contains the complete parent text.
    """
    sections: list[SourceSection] = []
    seen_parents: set[str] = set()
    for index, document in enumerate(documents):
        metadata = getattr(document, "metadata", {}) or {}
        parent_id = str(metadata.get("parent_id") or f"document-{index}")
        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)

        text = _clean_text(metadata.get("parent_content") or getattr(document, "page_content", ""))
        if not text:
            continue
        source_file = str(metadata.get("source_file") or "Uploaded material")
        try:
            page = int(metadata.get("page", index + 1))
        except (TypeError, ValueError):
            page = index + 1
        section_title = str(metadata.get("section") or f"Page {page}")
        for part_index, part in enumerate(_bounded_parts(text, max_chars), start=1):
            title = _heading(part, section_title)
            if part_index > 1:
                title = f"{title} (Part {part_index})"
            sections.append(
                SourceSection(
                    section_id=_section_id(
                        source_file,
                        page,
                        page,
                        part,
                        salt=f"{parent_id}-part-{part_index}",
                    ),
                    source_file=source_file,
                    title=title,
                    page_start=page,
                    page_end=page,
                    text=part,
                )
            )
    return sections


def openai_completion(api_key: str, model: str) -> Completion:
    """Return a small OpenAI adapter that is safe to invoke from worker threads."""

    def complete(messages: list[dict[str, str]], json_mode: bool, max_tokens: int) -> str:
        client = OpenAI(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    return complete


def _json_object(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Model response was not a JSON object")
    return data


class HierarchicalNotesPipeline:
    def __init__(
        self,
        completion: Completion,
        cache_dir: Path,
        model_name: str,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self.completion = completion
        self.cache_dir = Path(cache_dir)
        self.model_name = model_name
        self.max_workers = max(1, max_workers)

    def generate(
        self,
        sections: Sequence[SourceSection],
        subject: str,
        topic: str,
        focus: str | None = None,
        custom_instructions: str | None = None,
    ) -> dict[str, Any]:
        if not sections:
            raise ValueError("No source sections were available for notes generation")

        cards = self._analyze_sections(sections, subject, topic)
        outline = self._build_outline(cards, subject, topic, focus, custom_instructions)
        generated = self._generate_outline_sections(
            outline, cards, subject, topic, focus, custom_instructions
        )
        focus_areas = self._focus_areas(cards, generated)
        return {
            "markdown": self._render_markdown(generated, cards, topic),
            "notes": self._render_plan_notes(generated, cards),
            "focus_areas": focus_areas,
            "source_sections": len(sections),
            "evidence_cards": len(cards),
        }

    def _cache_path(self, section: SourceSection, subject: str, topic: str) -> Path:
        fingerprint = hashlib.sha256(
            (
                f"{PIPELINE_VERSION}|{self.model_name}|{subject}|{topic}|{section.section_id}|"
                f"{section.title}|{section.text}"
            ).encode("utf-8")
        ).hexdigest()
        return self.cache_dir / f"{fingerprint}.json"

    def _analyze_sections(
        self, sections: Sequence[SourceSection], subject: str, topic: str
    ) -> list[dict[str, Any]]:
        cards: list[dict[str, Any] | None] = [None] * len(sections)
        missing: list[tuple[int, SourceSection]] = []
        for index, section in enumerate(sections):
            cache_path = self._cache_path(section, subject, topic)
            try:
                cards[index] = json.loads(cache_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                missing.append((index, section))

        if missing:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(missing))) as executor:
                futures = {
                    executor.submit(self._analyze_one, section, subject, topic): (index, section)
                    for index, section in missing
                }
                for future in as_completed(futures):
                    index, section = futures[future]
                    try:
                        card = future.result()
                    except Exception as exc:
                        logger.warning("Evidence extraction failed for %s: %s", section.section_id, exc)
                        card = self._fallback_card(section)
                    else:
                        self._write_cache(self._cache_path(section, subject, topic), card)
                    cards[index] = card

        return [card for card in cards if card is not None]

    def _analyze_one(
        self, section: SourceSection, subject: str, topic: str
    ) -> dict[str, Any]:
        system = (
            "Extract a rich evidence card from one bounded source section. Use only the source. "
            "The summary must be a detailed 2-4 paragraph explanation that preserves the author's "
            "reasoning, definitions, relationships, assumptions, and conclusions rather than a terse "
            "abstract. Preserve every important equation, variable definition, algorithm step, worked "
            "example, diagnostic, limitation, and practical implication. Return JSON with keys summary "
            "(string), concepts, definitions, formulas, examples, and key_points (arrays of detailed "
            "strings). Do not cite pages and do not add outside knowledge."
        )
        user = (
            f"Subject: {subject}\nTopic: {topic}\nSource title: {section.title}\n"
            f"Source pages: {section.page_start}-{section.page_end}\n\n{section.text}"
        )
        data = _json_object(
            self.completion(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                True,
                1400,
            )
        )
        card = self._fallback_card(section)
        card["summary"] = _clean_text(data.get("summary"))[:MAX_CARD_FIELD_CHARS] or card["summary"]
        for key in ("concepts", "definitions", "formulas", "examples", "key_points"):
            values = data.get(key, [])
            if isinstance(values, list):
                card[key] = [
                    _clean_text(value)[:MAX_CARD_FIELD_CHARS]
                    for value in values[:12]
                    if _clean_text(value)
                ]
        return card

    @staticmethod
    def _fallback_card(section: SourceSection) -> dict[str, Any]:
        return {
            "section_id": section.section_id,
            "source_file": section.source_file,
            "title": section.title,
            "page_start": section.page_start,
            "page_end": section.page_end,
            "summary": section.text[:MAX_CARD_FIELD_CHARS],
            "concepts": [section.title],
            "definitions": [],
            "formulas": [],
            "examples": [],
            "key_points": [],
        }

    def _write_cache(self, path: Path, card: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            logger.warning("Could not cache evidence card %s: %s", path, exc)

    def _build_outline(
        self,
        cards: Sequence[dict[str, Any]],
        subject: str,
        topic: str,
        focus: str | None,
        custom_instructions: str | None,
    ) -> list[dict[str, Any]]:
        catalog = [
            {
                "section_id": card["section_id"],
                "title": card["title"],
                "pages": [card["page_start"], card["page_end"]],
                "concepts": card.get("concepts", [])[:8],
            }
            for card in cards
        ]
        system = (
            "Design a rigorous university-level study-notes outline from the source catalog. Return "
            "JSON with an outline array. Each item must have title, list_type ('ul' or 'ol'), and "
            "source_section_ids. Every source ID must be used at least once; a source ID may be reused "
            "when it supports more than one pedagogical section. Produce 5-8 coherent sections when "
            "the material supports them. Cover, where present: theoretical foundation and motivation; "
            "precise terminology; mathematical formulation with variable meanings; step-by-step method; "
            "hyperparameters and diagnostics; assumptions and failure modes; and worked examples or "
            "high-yield takeaways. Do not create a category unsupported by the catalog."
        )
        user = (
            f"Subject: {subject}\nTopic: {topic}\nFocus: {focus or 'Default'}\n"
            f"Teacher instructions: {custom_instructions or 'None'}\n\n"
            f"Source catalog:\n{json.dumps(catalog, ensure_ascii=False)}"
        )
        try:
            data = _json_object(
                self.completion(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    True,
                    1800,
                )
            )
            raw_outline = data.get("outline", [])
        except Exception as exc:
            logger.warning("Outline generation failed; using deterministic outline: %s", exc)
            raw_outline = []
        return self._validated_outline(raw_outline, cards)

    @staticmethod
    def _validated_outline(
        raw_outline: Any, cards: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        known = [str(card["section_id"]) for card in cards]
        known_set = set(known)
        covered: set[str] = set()
        outline: list[dict[str, Any]] = []
        if isinstance(raw_outline, list):
            for item in raw_outline:
                if not isinstance(item, dict):
                    continue
                ids = []
                item_ids: set[str] = set()
                for section_id in item.get("source_section_ids", []):
                    section_id = str(section_id)
                    if section_id in known_set and section_id not in item_ids:
                        ids.append(section_id)
                        item_ids.add(section_id)
                        covered.add(section_id)
                if ids:
                    outline.append(
                        {
                            "title": _clean_text(item.get("title")) or "Key Concepts",
                            "list_type": "ol" if item.get("list_type") == "ol" else "ul",
                            "source_section_ids": ids,
                        }
                    )

        missing = [section_id for section_id in known if section_id not in covered]
        if not outline:
            target_sections = max(
                math.ceil(len(known) / MAX_CARDS_PER_NOTE_SECTION),
                min(8, max(1, math.ceil(len(known) / 6))),
            )
            group_size = max(1, math.ceil(len(known) / target_sections))
            for index in range(0, len(known), group_size):
                group = known[index:index + group_size]
                first_card = next(card for card in cards if card["section_id"] == group[0])
                outline.append(
                    {
                        "title": first_card["title"],
                        "list_type": "ul",
                        "source_section_ids": group,
                    }
                )
            return outline

        for section_id in missing:
            target = next(
                (
                    item
                    for item in outline
                    if len(item["source_section_ids"]) < MAX_CARDS_PER_NOTE_SECTION
                ),
                None,
            )
            if target is None:
                target = {
                    "title": "Additional Source Material",
                    "list_type": "ul",
                    "source_section_ids": [],
                }
                outline.append(target)
            target["source_section_ids"].append(section_id)

        bounded_outline: list[dict[str, Any]] = []
        for item in outline:
            ids = item["source_section_ids"]
            for start in range(0, len(ids), MAX_CARDS_PER_NOTE_SECTION):
                part = ids[start:start + MAX_CARDS_PER_NOTE_SECTION]
                title = item["title"]
                if start:
                    title = f"{title} (continued)"
                bounded_outline.append({**item, "title": title, "source_section_ids": part})
        return bounded_outline

    def _generate_outline_sections(
        self,
        outline: Sequence[dict[str, Any]],
        cards: Sequence[dict[str, Any]],
        subject: str,
        topic: str,
        focus: str | None,
        custom_instructions: str | None,
    ) -> list[dict[str, Any]]:
        card_by_id = {card["section_id"]: card for card in cards}
        generated: list[dict[str, Any] | None] = [None] * len(outline)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(outline))) as executor:
            futures = {
                executor.submit(
                    self._generate_one_outline_section,
                    item,
                    card_by_id,
                    subject,
                    topic,
                    focus,
                    custom_instructions,
                ): index
                for index, item in enumerate(outline)
            }
            for future in as_completed(futures):
                index = futures[future]
                item = outline[index]
                try:
                    generated[index] = future.result()
                except Exception as exc:
                    logger.warning("Note section generation failed for %s: %s", item["title"], exc)
                    generated[index] = self._fallback_generated_section(item, card_by_id)
        return [item for item in generated if item is not None]

    def _generate_one_outline_section(
        self,
        outline_item: dict[str, Any],
        card_by_id: dict[str, dict[str, Any]],
        subject: str,
        topic: str,
        focus: str | None,
        custom_instructions: str | None,
    ) -> dict[str, Any]:
        allowed_ids = list(outline_item["source_section_ids"])
        evidence = [card_by_id[section_id] for section_id in allowed_ids]
        system = (
            "Write one polished, rigorous university-level study-note section using only the evidence "
            "cards. Teach the material: explain intuition, precise terminology, causal relationships, "
            "and why each step or equation matters. Preserve formulas, define every variable, and work "
            "through numerical examples in a clear sequence when the evidence contains them. Include "
            "assumptions, diagnostics, limitations, and common mistakes where relevant. Avoid shallow "
            "one-sentence summaries, repetitive wording, and raw extraction-style labels. Return JSON "
            "with title, list_type ('ul' or 'ol'), and 4-6 detailed, multi-sentence items. Each item must "
            "be an object with text and source_section_ids. Use only IDs provided in the evidence. "
            "Use $...$ for inline mathematics and $$...$$ for display mathematics. Do not put source "
            "names, page numbers, evidence IDs, citations, or other metadata in the title or item text."
        )
        user = (
            f"Subject: {subject}\nTopic: {topic}\nRequested section: {outline_item['title']}\n"
            f"Focus: {focus or 'Default'}\nTeacher instructions: {custom_instructions or 'None'}\n\n"
            f"Evidence cards:\n{json.dumps(evidence, ensure_ascii=False)}"
        )
        data = _json_object(
            self.completion(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                True,
                2200,
            )
        )
        items: list[dict[str, Any]] = []
        for raw_item in data.get("items", []):
            if isinstance(raw_item, str):
                text = _clean_text(raw_item)
                ids = allowed_ids[:1]
            elif isinstance(raw_item, dict):
                text = _clean_text(raw_item.get("text"))
                ids = [
                    str(section_id)
                    for section_id in raw_item.get("source_section_ids", [])
                    if str(section_id) in allowed_ids
                ]
            else:
                continue
            text = strip_display_metadata(text)
            if text:
                items.append({"text": text, "source_section_ids": ids or allowed_ids[:1]})
        if not items:
            return self._fallback_generated_section(outline_item, card_by_id)
        return {
            "title": strip_display_metadata(_clean_text(data.get("title"))) or outline_item["title"],
            "list_type": "ol" if data.get("list_type") == "ol" else outline_item["list_type"],
            "items": items,
        }

    @staticmethod
    def _fallback_generated_section(
        outline_item: dict[str, Any], card_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        items = []
        for section_id in outline_item["source_section_ids"]:
            card = card_by_id[section_id]
            text = strip_display_metadata(card.get("summary") or card.get("title"))
            if text:
                items.append({"text": text, "source_section_ids": [section_id]})
        return {
            "title": outline_item["title"],
            "list_type": outline_item["list_type"],
            "items": items,
        }

    def _render_markdown(
        self,
        generated: Sequence[dict[str, Any]],
        cards: Sequence[dict[str, Any]],
        topic: str,
    ) -> str:
        lines = [f"# {topic}", ""]
        for section in generated:
            lines.extend([f"## {section['title']}", ""])
            for index, item in enumerate(section["items"], start=1):
                prefix = f"{index}." if section["list_type"] == "ol" else "-"
                lines.append(f"{prefix} {item['text']}")
            lines.append("")

        labels = [section["title"][:42] for section in generated[:8]]
        lines.extend(
            [
                f"![Infographic - {topic} learning map](infographic.png)",
                "",
                "[INFOGRAPHIC_SCRIPT]",
                "```python",
                "fig, ax = plt.subplots(figsize=(9, 5))",
                f"labels = {labels!r}",
                "y = np.arange(len(labels))",
                "ax.barh(y, np.arange(len(labels), 0, -1), color='#4f46e5')",
                "ax.set_yticks(y, labels=labels)",
                "ax.invert_yaxis()",
                f"ax.set_title({('Learning map: ' + topic)!r})",
                "ax.set_xlabel('Recommended learning sequence')",
                "fig.tight_layout()",
                "plt.savefig(OUTPUT_PATH, bbox_inches='tight', dpi=150)",
                "plt.close(fig)",
                "```",
            ]
        )
        return "\n".join(lines).strip()

    def _render_plan_notes(
        self, generated: Sequence[dict[str, Any]], cards: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        card_by_id = {card["section_id"]: card for card in cards}
        notes = []
        for index, section in enumerate(generated, start=1):
            all_ids = [
                source_id
                for item in section["items"]
                for source_id in item["source_section_ids"]
                if source_id in card_by_id
            ]
            page = min((card_by_id[source_id]["page_start"] for source_id in all_ids), default=1)
            notes.append(
                {
                    "t": f"{index}. {section['title']}",
                    "p": page,
                    "k": section["list_type"],
                    "items": [item["text"] for item in section["items"]],
                }
            )
        return notes

    @staticmethod
    def _focus_areas(
        cards: Sequence[dict[str, Any]], generated: Sequence[dict[str, Any]]
    ) -> list[str]:
        candidates = [
            str(concept).strip()
            for card in cards
            for concept in card.get("concepts", [])
            if str(concept).strip()
        ]
        candidates.extend(section["title"] for section in generated)
        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
            if len(unique) == 8:
                break
        return unique
