import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI
from django.utils import timezone

from config import get_settings
from utils.logger import get_logger
from app.services.document_processor import extract_text_from_pdf, chunk_text, process_file
from app.services.notes_pipeline import (
    HierarchicalNotesPipeline,
    openai_completion,
    sections_from_pages,
    strip_display_metadata,
)
from app.services.vector_store import add_documents
from voice_tutor import models

logger = get_logger(__name__)

# Standard fallback focus areas for ML / Analytics if not parsed
DEFAULT_FOCUS_AREAS = [
    "Theoretical Foundation",
    "Mathematical Formulation",
    "Lloyd's Algorithm",
    "Initialization & k-means++",
    "Hyperparameter Tuning (Elbow Method)",
    "Assumptions & Failure Modes",
    "Evaluation Metrics"
]

DEFAULT_SAMPLE_NOTES = [
    {
        "t": "1. Introduction & Theoretical Foundation",
        "p": 2,
        "k": "ul",
        "items": [
            "K-Means is a foundational unsupervised machine learning algorithm designed to partition n unlabelled observations into k distinct, non-overlapping clusters.",
            "Each observation is mapped to the cluster whose centroid serves as the prototype, creating natural geometric decision boundaries across continuous feature space.",
            "The algorithm aims to discover latent grouping structures such that intra-cluster variance is minimized while inter-cluster variance is maximized.",
            "Widely utilized in customer segmentation, anomaly detection, image compression, and high-dimensional exploratory data analysis."
        ]
    },
    {
        "t": "2. Mathematical Formulation & Optimization Objective",
        "p": 3,
        "k": "ul",
        "items": [
            "Objective Function: Minimizes the total Within-Cluster Sum of Squares (WCSS), commonly referred to as Inertia: $J = \\sum_{i=1}^{k} \\sum_{x \\in S_i} \\|x - \\mu_i\\|^2$, where $S_i$ represents the set of observations in cluster $i$ and $\\mu_i$ is its centroid vector.",
            "Distance Metric: Standard formulation relies on Euclidean distance in $d$-dimensional continuous space: $d(x, \\mu_i) = \\sqrt{\\sum_{j=1}^{d} (x_j - \\mu_{ij})^2}$.",
            "Non-Convex Optimization: Because WCSS is non-convex over discrete assignment variables, finding the exact global minimum is an NP-hard problem; heuristics find local minima.",
            "Feature Scaling Sensitivity: Unnormalized features with large numerical ranges disproportionately distort Euclidean distances, necessitating pre-scaling (e.g. Z-score Standardization)."
        ]
    },
    {
        "t": "3. Step-by-Step Algorithmic Process (Lloyd's Heuristic)",
        "p": 4,
        "k": "ol",
        "items": [
            "Initialization: Specify hyperparameter $k$ and place $k$ initial centroids in the feature space (randomly, via Forgy selection, or through $k$-means++ probabilistic seeding).",
            "Cluster Assignment: Calculate distances from each data point to all $k$ centroids; assign every point to its closest centroid: $c^{(j)} = \\arg\\min_i \\|x_j - \\mu_i\\|^2$.",
            "Centroid Update: Recalculate each centroid coordinate as the arithmetic mean of all data points currently assigned to that cluster: $\\mu_i = \\frac{1}{|S_i|} \\sum_{x \\in S_i} x$.",
            "Iterative Loop: Repeat Assignment and Update steps sequentially until centroid drift falls below tolerance threshold $\\epsilon$ or memberships cease changing.",
            "Convergence Guarantee: Lloyd's algorithm is guaranteed to converge monotonically to a local minimum in a finite number of iterations because the partition space is finite."
        ]
    },
    {
        "t": "4. Hyperparameter Tuning & Model Diagnostics",
        "p": 5,
        "k": "ul",
        "items": [
            "The Elbow Method: Plot hyperparameter $k$ against total WCSS (Inertia). The optimal $k$ corresponds to the inflection point where additional clusters yield diminishing returns.",
            "Silhouette Score Analysis: Evaluates cohesion versus separation. For point $i$: $s(i) = \\frac{b(i) - a(i)}{\\max(a(i), b(i))}$, where $a(i)$ is mean intra-cluster distance and $b(i)$ is mean distance to the nearest neighboring cluster ($-1 \\le s(i) \\le +1$).",
            "Davies-Bouldin Index: Evaluates similarity between each cluster and its most similar counterpart; lower scores indicate superior partition compactness and separation.",
            "Gap Statistic: Compares the total within-cluster dispersion against an expected null reference distribution to objectively determine optimal cluster count."
        ]
    },
    {
        "t": "5. Core Terminology & Granular Definitions",
        "p": 6,
        "k": "ul",
        "items": [
            "Centroid: The mathematical center of mass (feature-wise arithmetic mean) of all data points belonging to a specific cluster.",
            "Inertia (WCSS): The aggregate squared Euclidean distance between every data point and its assigned centroid prototype: $\\sum_{x \\in S_i} \\|x - \\mu_i\\|^2$.",
            "k-means++: An advanced initialization algorithm that chooses starting centroids with probability proportional to their squared distance from existing centers, achieving an $O(\\log k)$ competitive ratio.",
            "Voronoi Tessellation: The geometric partitioning of the feature space into convex polygonal regions corresponding to the nearest centroid.",
            "Hard Clustering: Partition paradigm where each point strictly belongs to exactly one cluster with 100% certainty (in contrast to soft/fuzzy clustering)."
        ]
    },
    {
        "t": "6. Assumptions, Critical Gotchas & Failure Modes",
        "p": 7,
        "k": "ul",
        "items": [
            "Cluster Geometry Assumption: Assumes clusters are spherical, isotropic, and similarly sized; fails on concentric circles, elongated ribbons, or arbitrary manifold geometries (where DBSCAN or Spectral Clustering is preferred).",
            "High Sensitivity to Outliers: Extremes exert disproportionate leverage on the arithmetic mean because squared Euclidean distances penalize errors quadratically.",
            "Local Minima Susceptibility: Prone to becoming trapped in sub-optimal local minima; mitigated in practice by running multiple random initializations ($n\\_init = 10$).",
            "Hard vs. Soft Assignment: Provides binary partition assignments without probabilistic uncertainty estimates, unlike Gaussian Mixture Models (GMMs)."
        ]
    }
]

