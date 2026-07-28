"""
Content generation service.

Uses Gemini LLM with RAG context to generate educational content:
study notes, structured assignments, and AI video explanations.
"""

import asyncio
import logging
import os
import time
import urllib.request
from pathlib import Path
import json
import re

from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
import numpy as np

from google import genai
from google.genai import types
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.services.vector_store import retrieve_documents, retrieve_all_documents, _sanitize_name
from config import get_settings

logger = logging.getLogger(__name__)


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """
    Create and return a ChatOpenAI instance.

    Args:
        temperature: Sampling temperature for generation.

    Returns:
        Configured LLM instance.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        temperature=temperature,
        api_key=settings.OPENAI_API_KEY,
    )


def _build_context(subject: str, topic: str) -> str:
    """
    Retrieve and concatenate relevant documents to build LLM context (fallback synchronous).

    Args:
        subject: The subject to search within.
        topic: The topic to search for.

    Returns:
        Concatenated text from the most relevant document chunks.
    """
    query = f"Key concepts and content about {topic} in {subject}"
    docs = retrieve_documents(subject=subject, topic=topic, query=query, k=10)

    if not docs:
        logger.warning("No context documents found for %s/%s", subject, topic)
        return ""

    context = "\n\n---\n\n".join(doc.page_content for doc in docs)
    logger.info(
        "Built context from %d documents for %s/%s (%d chars)",
        len(docs),
        subject,
        topic,
        len(context),
    )
    return context


def _build_full_context(subject: str, topic: str) -> str:
    """
    Retrieve and concatenate all document chunks for a specific topic, sorted by index.
    """
    docs = retrieve_all_documents(subject=subject, topic=topic)
    if not docs:
        logger.warning("No context documents found for all-documents retrieve on %s/%s", subject, topic)
        return ""
    context = "\n\n---\n\n".join(doc.page_content for doc in docs)
    logger.info(
        "Built full context from %d documents for %s/%s (%d chars)",
        len(docs),
        subject,
        topic,
        len(context),
    )
    return context


from app.services.retrieval_pipeline import retrieve_hybrid

async def _build_context_hybrid(subject: str, topic: str, query: str, top_n_child: int = 8) -> str:
    """
    Retrieve and format documents using the hybrid RAG retrieval pipeline.
    """
    docs = await retrieve_hybrid(subject=subject, topic=topic, query=query, top_n_child=top_n_child)
    
    if not docs:
        logger.warning("No hybrid context documents found for %s/%s with query '%s'", subject, topic, query)
        return ""
        
    context_parts = []
    for idx, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source_file", "Unknown Source")
        page = doc.metadata.get("page", "Unknown Page")
        section = doc.metadata.get("section", "Unknown Section")
        content = doc.page_content.strip()
        context_parts.append(
            f"--- Document chunk {idx} ---\n"
            f"Source: {source}\n"
            f"Page: {page}\n"
            f"Section: {section}\n"
            f"Content:\n{content}"
        )
    
    context = "\n\n".join(context_parts)
    logger.info(
        "Built hybrid context from %d parent documents for %s/%s (%d chars)",
        len(docs),
        subject,
        topic,
        len(context),
    )
    return context


def get_off_topic_warning(subject: str, topic: str, custom_instructions: str) -> str:
    """
    Format a standardized warning message for off-topic custom guidelines.
    """
    return (
        "⚠️ **Off-Topic Guidelines Warning**\n\n"
        f"The custom guidelines you provided appear to be unrelated to the academic topic **{topic}** in **{subject}**.\n\n"
        f"**Your guidelines:** \"{custom_instructions}\"\n\n"
        "Please provide instructions that are relevant to the subject and topic to generate appropriate learning materials."
    )


async def check_custom_prompt_off_topic(subject: str, topic: str, custom_prompt: str | None) -> bool:
    """
    Classify whether a custom instruction/prompt is off-topic for a given subject and topic.
    """
    if not custom_prompt or not custom_prompt.strip():
        return False
        
    llm = get_llm(temperature=0.0)
    
    system_prompt = (
        "You are an academic guardrail system. Your job is to classify if the user's custom prompt/guidelines "
        "are off-topic or unrelated to the specified academic subject and topic.\n\n"
        "Academic Subject: {subject}\n"
        "Academic Topic: {topic}\n\n"
        "Criteria for ON-TOPIC (return 'ON_TOPIC'):\n"
        "- The guidelines request specific pedagogical formats, styles, or questions related to the topic (e.g. 'focus on math formulas', 'explain with bullet points', 'make it simple', 'explain ANOVA assumptions').\n"
        "- The guidelines ask academic questions or subtopics directly relevant to the topic or subject.\n\n"
        "Criteria for OFF-TOPIC (return 'OFF_TOPIC'):\n"
        "- The guidelines are completely unrelated to the subject and topic (e.g. asking who will win the next FIFA World Cup, asking for a cookie recipe, general news/politics/sports trivia, writing unrelated python programs).\n\n"
        "Respond with EXACTLY one word: 'OFF_TOPIC' or 'ON_TOPIC'. Do not add any punctuation, explanation, or other text."
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "User guidelines to classify:\n\"\"\"\n{custom_prompt}\n\"\"\"")
    ])
    
    chain = prompt | llm
    try:
        response = await chain.ainvoke({
            "subject": subject,
            "topic": topic,
            "custom_prompt": custom_prompt
        })
        result = response.content.strip().upper()
        logger.info("Off-topic check for custom prompt '%s' returned: %s", custom_prompt, result)
        return "OFF_TOPIC" in result
    except Exception as e:
        logger.error("Failed to perform off-topic classification: %s. Defaulting to ON_TOPIC.", str(e))
        return False


async def generate_notes(
    subject: str,
    topic: str,
    focus: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """
    Generate detailed study notes from uploaded material using hybrid retrieval.

    Args:
        subject: The subject name.
        topic: The topic name.
        focus: Custom focus style (e.g. Deep Dive, Critique, Summary & Bullets).
        custom_instructions: Specific directions from the teacher.

    Returns:
        Generated notes in markdown format with page-level citations.
    """
    if await check_custom_prompt_off_topic(subject, topic, custom_instructions):
        return get_off_topic_warning(subject, topic, custom_instructions)

    query = f"Comprehensive concepts, definitions, formulas, and examples about {topic} in {subject}"
    context = await _build_context_hybrid(subject, topic, query, top_n_child=12)
    
    if not context:
        return (
            f"No study material found for **{topic}** in **{subject}**. "
            "Please ask your teacher to upload relevant documents first."
        )

    llm = get_llm(temperature=0.3)
    
    system_prompt = (
        "You are an expert educational content creator. Your task is to "
        "create detailed, well-structured study notes from ONLY the provided "
        "study material. Do NOT add information beyond what is in the material. "
        "If a concept or topic is not mentioned in the material, do not generate notes for it.\n\n"
        "Structure your notes exactly as follows:\n"
        "1. **Introduction** — A brief overview and introduction to the topic\n"
        "2. **Key Concepts** — Detailed explanation of each major concept\n"
        "3. **Formulae** — Key equations, mathematical representations, and definitions of terms\n"
        "4. **Examples** — Relevant step-by-step examples from the material\n"
        "5. **Summary** — A concise concluding summary\n\n"
        "**CRITICAL REQUIREMENT - CITATION-AWARE GENERATION**:\n"
        "Every definition, fact, formula, example, or statement you make MUST include an inline citation "
        "referencing its Source, Page, and Section immediately at the end of the sentence or block in this exact format:\n"
        "`[Source: <source_file>, Page: <page>, Section: <section>]`\n\n"
        "For example:\n"
        "- The linearity assumption requires a linear relationship between variables. [Source: DataScience.pdf, Page 14, Section: Assumptions]\n"
        "- ANOVA tests if the means of three or more groups are different. [Source: stats_book.pdf, Page 22, Section: ANOVA Overview]\n\n"
        "Format everything in clean, readable Markdown. Use headings, "
        "bullet points, bold text, and code blocks where appropriate.\n\n"
        "**CRITICAL REQUIREMENT - INFOGRAPHIC**:\n"
        "You MUST include exactly one beautiful educational infographic/chart relevant to the topic. "
        "To do this, embed a python script block at the very end of your notes in this format:\n\n"
        "[INFOGRAPHIC_SCRIPT]\n"
        "```python\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "# Generate professional chart relevant to the notes (e.g. F-distribution, bar chart, comparison curves)\n"
        "# Use matplotlib or numpy. Do NOT import scipy.\n"
        "fig, ax = plt.subplots(figsize=(6, 4))\n"
        "# Drawing logic...\n"
        "plt.savefig(OUTPUT_PATH, bbox_inches='tight', dpi=150)\n"
        "plt.close()\n"
        "```\n\n"
        "In your markdown notes content, reference the infographic where it fits best using: "
        "`![Infographic - Topic Visual Description](infographic.png)`.\n"
        "Our backend will run the script and serve the image file in place of the placeholder."
    )

    # Inject focus and custom parameters
    if (focus and focus != "Default") or custom_instructions:
        system_prompt += "\n\n**Special Focus Guidelines for Notes Generation**:\n"
        if focus and focus != "Default":
            system_prompt += f"- Focus Style: Focus on producing a **{focus}** set of notes. "
            if focus == "Deep Dive":
                system_prompt += "Explain all key concepts in high granularity, incorporating theoretical details, formulas, and in-depth analyses present in the source text.\n"
            elif focus == "Critique":
                system_prompt += "Critically analyze the methodology, limitations, core assumptions, and potential counter-arguments or alternate viewpoints mentioned in the source.\n"
            elif focus == "Summary & Bullets":
                system_prompt += "Ensure the output is highly concise and structured, relying heavily on clean bullet points, summary tables, bold terminology, and minimal fluff.\n"
        if custom_instructions:
            system_prompt += f"- Custom Teacher Focus Instructions: {custom_instructions}\n"

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        (
            "human",
            "Subject: {subject}\n"
            "Topic: {topic}\n\n"
            "Study Material Chunks:\n\n{context}\n\n"
            "Please create comprehensive study notes with page-level citations from the above material.",
        ),
    ])
    chain = prompt | llm
    logger.info("Generating notes for %s/%s using hybrid context (%d chars)", subject, topic, len(context))
    response = await chain.ainvoke({"subject": subject, "topic": topic, "context": context})
    return response.content


async def generate_assignment(
    subject: str,
    topic: str,
    focus: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """
    Generate 5 separate assignments, each containing MCQ, Short Answer, Long Answer, Case Study, and Numerical sections,
    covering Easy, Medium, and Hard difficulty levels with citation traceability.

    Args:
        subject: The subject name.
        topic: The topic name.
        focus: Custom focus style (e.g. Conceptual, Numerical, Case Studies).
        custom_instructions: Specific directions from the teacher.

    Returns:
        Generated assignment list in markdown format.
    """
    if await check_custom_prompt_off_topic(subject, topic, custom_instructions):
        return get_off_topic_warning(subject, topic, custom_instructions)

    query = f"Assessable definitions, formulas, numerical examples, and concept explanations about {topic} in {subject}"
    context = await _build_context_hybrid(subject, topic, query, top_n_child=10)

    if not context:
        return (
            f"No study material found for **{topic}** in **{subject}**. "
            "Please ask your teacher to upload relevant documents first."
        )

    system_prompt = (
        "You are an expert educator creating a series of assignments based on ONLY "
        "the provided study material. Do NOT use knowledge outside the material.\n\n"
        "Generate exactly 5 separate assignments, numbered 1 to 5. "
        "Each assignment must cover different concepts and contain a mix of:\n"
        "- Multiple Choice Questions (MCQs)\n"
        "- Short Answer Questions\n"
        "- Long Answer Questions\n"
        "- Case Studies / Scenario Questions\n"
        "- Numerical Problems (where applicable)\n\n"
        "For each question, specify:\n"
        "1. The Difficulty Level (Easy, Medium, Hard)\n"
        "2. Marks allocation\n"
        "3. A traceability citation tag pointing to the exact source chunk, page, and section: "
        "`[Source: <source_file>, Page: <page>, Section: <section>]`\n\n"
        "Provide a clear 'Answer Key' section at the end of each individual assignment.\n\n"
        "Structure each assignment as follows:\n"
        "### Assignment [Number]: [Topic Sub-Focus/Title]\n"
        "#### Questions\n"
        "1. [Question text] (Difficulty: [Easy/Medium/Hard], [Marks]) - Citation: [Source: <source_file>, Page: <page>, Section: <section>]\n"
        "   If MCQ, provide A) B) C) D) options.\n"
        "...\n"
        "#### Answer Key for Assignment [Number]\n"
        "1. [Detailed answer / Correct MCQ option] - Citation: [Source: <source_file>, Page: <page>, Section: <section>]\n"
        "...\n\n"
        "Format the entire output in clean, readable Markdown."
    )

    # Inject focus and custom parameters
    if (focus and focus != "Default") or custom_instructions:
        system_prompt += "\n\n**Special Focus Guidelines for Assignment Generation**:\n"
        if focus and focus != "Default":
            system_prompt += f"- Question Type/Focus: Focus heavily on **{focus}** questions. "
            if focus == "Conceptual":
                system_prompt += "Design questions that test students' conceptual understanding, definitions, logical deductions, and theoretical knowledge.\n"
            elif focus == "Numerical & Calculation":
                system_prompt += "Design questions that test mathematical, statistical, or quantitative calculation skills, utilizing formulas and numeric problem-solving from the material.\n"
            elif focus == "Case Studies":
                system_prompt += "Design scenario-based or case-study style questions that present real-world situations and ask students to apply information from the text to solve or analyze them.\n"
        if custom_instructions:
            system_prompt += f"- Custom Teacher Focus Instructions: {custom_instructions}\n"

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        (
            "human",
            "Subject: {subject}\n"
            "Topic: {topic}\n\n"
            "Study Material Chunks:\n\n{context}\n\n"
            "Please create the 5 assignments with source citation traceability tags from the above material.",
        ),
    ])

    llm = get_llm(temperature=0.5)
    chain = prompt | llm

    logger.info("Generating assignments for %s/%s using hybrid context", subject, topic)
    response = await chain.ainvoke(
        {"subject": subject, "topic": topic, "context": context}
    )
    return response.content


class OptionSchema(BaseModel):
    option_letter: str = Field(description="The letter of the option, e.g., 'A', 'B', 'C', 'D'")
    option_text: str = Field(description="The text content of the option")

class QuestionSchema(BaseModel):
    content: str = Field(description="The question text")
    question_type: str = Field(description="Type of question: must be 'mcq'")
    options: list[OptionSchema] = Field(..., description="List of exactly 4 options for the MCQ")
    correct_answer: str = Field(description="The letter of the correct option (A, B, C, or D)")
    concept_tags: list[str] = Field(description="List of concepts tested in this question")
    difficulty: str = Field(description="Difficulty level: 'beginner', 'intermediate', 'advanced'")
    order: int = Field(description="The question order number")

class AssignmentSchema(BaseModel):
    title: str = Field(description="A catchy, descriptive title for the assignment")
    description: str = Field(description="A brief description of what this assignment covers")
    difficulty: str = Field(description="Overall difficulty: 'beginner', 'intermediate', 'advanced'")
    expected_duration: int = Field(description="Expected time to complete in minutes")
    questions: list[QuestionSchema] = Field(description="List of questions for this assignment")

async def generate_structured_assignment(
    subject: str,
    topic: str,
    context: str,
    custom_instructions: str = None,
) -> AssignmentSchema:
    """
    Generate a highly structured JSON assignment object from the provided PDF text chunks.
    """
    llm = get_llm(temperature=0.3)
    structured_llm = llm.with_structured_output(AssignmentSchema)
    
    system_prompt = (
        "You are an expert AI educator. Based ONLY on the provided study material chunks, "
        "generate a comprehensive assignment that tests the student's understanding of the text.\n"
        "You MUST generate ONLY Multiple Choice Questions (MCQs).\n"
        "For each question, assign a difficulty level (beginner, intermediate, advanced).\n"
        "You MUST provide exactly 4 options for EVERY question, and set the correct_answer to the correct letter (A, B, C, or D).\n"
        "Ensure all questions are directly answerable using the provided context."
    )
    if custom_instructions:
        system_prompt += f"\n\nCRITICAL PROFESSOR INSTRUCTIONS:\n{custom_instructions}"
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Subject: {subject}\nTopic: {topic}\n\nContext Material:\n{context}\n\nPlease generate the assignment JSON."),
    ])
    
    chain = prompt | structured_llm
    logger.info("Generating structured JSON assignment for %s/%s", subject, topic)
    result = await chain.ainvoke({"subject": subject, "topic": topic, "context": context})
    return result


def download_placeholder_video(dest_path: Path):
    """Download or copy a local valid MP4 to use as a placeholder."""
    local_options = [
        "/Users/tatparramawat/Downloads/Plain Black Video.mp4",
        "/Users/tatparramawat/Downloads/vecteezy_hd-bars-and-tone_2018013.mp4",
        "/Users/tatparramawat/Downloads/25102-347958100_small.mp4"
    ]
    
    import shutil
    for path_str in local_options:
        src_path = Path(path_str)
        if src_path.exists() and src_path.stat().st_size > 0:
            logger.info("Found local MP4 at %s. Copying to placeholder.", path_str)
            try:
                shutil.copy(str(src_path), str(dest_path))
                logger.info("Placeholder video copied successfully from local source.")
                return
            except Exception as e:
                logger.error("Failed to copy local MP4 from %s: %s", path_str, str(e))

    url = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
    logger.info("No local MP4s found. Downloading placeholder video from %s to %s", url, dest_path)
    try:
        urllib.request.urlretrieve(url, str(dest_path))
        logger.info("Placeholder video downloaded successfully.")
    except Exception as e:
        dest_path.write_bytes(b"")


def draw_professor_avatar(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, mouth_open: bool = False) -> None:
    """
    Draw a stylized cartoon professor face using vector shapes in Pillow.
    """
    # 1. Suit / Shoulders
    draw.polygon(
        [
            (center_x - 120, center_y + 180),
            (center_x + 120, center_y + 180),
            (center_x + 80, center_y + 90),
            (center_x - 80, center_y + 90)
        ],
        fill="#1e3a8a" # Dark Blue suit
    )
    # Tie/Collar details
    draw.polygon(
        [
            (center_x - 20, center_y + 90),
            (center_x + 20, center_y + 90),
            (center_x, center_y + 130)
        ],
        fill="#ffffff" # White shirt V-neck
    )
    draw.polygon(
        [
            (center_x - 8, center_y + 110),
            (center_x + 8, center_y + 110),
            (center_x, center_y + 170)
        ],
        fill="#dc2626" # Red tie
    )
    
    # 2. Neck
    draw.rectangle(
        [center_x - 25, center_y + 60, center_x + 25, center_y + 95],
        fill="#ffdbac" # Skin tone
    )
    
    # 3. Face
    draw.ellipse(
        [center_x - 70, center_y - 70, center_x + 70, center_y + 70],
        fill="#ffdbac"
    )
    
    # 4. Hair (Brown curls/receding hair outline)
    draw.chord(
        [center_x - 75, center_y - 75, center_x + 75, center_y - 20],
        start=180, end=360,
        fill="#78350f" # Dark brown hair
    )
    
    # 5. Glasses
    # Left Lens
    draw.ellipse(
        [center_x - 45, center_y - 25, center_x - 5, center_y + 15],
        outline="#475569", width=4
    )
    # Right Lens
    draw.ellipse(
        [center_x + 5, center_y - 25, center_x + 45, center_y + 15],
        outline="#475569", width=4
    )
    # Glasses Bridge
    draw.line(
        [center_x - 5, center_y - 5, center_x + 5, center_y - 5],
        fill="#475569", width=4
    )
    
    # 6. Eyes (Simple pupils)
    draw.ellipse([center_x - 28, center_y - 8, center_x - 22, center_y - 2], fill="#000000")
    draw.ellipse([center_x + 22, center_y - 8, center_x + 28, center_y - 2], fill="#000000")
    
    # 7. Eyebrows
    draw.line([center_x - 40, center_y - 32, center_x - 10, center_y - 28], fill="#78350f", width=4)
    draw.line([center_x + 10, center_y - 28, center_x + 40, center_y - 32], fill="#78350f", width=4)

    # 8. Graduation Cap (Professor hat)
    draw.polygon(
        [
            (center_x, center_y - 120),       # Top
            (center_x + 100, center_y - 90),   # Right
            (center_x, center_y - 60),       # Bottom
            (center_x - 100, center_y - 90)    # Left
        ],
        fill="#0f172a" # Black cap
    )
    # Cap band
    draw.rectangle(
        [center_x - 45, center_y - 80, center_x + 45, center_y - 65],
        fill="#1e293b"
    )
    # Tassel
    draw.line([center_x, center_y - 90, center_x - 70, center_y - 80], fill="#eab308", width=3) # Gold string
    draw.ellipse([center_x - 75, center_y - 82, center_x - 65, center_y - 72], fill="#eab308")
    
    # 9. Mouth (Dynamic closed/open)
    if mouth_open:
        # Open mouth (vertical oval)
        draw.ellipse(
            [center_x - 15, center_y + 15, center_x + 15, center_y + 40],
            fill="#be123c",
            outline="#991b1b",
            width=2
        )
    else:
        # Closed mouth (smiling line)
        draw.arc(
            [center_x - 20, center_y + 10, center_x + 20, center_y + 30],
            start=20, end=160,
            fill="#991b1b",
            width=4
        )


def render_slide_image(
    title: str,
    bullet_points: list[str],
    output_path: Path | None = None,
    draw_avatar: bool = True,
    mouth_open: bool = False
) -> Image.Image | None:
    """
    Render a beautiful dark-themed presentation slide image using Pillow.
    Left 60% contains text; right 40% is kept blank or holds the avatar.
    """
    logger.info("Rendering slide: '%s' (avatar=%s, mouth_open=%s)", title, draw_avatar, mouth_open)
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), color="#0b0f19")
    draw = ImageDraw.Draw(image)
    
    # Left accent indicator line
    draw.rectangle([0, 0, 15, height], fill="#3b82f6") # Blue accent line
    
    # Font setup
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 60)
        text_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 36)
        avatar_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 28)
    except Exception:
        title_font = ImageFont.load_default()
        text_font = ImageFont.load_default()
        avatar_font = ImageFont.load_default()
        
    # Draw slide title
    draw.text((80, 100), title, font=title_font, fill="#ffffff")
    
    # Draw bullet points (with simple text wrapping)
    y = 250
    for bullet in bullet_points:
        words = bullet.split()
        lines = []
        current_line = []
        for word in words:
            test_line = " ".join(current_line + [word])
            if hasattr(draw, "textlength"):
                line_w = draw.textlength(test_line, font=text_font)
            else:
                line_w = len(test_line) * 20 # crude estimate
                
            if line_w < 1000:
                current_line.append(word)
            else:
                lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))
            
        # Draw bullet point dot
        draw.ellipse([80, y + 12, 92, y + 24], fill="#10b981") # Green dot
        
        # Draw wrapped lines
        for line in lines:
            draw.text((120, y), line, font=text_font, fill="#d1d5db")
            y += 50
        y += 40 # extra margin between bullets
        
    # Draw professor avatar if enabled
    if draw_avatar:
        avatar_x = 1580
        avatar_y = 700
        draw_professor_avatar(draw, avatar_x, avatar_y, mouth_open=mouth_open)
        draw.text((avatar_x - 85, avatar_y + 200), "Professor AI", font=avatar_font, fill="#a855f7")
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(output_path))
        logger.info("Saved slide image to %s", output_path)
        return None
    else:
        return image


def upload_slide_to_public_host(image_path: Path) -> str:
    """
    Upload slide PNG to tmpfiles.org anonymously and retrieve direct download link.
    """
    logger.info("Uploading slide image %s to tmpfiles.org...", image_path.name)
    url = "https://tmpfiles.org/api/v1/upload"
    
    import uuid
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    
    with open(image_path, "rb") as f:
        file_bytes = f.read()
        
    parts = []
    parts.append(f"--{boundary}".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{image_path.name}"'.encode())
    parts.append(b"Content-Type: image/png")
    parts.append(b"")
    parts.append(file_bytes)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    
    body = b"\r\n".join(parts)
    
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())
            
        viewer_url = res_data.get("data", {}).get("url")
        if not viewer_url:
            raise ValueError(f"Upload failed, empty response: {res_data}")
            
        direct_url = viewer_url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/")
        logger.info("Slide uploaded successfully! Direct URL: %s", direct_url)
        return direct_url
    except Exception as e:
        logger.error("Failed to upload slide image to public host: %s", str(e))
        raise


def generate_local_video(slides: list[dict], output_file: Path) -> None:
    """
    Generate slide presentation video locally using Pillow, gTTS, and MoviePy.
    Includes an animated 2D mouth-flap talking professor avatar in the corner.
    """
    logger.info("Starting local presentation video compilation with animated avatar...")
    slide_clips = []
    
    temp_dir = output_file.parent / f"temp_{int(time.time())}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        from moviepy import VideoClip
        
        for idx, slide in enumerate(slides):
            title = slide.get("title", f"Slide {idx+1}")
            bullets = slide.get("bullets", [])
            narration = slide.get("narration", "")
            
            # 1. Render slide closed and open mouth frames in-memory
            img_closed = render_slide_image(title, bullets, output_path=None, draw_avatar=True, mouth_open=False)
            img_open = render_slide_image(title, bullets, output_path=None, draw_avatar=True, mouth_open=True)
            
            frame_closed = np.array(img_closed)
            frame_open = np.array(img_open)
            
            # 2. Synthesize audio
            audio_path = temp_dir / f"voice_{idx}.mp3"
            clean_narration = re.sub(r"\[Source:\s*[^\]]+\]", "", narration)
            clean_narration = re.sub(r"#+\s*", "", clean_narration)
            clean_narration = clean_narration.replace("**", "").replace("*", "").strip()
            
            try:
                logger.info("Generating audio for slide %d: '%s'", idx, clean_narration[:60])
                tts = gTTS(text=clean_narration, lang="en", tld="com")
                tts.save(str(audio_path))
            except Exception as tts_err:
                logger.error("gTTS synthesis failed: %s. Generating 5-second silence fallback.", str(tts_err))
                import base64
                silence_b64 = (
                    "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGFtZTMuOThyAhIAAAAAAAAAA"
                    "FVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
                    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
                )
                with open(audio_path, "wb") as sf:
                    sf.write(base64.b64decode(silence_b64))
            
            # 3. Create audio clip and animate avatar mouth flap
            audio_clip = AudioFileClip(str(audio_path))
            
            # Define make_frame to swap between open and closed mouth frames to animate speaking
            def make_frame(t, closed=frame_closed, open_frame=frame_open):
                # Alternate open/closed mouth every 0.15 seconds
                if int(t / 0.15) % 2 == 0:
                    return open_frame
                else:
                    return closed
            
            slide_clip = VideoClip(make_frame, duration=audio_clip.duration)
            slide_clip = slide_clip.with_audio(audio_clip)
            
            slide_clips.append(slide_clip)
            
        # 4. Concatenate slides
        logger.info("Concatenating %d clips into final video...", len(slide_clips))
        final_video = concatenate_videoclips(slide_clips, method="compose")
        final_video.write_videofile(
            str(output_file),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None
        )
        logger.info("Successfully compiled video locally to %s", output_file)
        
    finally:
        # Close all clips to release file handles
        for clip in slide_clips:
            try:
                clip.close()
            except Exception:
                pass
        # Clean up temporary directory
        import shutil
        try:
            shutil.rmtree(str(temp_dir))
        except Exception as te:
            logger.warning("Failed to clean up temp dir %s: %s", temp_dir, str(te))


async def generate_video_explanation(
    subject: str,
    topic: str,
    focus: str | None = None,
    custom_instructions: str | None = None,
) -> tuple[str | None, str]:
    """
    Generate an educational slide presentation video and script using RAG and local/HeyGen compilers.

    Args:
        subject: The subject name.
        topic: The topic name.
        focus: Optional style focus for slides/script.
        custom_instructions: Optional custom guidelines for the explainer video.

    Returns:
        A tuple of (video_url, script_content).
    """
    if await check_custom_prompt_off_topic(subject, topic, custom_instructions):
        return None, get_off_topic_warning(subject, topic, custom_instructions)

    query = f"Step-by-step concept explanations, analogies, progression, and practical walkthroughs about {topic} in {subject}"
    context = await _build_context_hybrid(subject, topic, query, top_n_child=8)
    
    if not context:
        raise ValueError(f"No study material found for {topic} in {subject}.")

    # Generate the structured slides JSON
    system_prompt = (
        "You are an expert professor creating an educational slide presentation from ONLY the provided "
        "study material. Do NOT add outside information.\n\n"
        "You MUST output a valid JSON object containing a list of exactly 4 slides. "
        "The JSON must have a single key 'slides' which maps to an array of objects. "
        "Each object must contain the following keys exactly:\n"
        "1. 'title' — The title of the slide (concise and clear)\n"
        "2. 'bullets' — An array of 3 to 4 string bullet points presenting key concepts, definitions, or formulas\n"
        "3. 'narration' — A detailed spoken professor script explaining the slide in a friendly class tone. "
        "You must include inline source citations at the end of statements inside the narration: "
        "`[Source: <file>, Page: <page>, Section: <section>]`.\n\n"
        "Example JSON output format:\n"
        "{{\n"
        "  \"slides\": [\n"
        "    {{\n"
        "      \"title\": \"What is ANOVA?\",\n"
        "      \"bullets\": [\n"
        "        \"ANOVA stands for Analysis of Variance\",\n"
        "        \"Used to compare three or more group means simultaneously\"\n"
        "      ],\n"
        "      \"narration\": \"Welcome class! Today we are discussing ANOVA. ANOVA stands for... [Source: stats.pdf, Page 2, Section: Intro]\"\n"
        "    }}\n"
        "  ]\n"
        "}}\n\n"
        "Output ONLY raw JSON. Do not include markdown code block tags like ```json or ```. Just raw valid JSON text."
    )

    # Inject focus and custom parameters
    if (focus and focus != "Default") or custom_instructions:
        system_prompt += "\n\n**Special Focus Guidelines for Video Generation**:\n"
        if focus and focus != "Default":
            system_prompt += f"- Video Focus/Style: Focus on producing a **{focus}** explanation video. "
            if focus == "Conceptual":
                system_prompt += "Design slides and narration that explain concepts, definitions, logical deductions, and theoretical knowledge.\n"
            elif focus == "Numerical & Calculation":
                system_prompt += "Design slides and narration that focus on formula applications, step-by-step math steps, and calculations.\n"
            elif focus == "Case Studies":
                system_prompt += "Design slides and narration around real-world scenarios or case studies related to the topic.\n"
        if custom_instructions:
            system_prompt += f"- Custom Video Guidelines: {custom_instructions}\n"

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Subject: {subject}\nTopic: {topic}\nContext: {context}\n\nGenerate the structured slides JSON:")
    ])
    
    llm = get_llm(temperature=0.4)
    
    try:
        logger.info("Generating presentation slides JSON for %s/%s using RAG context", subject, topic)
        chain = prompt | llm
        response = await chain.ainvoke({"subject": subject, "topic": topic, "context": context})
        
        raw_content = response.content.strip()
        if raw_content.startswith("```"):
            lines = raw_content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            raw_content = "\n".join(lines).strip()
            
        try:
            slides_data = json.loads(raw_content)
        except Exception:
            match = re.search(r"(\{.*\})", raw_content, re.DOTALL)
            if match:
                slides_data = json.loads(match.group(1))
            else:
                raise
                
        slides = slides_data.get("slides", [])
        if not slides:
            raise ValueError("No slides found in generated JSON.")
            
    except Exception as e:
        logger.error("Failed to generate structured slides JSON: %s. Using default fallback slides.", str(e))
        slides = [
            {
                "title": f"Introduction to {topic}",
                "bullets": ["Grounded context loading failed.", "Please retry generation."],
                "narration": f"Welcome to the video guide on {topic} in the subject of {subject}. "
                             f"This slide presentation serves as an introduction to our topic."
            }
        ]

    # Compile the final script markdown from the slides data
    script_parts = []
    for idx, s in enumerate(slides, start=1):
        bullets_md = "\n".join(f"* {b}" for b in s.get("bullets", []))
        script_parts.append(
            f"## Slide {idx}: {s.get('title')}\n\n"
            f"{bullets_md}\n\n"
            f"**Narration:**\n{s.get('narration')}"
        )
    script_content = "\n\n---\n\n".join(script_parts)

    subject_sanitized = _sanitize_name(subject)
    topic_sanitized = _sanitize_name(topic)
    settings = get_settings()
    videos_dir = settings.VIDEOS_DIR
    output_dir = videos_dir / subject_sanitized / topic_sanitized
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"explanation_{int(time.time())}.mp4"
    output_file = output_dir / filename

    # 1. Try generating with HeyGen if key is configured
    video_url = None
    if settings.HEYGEN_API_KEY and settings.HEYGEN_API_KEY.strip():
        logger.info("HeyGen API Key configured. Generating multi-scene avatar video using HeyGen...")
        try:
            video_inputs = []
            for idx, slide in enumerate(slides):
                title = slide.get("title", f"Slide {idx+1}")
                bullets = slide.get("bullets", [])
                narration = slide.get("narration", "")
                
                # Render slide locally without drawing the cartoon avatar
                temp_slide_path = output_dir / f"temp_slide_{idx}_{int(time.time())}.png"
                render_slide_image(title, bullets, temp_slide_path, draw_avatar=False)
                
                # Upload to public host
                public_url = upload_slide_to_public_host(temp_slide_path)
                
                # Clean up local temp slide image
                try:
                    temp_slide_path.unlink()
                except Exception:
                    pass
                
                # Clean spoken narration
                clean_narration = re.sub(r"\[Source:\s*[^\]]+\]", "", narration)
                clean_narration = re.sub(r"#+\s*", "", clean_narration)
                clean_narration = clean_narration.replace("**", "").replace("*", "").strip()
                clean_narration = clean_narration[:1500].strip()
                
                video_inputs.append({
                    "character": {
                        "type": "avatar",
                        "avatar_id": settings.HEYGEN_AVATAR_ID,
                        "avatar_style": "normal"
                    },
                    "voice": {
                        "type": "text",
                        "input_text": clean_narration,
                        "voice_id": settings.HEYGEN_VOICE_ID
                    },
                    "background": {
                        "type": "image",
                        "url": public_url
                    }
                })
                
            url = "https://api.heygen.com/v2/video/generate"
            headers = {
                "X-Api-Key": settings.HEYGEN_API_KEY.strip(),
                "Content-Type": "application/json"
            }
            
            payload = {
                "title": f"{subject} - {topic} Explanation",
                "dimension": {
                    "width": 1280,
                    "height": 720
                },
                "video_inputs": video_inputs
            }
            
            req_data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            
            with urllib.request.urlopen(request) as response:
                res = json.loads(response.read().decode())
                
            video_id = res.get("data", {}).get("video_id")
            if not video_id:
                raise ValueError(f"HeyGen generate video failed. Response: {res}")
                
            logger.info("HeyGen video generation task submitted. Video ID: %s. Polling status...", video_id)
            
            status_url = f"https://api.heygen.com/v3/videos/{video_id}"
            status_headers = {"X-Api-Key": settings.HEYGEN_API_KEY.strip()}
            
            max_polls = 30
            poll_interval = 10
            polls = 0
            completed = False
            download_url = None
            
            while polls < max_polls:
                await asyncio.sleep(poll_interval)
                polls += 1
                
                status_req = urllib.request.Request(status_url, headers=status_headers, method="GET")
                with urllib.request.urlopen(status_req) as status_res:
                    status_data = json.loads(status_res.read().decode())
                    
                data_obj = status_data.get("data", {})
                status = data_obj.get("status")
                logger.info("Polling HeyGen video status: %s (poll %d/%d)", status, polls, max_polls)
                
                if status == "completed":
                    download_url = data_obj.get("video_url")
                    completed = True
                    break
                elif status == "failed":
                    err_msg = data_obj.get("error", {}).get("message", "Unknown error")
                    raise ValueError(f"HeyGen video generation failed: {err_msg}")
            
            if not completed or not download_url:
                raise TimeoutError("HeyGen video generation timed out or failed to return video URL.")
                
            logger.info("HeyGen video generated successfully! Downloading from %s...", download_url)
            urllib.request.urlretrieve(download_url, str(output_file))
            logger.info("HeyGen video downloaded and saved to %s", output_file)
            video_url = f"/videos/{subject_sanitized}/{topic_sanitized}/{filename}"
            
        except Exception as e:
            logger.error("Failed to generate video via HeyGen API: %s. Falling back to local video generator...", str(e))

    # 2. Compile video locally if HeyGen not used or failed
    if video_url is None:
        logger.info("Generating presentation video locally...")
        try:
            generate_local_video(slides, output_file)
            video_url = f"/videos/{subject_sanitized}/{topic_sanitized}/{filename}"
        except Exception as local_err:
            logger.error("Local video compilation failed: %s. Falling back to placeholder video.", str(local_err))
            fallback_filename = "explanation_placeholder.mp4"
            fallback_file = output_dir / fallback_filename
            if not fallback_file.exists() or fallback_file.stat().st_size == 0:
                download_placeholder_video(fallback_file)
    return video_url, script_content




def save_assignment_to_db(assignment_json: dict, subject: str, topic: str, batch_id: str, file_path: str = "") -> str:
    from voice_tutor.models import (
        TopicMaster, SubjectMaster, Batch,
        Assignment, AssignmentQuestion, QuestionOption, ProfessorAllocation
    )
    import uuid
    import json

    subject_obj = SubjectMaster.objects.filter(name=subject).first()
    if not subject_obj:
        subject_obj = SubjectMaster.objects.first()

    topic_obj = TopicMaster.objects.filter(name=topic).first()
    if not topic_obj:
        topic_obj = TopicMaster.objects.first()

    assign_code = f"A_{uuid.uuid4().hex[:6].upper()}"

    assignment_obj = Assignment.objects.create(
        topic=topic_obj,
        code=assign_code,
        title=assignment_json.get("title", "Generated Assignment"),
        description=assignment_json.get("description", ""),
        difficulty=assignment_json.get("difficulty", "intermediate"),
        expected_duration=assignment_json.get("expected_duration", 30),
        source_pdf_path=file_path
    )

    for i, q in enumerate(assignment_json.get("questions", [])):
        q_code = f"Q{i+1}_{assign_code}"
        
        q_obj = AssignmentQuestion.objects.create(
            assignment=assignment_obj,
            code=q_code,
            content=q["content"],
            question_type=q["question_type"],
            correct_answer=q["correct_answer"],
            concept_tags=json.dumps(q.get("concept_tags", [])),
            difficulty=q.get("difficulty", "intermediate"),
            order=q.get("order", i+1)
        )

        options = q.get("options")
        if options:
            for opt in options:
                QuestionOption.objects.create(
                    question=q_obj,
                    option_letter=opt["option_letter"],
                    option_text=opt["option_text"]
                )

    batch_obj = Batch.objects.filter(code=batch_id).first()
    if batch_obj:
        alloc = ProfessorAllocation.objects.filter(batch=batch_obj, subject=subject_obj).first()
        if alloc:
            assignment_obj.professor_allocation = alloc
            assignment_obj.save()

    return assign_code

def save_notes_to_db(notes_content: str, subject: str, topic: str) -> bool:
    """Save generated summary notes to the database."""
    from voice_tutor.models import TopicMaster, SubjectMaster, SummaryNotes
    
    topic_obj = TopicMaster.objects.filter(name=topic).first()
    if not topic_obj:
        # Fallback to first if mismatch
        topic_obj = TopicMaster.objects.first()
        
    if not topic_obj:
        logger.error("No TopicMaster found to attach summary notes to.")
        return False
        
    SummaryNotes.objects.create(
        topic=topic_obj,
        title=f"{topic} Summary Notes",
        content=notes_content
    )
    logger.info("Saved summary notes for %s to database.", topic)
    return True