DEFAULT_SAMPLE_QUESTIONS = [
    {
        "id": 1,
        "d": "Easy",
        "p": 3,
        "f": "Algorithm",
        "q": "In K-means, what happens during the assignment step?",
        "o": [
            "Each point is assigned to its nearest centroid",
            "Each centroid moves to the mean of all its points",
            "The value of k is chosen automatically",
            "Outliers are removed from the data"
        ],
        "a": 0,
        "e": "The assignment step labels every point with its closest centroid. Centroid coordinates only update in the subsequent step."
    },
    {
        "id": 2,
        "d": "Medium",
        "p": 3,
        "f": "Objective",
        "q": "What loss function does K-means explicitly try to minimise?",
        "o": [
            "The distance between distant centroids",
            "The within-cluster sum of squared distances (inertia)",
            "The number of clusters in the feature space",
            "The total number of loop iterations"
        ],
        "a": 1,
        "e": "This objective metric is called inertia or WCSS. Lower inertia signifies tighter, more compact clusters."
    },
    {
        "id": 3,
        "d": "Easy",
        "p": 3,
        "f": "Convergence",
        "q": "When does the iterative K-means loop stop?",
        "o": [
            "After exactly k iterations",
            "When every point becomes its own separate cluster",
            "When cluster assignments no longer change between iterations",
            "When inertia reaches absolute zero"
        ],
        "a": 2,
        "e": "Once no point switches clusters, centroid coordinates remain static and the algorithm has formally converged."
    },
    {
        "id": 4,
        "d": "Medium",
        "p": 4,
        "f": "Initialization",
        "q": "Why is K-means++ initialization generally preferred over naive random selection?",
        "o": [
            "It guarantees finding the global optimum every time",
            "It spreads starting centroids apart proportionally to distance, avoiding poor local minima",
            "It completely removes the requirement to pick k",
            "It forces convergence in exactly one step"
        ],
        "a": 1,
        "e": "K-means++ selects initial centroids with probability proportional to squared distance from existing ones, avoiding centroid clumping."
    },
    {
        "id": 5,
        "d": "Hard",
        "p": 6,
        "f": "Pitfalls",
        "q": "Two separate runs on identical data yield different clusters. What is the root cause?",
        "o": [
            "K-means dynamically changed the value of k",
            "The input dataset mutated between executions",
            "Different initial random centroid placements trapped the run in different local minima",
            "Centroid coordinates cannot be recalculated"
        ],
        "a": 2,
        "e": "Because K-means solves non-convex optimization, random starting points often land in distinct local minima."
    },
    {
        "id": 6,
        "d": "Medium",
        "p": 5,
        "f": "Choosing k",
        "q": "On an elbow plot (k vs. inertia), how is an optimal k identified?",
        "o": [
            "The value of k that gives lowest possible inertia",
            "The largest value of k evaluated",
            "The inflection point where additional clusters yield diminishing returns in inertia drop",
            "The point where inertia equals one"
        ],
        "a": 2,
        "e": "Past the elbow inflection point, adding extra clusters provides negligible improvement in compactness."
    }
]


def get_openai_client() -> Optional[OpenAI]:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        return None
    try:
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        return None


def extract_pdf_data(file_path: str) -> Dict[str, Any]:
    """
    Extracts text and page numbers from a PDF file using pymupdf4llm.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"File path does not exist: {file_path}")
        return {"pages": [], "page_count": 0, "full_text": ""}

    pages = extract_text_from_pdf(str(path))
    page_count = len(pages)
    full_text = "\n\n".join([f"--- Page {p.get('page_number', i+1)} ---\n" + p.get("text", "") for i, p in enumerate(pages)])

    return {
        "pages": pages,
        "page_count": page_count,
        "full_text": full_text
    }


def repair_math_string(text: str) -> str:
    """
    Sanitizes mathematical text and repairs control-character corruptions from JSON decoding:
    - \\x0c (Form Feed from single \\f) -> \\frac or \\f
    - \\x08 (Backspace from single \\b) -> \\bullet, \\bigg, \\bar, etc.
    - \\t (Tab from single \\t) -> \\text, \\theta, \\tau, etc.
    - \\r (Carriage Return from single \\r) -> \\rho
    - Mangled unicode or control escapes (e.g. \\x03B1 -> \\alpha)
    - Unicode sigma/bullet to standard LaTeX
    - Broken escaped dollar signs (\\$$ or \\$)
    - Wraps raw equations without delimiters into $...$
    """
    if not isinstance(text, str):
        return text

    # 1. Revert corrupted control characters caused by single-backslash JSON escape sequences
    text = text.replace('\x0crac', r'\frac')
    text = text.replace('\x0c', r'\f')
    text = text.replace('\x08ullet', r'\bullet')
    text = text.replace('\x08igg', r'\bigg')
    text = text.replace('\x08ar', r'\bar')
    text = text.replace('\x08eta', r'\beta')
    text = text.replace('\x08', r'\b')
    text = text.replace('\text', r'\text')
    text = text.replace('\times', r'\times')
    text = text.replace('\theta', r'\theta')
    text = text.replace('\tau', r'\tau')
    text = text.replace('\rho', r'\rho')
    text = text.replace('\x03B1', r'\alpha')
    text = text.replace('\x07lpha', r'\alpha')

    # 2. Convert common unicode mathematical symbols to standard LaTeX
    text = text.replace('Σ', r'\sum')
    text = text.replace('•', r'\bullet')
    text = text.replace('Math input errorMath input error', r'$z = w \bullet x + b = 0$')
    text = text.replace('Math input error', '')

    # 3. Fix escaped dollar signs that prevent MathJax delimiters from triggering
    text = text.replace(r'\$$', '$$').replace(r'\$', '$')

    # 4. If line contains an equation with backslash commands without $ delimiters
    if '\\' in text and '$' not in text:
        pattern = r'(?:(?<=:\s)|(?<=\s\s)|(?<=^))([A-Za-z0-9_\(\)\{\}\^\\,\s*\/+-]+=[^;\.]*\\[a-zA-Z]+[^;\.]*)'
        text = re.sub(pattern, r'$\1$', text)

    return text


def normalize_notes_math(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ensures any LaTeX equations in note items are properly wrapped in LaTeX $...$ delimiters
    and sanitized against JSON escape corruption so that MathJax renders them cleanly.
    """
    for sec in notes:
        items = sec.get("items", [])
        fixed_items = []
        for itm in items:
            fixed_items.append(repair_math_string(str(itm)))
        sec["items"] = fixed_items
    return notes


def _pages_from_context_text(context_text: str) -> List[Dict[str, Any]]:
    """Recover page records from the legacy marked context stored in sessions."""
    matches = list(re.finditer(r"(?m)^--- Page (\d+) ---\s*$", context_text))
    if not matches:
        return [{"page_number": 1, "text": context_text}] if context_text.strip() else []

    pages: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(context_text)
        pages.append({
            "page_number": int(match.group(1)),
            "text": context_text[match.end():end].strip(),
        })
    return pages


def generate_notes_with_ai(
    subject: str,
    topic: str,
    context_text: str = "",
    pages: Optional[List[Dict[str, Any]]] = None,
    source_file: str = "Uploaded material.pdf",
) -> Dict[str, Any]:
    """Generate Plan Mode notes without sending the whole PDF in one prompt."""
    settings = get_settings()
    source_pages = pages or _pages_from_context_text(context_text)
    sections = sections_from_pages(source_pages, source_file=source_file)
    if not settings.OPENAI_API_KEY or not sections:
        logger.info("OpenAI client unavailable or empty source. Using standard notes structure.")
        return {
            "notes": normalize_notes_math(DEFAULT_SAMPLE_NOTES),
            "focus_areas": DEFAULT_FOCUS_AREAS,
        }

    try:
        pipeline = HierarchicalNotesPipeline(
            completion=openai_completion(
                settings.OPENAI_API_KEY,
                settings.LLM_MODEL or "gpt-4o-mini",
            ),
            cache_dir=settings.GENERATED_DIR / "_notes_analysis",
            model_name=settings.LLM_MODEL or "gpt-4o-mini",
        )
        result = pipeline.generate(sections, subject, topic)
        return {
            "notes": normalize_notes_math(result["notes"]),
            "focus_areas": result["focus_areas"] or DEFAULT_FOCUS_AREAS,
        }
    except Exception as exc:
        logger.error("Hierarchical Plan Mode notes generation failed: %s", exc)
        return {
            "notes": normalize_notes_math(DEFAULT_SAMPLE_NOTES),
            "focus_areas": DEFAULT_FOCUS_AREAS,
        }


def generate_course_from_brief(
    subject: str,
    topic: str,
    description: str,
) -> Dict[str, Any]:
    """Create a structured course plan when the professor has no source document."""
    topic = str(topic or "").strip()
    description = str(description or "").strip()
    if not topic:
        raise ValueError("Add a topic before asking Sage to prepare the course.")
    if len(description) < 20:
        raise ValueError("Describe what you want students to learn in at least 20 characters.")

    client = get_openai_client()
    if not client:
        raise ValueError("Sage is unavailable because the OpenAI API key is not configured.")

    system_prompt = (
        "You are Sage, an expert university instructional designer. Build comprehensive study "
        "notes from a professor's topic and teaching brief. You may use established domain "
        "knowledge, but do not invent statistics, research findings, citations, source files, or "
        "page references. Organize the course in a logical teaching sequence from foundations to "
        "application. Include definitions, mechanisms or processes, a worked example where useful, "
        "common misconceptions or pitfalls, and practical applications. Use $...$ for inline math "
        "and $$...$$ for display math. Return only valid JSON with this shape: "
        '{"notes":[{"t":"numbered section title","k":"ul or ol",'
        '"items":["detailed teaching note"]}],"focus_areas":["short concept label"]}. '
        "Create 5-8 sections with 3-6 substantive, multi-sentence items per section and 5-8 focus areas."
    )
    payload = {
        "subject": subject,
        "topic": topic,
        "professor_brief": description[:6000],
    }

    try:
        settings = get_settings()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=6000,
        )
        data = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        logger.error("Description-based course generation failed: %s", exc)
        raise ValueError("Sage could not prepare this course right now. Please try again.") from exc

    notes: List[Dict[str, Any]] = []
    for index, section in enumerate(data.get("notes", [])):
        if not isinstance(section, dict):
            continue
        items = [
            repair_math_string(strip_display_metadata(item))
            for item in section.get("items", [])
            if strip_display_metadata(item)
        ]
        if not items:
            continue
        notes.append({
            "t": strip_display_metadata(section.get("t") or f"{index + 1}. Key Concepts"),
            "p": 1,
            "k": section.get("k") if section.get("k") in {"ul", "ol", "chips"} else "ul",
            "items": items,
        })

    if not notes:
        raise ValueError("Sage did not return a usable course plan. Please add more detail and try again.")

    focus_areas = [
        strip_display_metadata(area)
        for area in data.get("focus_areas", [])
        if strip_display_metadata(area)
    ][:8]
    if not focus_areas:
        focus_areas = [strip_display_metadata(note["t"]) for note in notes[:6]]

    return {"notes": notes, "focus_areas": focus_areas}


def build_course_context(topic: str, description: str, notes: List[Dict[str, Any]]) -> str:
    """Build reusable context for questions and later refinements from a topic brief."""
    parts = [f"Topic: {topic}", f"Professor's brief: {description}", "Generated course notes:"]
    for section in notes:
        parts.append(str(section.get("t", "Key Concepts")))
        parts.extend(f"- {item}" for item in section.get("items", []))
    return "\n\n".join(parts)


def refine_note_section_with_ai(
    subject: str,
    topic: str,
    section: Dict[str, Any],
    instruction: str,
    pages: Optional[List[Dict[str, Any]]] = None,
    conversation: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Revise one Plan Mode note section through a grounded Sage conversation."""
    instruction = str(instruction or "").strip()
    if not instruction:
        raise ValueError("Tell Sage what you want to change in this section.")

    current_section = {
        "t": strip_display_metadata(section.get("t", "Key Concepts")),
        "p": section.get("p", 1),
        "k": section.get("k", "ul") if section.get("k") in {"ul", "ol", "chips"} else "ul",
        "items": [
            strip_display_metadata(item)
            for item in section.get("items", [])
            if strip_display_metadata(item)
        ],
    }
    if not current_section["items"]:
        raise ValueError("This note section has no content to revise.")

    client = get_openai_client()
    if not client:
        raise ValueError("Sage is unavailable because the OpenAI API key is not configured.")

    try:
        target_page = int(current_section.get("p") or 1)
    except (TypeError, ValueError):
        target_page = 1

    source_parts: List[str] = []
    source_chars = 0
    for index, page in enumerate(pages or []):
        try:
            page_number = int(page.get("page_number", index + 1))
        except (TypeError, ValueError):
            page_number = index + 1
        if page_number < max(1, target_page - 1) or page_number > target_page + 3:
            continue
        page_text = str(page.get("text", "")).strip()
        if not page_text:
            continue
        remaining = 14_000 - source_chars
        if remaining <= 0:
            break
        excerpt = page_text[:remaining]
        source_parts.append(f"Page {page_number}:\n{excerpt}")
        source_chars += len(excerpt)

    history = []
    for message in (conversation or [])[-6:]:
        role = message.get("role")
        content = strip_display_metadata(message.get("content", ""))
        if role in {"user", "assistant"} and content:
            history.append({"role": role, "content": content[:1500]})

    system_prompt = (
        "You are Sage, an expert teaching assistant collaborating with a university professor. "
        "Revise only the supplied note section according to the professor's latest instruction. "
        "Preserve correct material that was not targeted by the request. Improve clarity, depth, "
        "pedagogy, equations, or examples when asked, while staying grounded in the current section "
        "and source excerpt. Do not mention source files, page numbers, citations, evidence IDs, or "
        "metadata in the revised notes. Use $...$ for inline mathematics and $$...$$ for display "
        "mathematics. Return only valid JSON with this shape: "
        '{"message":"brief description of the revision","section":{"t":"section title",'
        '"k":"ul or ol","items":["detailed note item"]}}. '
        "Keep 3-7 substantive items unless the professor explicitly requests another length."
    )
    user_payload = {
        "subject": subject,
        "topic": topic,
        "current_section": current_section,
        "source_excerpt": "\n\n".join(source_parts) or "No additional source excerpt available.",
        "professor_instruction": instruction,
    }

    settings = get_settings()
    response = client.chat.completions.create(
        model=settings.LLM_MODEL or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.25,
        max_tokens=2500,
    )
    data = json.loads(response.choices[0].message.content or "{}")
    revised = data.get("section") if isinstance(data.get("section"), dict) else {}
    revised_items = revised.get("items") if isinstance(revised.get("items"), list) else []
    clean_items = [
        repair_math_string(strip_display_metadata(item))
        for item in revised_items
        if strip_display_metadata(item)
    ]
    if not clean_items:
        raise ValueError("Sage did not return a usable section revision.")

    revised_section = {
        "t": strip_display_metadata(revised.get("t") or current_section["t"]),
        "p": current_section["p"],
        "k": revised.get("k") if revised.get("k") in {"ul", "ol", "chips"} else current_section["k"],
        "items": clean_items,
    }
    message = strip_display_metadata(data.get("message") or "I updated this section.")
    return {"message": message, "section": revised_section}


def generate_questions_with_ai(
    subject: str,
    topic: str,
    context_text: str,
    count: int = 6,
    difficulty: str = "Mixed",
    focus_areas: Optional[List[str]] = None,
    source_has_pages: bool = True,
    professor_brief: str = "",
) -> List[Dict[str, Any]]:
    """
    Generates multiple-choice questions matching the professor's filters.
    """
    client = get_openai_client()
    if not client or not context_text.strip():
        if not source_has_pages:
            raise ValueError(
                "Sage could not generate grounded questions from this course description. "
                "Please try again; no unrelated sample questions were substituted."
            )
        logger.info("OpenAI client unavailable or empty context. Using default question set.")
        qs = [dict(q) for q in DEFAULT_SAMPLE_QUESTIONS]
        return qs[:count]

    focus_str = ", ".join(focus_areas) if focus_areas else "all core concepts"
    context_snippet = context_text[:14000]
    professor_brief = str(professor_brief or "").strip()[:6000]
    source_instruction = (
        "Every question MUST cite the exact page number ('p') where the concept or answer is found.\n"
        if source_has_pages else
        "The source is a professor-written topic brief, not a paginated document. "
        "Treat the professor's brief as the authoritative scope. Every question and correct answer "
        "MUST directly assess a concept, example, constraint, or learning outcome explicitly requested "
        "in that brief. Do not introduce adjacent subject knowledge merely because it relates to the "
        "topic. Set 'p' to 1 as an internal value and do not mention pages or citations in the question text.\n"
    )

    system_prompt = (
        "You are an expert exam author creating multiple-choice questions for university students.\n"
        f"Generate exactly {count} multiple-choice questions from the provided material.\n"
        f"Difficulty guideline: '{difficulty}'.\n"
        f"Focus specifically on these conceptual areas: {focus_str}.\n"
        f"{source_instruction}"
        "Before returning the JSON, verify internally that each question can be answered from the supplied material and that no question falls outside its stated teaching scope.\n"
        "Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "id": 1,\n'
        '      "d": "Easy", // Easy, Medium, or Hard\n'
        '      "p": 3, // page number\n'
        '      "f": "Algorithm", // Focus area tag matching the topic\n'
        '      "q": "Clear, rigorous question stem",\n'
        '      "o": ["Option A", "Option B", "Option C", "Option D"],\n'
        '      "a": 0, // integer 0, 1, 2, or 3 indicating the correct option\n'
        '      "e": "Concise pedagogical explanation of why this answer is correct"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    try:
        settings = get_settings()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Subject: {subject}\nTopic: {topic}\n\n"
                        + (
                            f"Professor's brief (authoritative scope):\n{professor_brief}\n\n"
                            if professor_brief else ""
                        )
                        + f"Study material derived from the source:\n{context_snippet}"
                    ),
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        data = json.loads(response.choices[0].message.content)
        questions = data.get("questions")
        if not questions:
            for val in data.values():
                if isinstance(val, list) and val and isinstance(val[0], dict) and ("q" in val[0] or "question" in val[0]):
                    questions = val
                    break

        if questions:
            for i, q in enumerate(questions):
                q["id"] = i + 1
            return questions
        if not source_has_pages:
            raise ValueError(
                "Sage returned no usable questions for this course description. "
                "Please try generating them again."
            )
        return DEFAULT_SAMPLE_QUESTIONS[:count]
    except Exception as e:
        logger.error(f"Error in generate_questions_with_ai: {e}")
        if not source_has_pages:
            raise ValueError(
                "Sage could not generate grounded questions from this course description. "
                "Please try again; no unrelated sample questions were substituted."
            ) from e
        return DEFAULT_SAMPLE_QUESTIONS[:count]


def swap_question_with_ai(
    subject: str,
    topic: str,
    context_text: str,
    focus: str,
    difficulty: str,
    existing_questions: List[Dict[str, Any]],
    source_has_pages: bool = True,
    professor_brief: str = "",
) -> Dict[str, Any]:
    """
    Generates a single replacement question for a specific focus concept.
    """
    client = get_openai_client()
    existing_texts = [q.get("q", "") for q in existing_questions]

    if not client or not context_text.strip():
        if not source_has_pages:
            raise ValueError(
                "Sage could not create a grounded replacement from this course description. "
                "The existing question was left unchanged."
            )
        # Pick a fallback question not currently in use
        for q in DEFAULT_SAMPLE_QUESTIONS:
            if q.get("q") not in existing_texts and (q.get("f") == focus or not focus):
                return dict(q)
        # Or return a variant
        return {
            "id": len(existing_questions) + 1,
            "d": difficulty or "Medium",
            "p": 4,
            "f": focus or "Algorithm",
            "q": f"Regarding {focus or topic}, which statement correctly highlights its fundamental operational principle?",
            "o": [
                "It systematically refines cluster boundaries until variance stabilization",
                "It eliminates all need for initial baseline assumptions",
                "It guarantees identical clustering regardless of initial seed coordinates",
                "It replaces distance metrics with random distributions"
            ],
            "a": 0,
            "e": f"The core principle of {focus or topic} relies on iterative variance minimization across assignments."
        }

    system_prompt = (
        f"You are an exam author creating ONE replacement multiple-choice question for university students.\n"
        f"The question must focus strictly on: '{focus}'.\n"
        f"Difficulty: '{difficulty}'.\n"
        f"Avoid repeating any of these existing questions: {json.dumps(existing_texts[:5])}.\n"
        + (
            "The professor's brief is the authoritative scope. The replacement question and correct "
            "answer must directly assess something explicitly requested in that brief. Do not introduce "
            "adjacent subject knowledge. Set 'p' to 1 and do not mention pages.\n"
            if not source_has_pages else ""
        )
        + "Return ONLY a JSON object:\n"
        "{\n"
        '  "question": {\n'
        '    "d": "' + difficulty + '",\n'
        '    "p": 3,\n'
        '    "f": "' + focus + '",\n'
        '    "q": "New question text",\n'
        '    "o": ["Option A", "Option B", "Option C", "Option D"],\n'
        '    "a": 0,\n'
        '    "e": "Explanation text"\n'
        "  }\n"
        "}"
    )

    try:
        settings = get_settings()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Subject: {subject}\nTopic: {topic}\n\n"
                        + (
                            f"Professor's brief (authoritative scope):\n{str(professor_brief or '').strip()[:6000]}\n\n"
                            if professor_brief else ""
                        )
                        + f"Context:\n{context_text[:8000]}"
                    ),
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.4
        )
        data = json.loads(response.choices[0].message.content)
        q = data.get("question", {})
        q["id"] = len(existing_questions) + 1
        return q
    except Exception as e:
        logger.error(f"Error in swap_question_with_ai: {e}")
        if not source_has_pages:
            raise ValueError(
                "Sage could not create a grounded replacement from this course description. "
                "The existing question was left unchanged."
            ) from e
        return {
            "id": len(existing_questions) + 1,
            "d": difficulty or "Medium",
            "p": 3,
            "f": focus or "Algorithm",
            "q": f"In {topic}, how does {focus} impact overall convergence behavior?",
            "o": [
                "It dictates the rate and trajectory of objective optimization",
                "It has no measurable effect on computational stability",
                "It solely determines the total feature dimensionality",
                "It converts unsupervised clustering into supervised regression"
            ],
            "a": 0,
            "e": f"{focus} directly influences how effectively and swiftly the solution stabilizes."
        }


def normalize_oral_defense_config(
    game_config: Optional[Dict[str, Any]],
    topic_name: str,
) -> Optional[Dict[str, Any]]:
    """Validate and normalize the professor-authored Oral Defense template."""
    if not game_config or not game_config.get("enabled"):
        return None

    raw_levels = game_config.get("levels")
    if not isinstance(raw_levels, list) or not 1 <= len(raw_levels) <= 5:
        raise ValueError("Choose between 1 and 5 Oral Defense levels.")

    levels = []
    for index, raw_level in enumerate(raw_levels, 1):
        if not isinstance(raw_level, dict):
            raise ValueError(f"Level {index} is incomplete.")

        title = strip_display_metadata(raw_level.get("title") or f"Level {index}")[:120]
        topic = strip_display_metadata(raw_level.get("topic") or topic_name)[:180]
        question = repair_math_string(
            strip_display_metadata(raw_level.get("question"))
        )[:2000]
        knowledge_base = repair_math_string(
            strip_display_metadata(raw_level.get("knowledge_base"))
        )[:6000]
        if not question:
            raise ValueError(f"Add an oral-defense question for Level {index}.")
        if len(knowledge_base) < 20:
            raise ValueError(
                f"Add a clearer knowledge base for Level {index} so Sage can evaluate students fairly."
            )

        levels.append({
            "level": index,
            "title": title,
            "topic": topic,
            "question": question,
            "knowledge_base": knowledge_base,
        })

    game_title = strip_display_metadata(
        game_config.get("title") or f"{topic_name} Oral Defense"
    )[:160]
    return {
        "enabled": True,
        "template": "oral_defense",
        "title": game_title,
        "boss_name": "Professor Algor",
        "boss_title": f"Guardian of {topic_name}"[:120],
        "levels": levels,
    }


def publish_plan_to_database(
    subject_name: str,
    batch_name_or_code: str,
    topic_name: str,
    notes_list: List[Dict[str, Any]],
    questions_list: List[Dict[str, Any]],
    prof_profile: Optional[models.UserProfile] = None,
    file_path: str = "",
    course_name: str = "",
    game_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Commits the curated plan into the official database tables:
    - SubjectMaster
    - TopicMaster
    - SummaryNotes
    - Assignment
    - AssignmentQuestion & QuestionOption
    - Links to ProfessorAllocation for the chosen Batch
    """
    display_name = course_name.strip() or topic_name
    normalized_game = normalize_oral_defense_config(game_config, topic_name)

    # 1. Subject Master
    subject_obj = models.SubjectMaster.objects.filter(name__iexact=subject_name).first()
    if not subject_obj:
        subject_obj = models.SubjectMaster.objects.filter(code__iexact=subject_name).first()
    if not subject_obj:
        # Fallback to first existing subject or create
        subject_obj = models.SubjectMaster.objects.first()

    # 2. Topic Master
    topic_code = f"TOP_{uuid.uuid4().hex[:6].upper()}"
    topic_obj = models.TopicMaster.objects.filter(name__iexact=topic_name, subject=subject_obj).first()
    if not topic_obj:
        topic_obj = models.TopicMaster.objects.create(
            name=topic_name,
            code=topic_code,
            subject=subject_obj,
            description=f"Course topic on {topic_name} planned with Sage AI."
        )

    # 3. Store Raw Material if file provided
    if file_path and Path(file_path).exists():
        try:
            models.RawMaterial.objects.create(
                topic=topic_obj,
                title=f"{topic_name} Source Material",
                material_type="pdf",
                file_url=file_path
            )
        except Exception as e:
            logger.warning(f"Could not link RawMaterial: {e}")

    # 4. Summary Notes
    # Convert structured notes into readable markdown for storage
    notes_md_parts = [f"# {display_name} — Comprehensive Study Notes\n"]
    if display_name.casefold() != topic_name.casefold():
        notes_md_parts.append(f"\n**Topic:** {topic_name}\n")
    for sec in notes_list:
        t = sec.get("t", "Key Concepts")
        items = sec.get("items", [])
        k = sec.get("k", "ul")
        notes_md_parts.append(f"\n## {t}\n")
        if k == "ol":
            for i, itm in enumerate(items, 1):
                clean_itm = repair_math_string(str(itm))
                notes_md_parts.append(f"{i}. {clean_itm}\n")
        elif k == "chips":
            for itm in items:
                clean_itm = repair_math_string(str(itm))
                notes_md_parts.append(f"- **{clean_itm}**\n")
        else:
            for itm in items:
                clean_itm = repair_math_string(str(itm))
                notes_md_parts.append(f"- {clean_itm}\n")

    formatted_content = "\n".join(notes_md_parts)
    models.SummaryNotes.objects.create(
        topic=topic_obj,
        title=f"{display_name} — Study Notes",
        content=formatted_content
    )

    # 5. Assignment
    assign_code = f"A_{uuid.uuid4().hex[:6].upper()}"
    assignment_obj = models.Assignment.objects.create(
        topic=topic_obj,
        code=assign_code,
        title=f"{display_name}: Comprehensive Assignment",
        description=f"Assessment for {display_name} on {topic_name}, covering core concepts and problem solving.",
        difficulty="intermediate",
        expected_duration=max(15, len(questions_list) * 3),
        source_pdf_path=file_path,
        oral_defense_enabled=bool(normalized_game),
        oral_defense_config=normalized_game,
    )

    # 6. Questions & Options
    opt_letters = ["a", "b", "c", "d"]
    for idx, q_data in enumerate(questions_list):
        q_code = f"Q{idx+1}_{assign_code}"
        correct_idx = q_data.get("a", 0)
        correct_letter = opt_letters[correct_idx] if 0 <= correct_idx < len(opt_letters) else "a"

        visualization = q_data.get("visualization")
        visualization_approved = bool(
            q_data.get("visualization_approved") and isinstance(visualization, dict)
        )

        q_obj = models.AssignmentQuestion.objects.create(
            assignment=assignment_obj,
            code=q_code,
            content=q_data.get("q", ""),
            question_type="mcq",
            correct_answer=correct_letter,
            concept_tags=json.dumps([q_data.get("f", "General")]),
            difficulty=q_data.get("d", "Medium").lower(),
            order=idx + 1,
            visualization_data=visualization if visualization_approved else None,
            visualization_approved=visualization_approved,
            visualization_approved_at=timezone.now() if visualization_approved else None,
            visualization_approved_by=prof_profile if visualization_approved else None,
        )

        options = q_data.get("o", [])
        for opt_idx, opt_text in enumerate(options):
            l = opt_letters[opt_idx] if opt_idx < len(opt_letters) else f"opt_{opt_idx}"
            models.QuestionOption.objects.create(
                question=q_obj,
                option_letter=l,
                option_text=opt_text
            )

    # 7. Link Allocation if Batch specified
    batch_obj = models.Batch.objects.filter(code__iexact=batch_name_or_code).first()
    if not batch_obj:
        batch_obj = models.Batch.objects.filter(name__iexact=batch_name_or_code).first()

    if batch_obj:
        alloc = None
        if prof_profile:
            alloc = models.ProfessorAllocation.objects.filter(batch=batch_obj, subject=subject_obj, professor=prof_profile).first()
        if not alloc:
            alloc = models.ProfessorAllocation.objects.filter(batch=batch_obj, subject=subject_obj).first()
        if alloc:
            assignment_obj.professor_allocation = alloc
            assignment_obj.save()

    return {
        "success": True,
        "topic_id": topic_obj.id,
        "topic_code": topic_obj.code,
        "assignment_code": assignment_obj.code,
        "title": assignment_obj.title,
        "question_count": len(questions_list),
        "game_enabled": bool(normalized_game),
        "game_levels": len(normalized_game["levels"]) if normalized_game else 0,
    }
