"""Module 4 (part 2): export.

Six artefacts, each returned as `bytes` so Streamlit can hand them straight to
`st.download_button`:

  * question paper   -> PDF, DOCX
  * answer key       -> PDF, DOCX  (includes answers, explanations, QC scores)
  * full data        -> JSON
  * quality report   -> CSV

Nothing here touches Streamlit, so the test script can call it too.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

import config

_OPTION_LABELS = ["A", "B", "C", "D", "E", "F"]


def _escape(text: Any) -> str:
    """Escape for ReportLab's mini-HTML paragraph markup."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _total_marks(questions: list[dict[str, Any]]) -> int:
    return sum(int(q.get("marks", 0) or 0) for q in questions)


def _meta(settings: Any) -> dict[str, Any]:
    return {
        "subject": getattr(settings, "subject", ""),
        "difficulty": getattr(settings, "difficulty", ""),
        "bloom_level": getattr(settings, "bloom_level", ""),
        "num_questions": getattr(settings, "num_questions", 0),
        "marks": getattr(settings, "marks", 0),
        "question_types": getattr(settings, "question_types", []),
    }


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_json(
    questions: list[dict[str, Any]],
    settings: Any,
    set_level_notes: str = "",
    usage: dict[str, Any] | None = None,
) -> bytes:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app": config.APP_TITLE,
        "team": config.TEAM,
        "model": config.MODEL_ID,
        "settings": _meta(settings),
        "total_marks": _total_marks(questions),
        "quality_threshold": config.QUALITY_THRESHOLD,
        "set_level_notes": set_level_notes,
        "usage": usage or {},
        "questions": questions,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# CSV quality report
# ---------------------------------------------------------------------------

def to_quality_csv(questions: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "type",
            "difficulty",
            "bloom_level",
            "marks",
            "score",
            "flagged",
            "duplicate_of",
            "clarity",
            "answer_correctness",
            "single_answer",
            "difficulty_match",
            "bloom_match",
            "source_relevance",
            "issues",
            "suggested_fix",
            "question",
        ]
    )
    for q in questions:
        scores = q.get("scores") or {}
        writer.writerow(
            [
                q.get("id"),
                q.get("type"),
                q.get("difficulty"),
                q.get("bloom_level"),
                q.get("marks"),
                q.get("score"),
                "YES" if q.get("flagged") else "no",
                q.get("duplicate_of") or "",
                scores.get("clarity", ""),
                scores.get("answer_correctness", ""),
                scores.get("single_answer", ""),
                scores.get("difficulty_match", ""),
                scores.get("bloom_match", ""),
                scores.get("source_relevance", ""),
                " | ".join(q.get("issues") or []),
                q.get("suggested_fix", ""),
                q.get("question", ""),
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PaperTitle",
            parent=base["Title"],
            fontSize=16,
            leading=20,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "PaperSubtitle",
            parent=base["Normal"],
            fontSize=9.5,
            leading=13,
            alignment=TA_CENTER,
            textColor="#444444",
        ),
        "meta": ParagraphStyle(
            "Meta", parent=base["Normal"], fontSize=9.5, leading=14
        ),
        "question": ParagraphStyle(
            "Question",
            parent=base["Normal"],
            fontSize=11,
            leading=15.5,
            spaceBefore=10,
            spaceAfter=3,
        ),
        "option": ParagraphStyle(
            "Option",
            parent=base["Normal"],
            fontSize=10.5,
            leading=14.5,
            leftIndent=16,
        ),
        "tag": ParagraphStyle(
            "Tag",
            parent=base["Normal"],
            fontSize=8.5,
            leading=12,
            textColor="#666666",
            leftIndent=16,
            spaceBefore=2,
        ),
        "answer": ParagraphStyle(
            "Answer",
            parent=base["Normal"],
            fontSize=10.5,
            leading=14.5,
            leftIndent=16,
            textColor="#0B6B3A",
        ),
        "note": ParagraphStyle(
            "Note",
            parent=base["Normal"],
            fontSize=9.5,
            leading=13.5,
            leftIndent=16,
            textColor="#333333",
        ),
        "flag": ParagraphStyle(
            "Flag",
            parent=base["Normal"],
            fontSize=9.5,
            leading=13.5,
            leftIndent=16,
            textColor="#B3261E",
        ),
    }


def _pdf_header(story: list, styles: dict, settings: Any, questions: list, title: str):
    meta = _meta(settings)
    story.append(Paragraph(_escape(meta["subject"] or "Question Paper"), styles["title"]))
    story.append(Paragraph(_escape(title), styles["subtitle"]))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"<b>Questions:</b> {len(questions)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Total marks:</b> {_total_marks(questions)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Difficulty:</b> {_escape(meta['difficulty'])} "
            f"&nbsp;&nbsp;|&nbsp;&nbsp; <b>Bloom's level:</b> "
            f"{_escape(meta['bloom_level'])}",
            styles["meta"],
        )
    )
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.8, color="#999999"))


def _build_pdf(story: list, title: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=config.APP_TITLE,
    )
    doc.build(story)
    return buffer.getvalue()


def to_pdf_question_paper(
    questions: list[dict[str, Any]], settings: Any
) -> bytes:
    styles = _pdf_styles()
    story: list = []
    _pdf_header(story, styles, settings, questions, "Question Paper")

    for n, q in enumerate(questions, start=1):
        story.append(
            Paragraph(
                f"<b>Q{n}.</b> {_escape(q['question'])} "
                f"<font size=9 color='#666666'>[{q.get('marks', 0)} marks]</font>",
                styles["question"],
            )
        )
        for i, option in enumerate(q.get("options") or []):
            label = _OPTION_LABELS[i] if i < len(_OPTION_LABELS) else str(i + 1)
            story.append(
                Paragraph(f"({label}) {_escape(option)}", styles["option"])
            )
        if q["type"] in {"Short Answer", "Long Answer"}:
            story.append(Spacer(1, 6))

    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="30%", thickness=0.6, color="#aaaaaa"))
    story.append(Paragraph("End of question paper", styles["subtitle"]))
    return _build_pdf(story, "Question Paper")


def to_pdf_answer_key(questions: list[dict[str, Any]], settings: Any) -> bytes:
    styles = _pdf_styles()
    story: list = []
    _pdf_header(story, styles, settings, questions, "Answer Key and Quality Report")

    for n, q in enumerate(questions, start=1):
        story.append(
            Paragraph(f"<b>Q{n}.</b> {_escape(q['question'])}", styles["question"])
        )
        for i, option in enumerate(q.get("options") or []):
            label = _OPTION_LABELS[i] if i < len(_OPTION_LABELS) else str(i + 1)
            marker = " &#10003;" if option == q.get("answer") else ""
            story.append(
                Paragraph(
                    f"({label}) {_escape(option)}{marker}", styles["option"]
                )
            )
        story.append(
            Paragraph(f"<b>Answer:</b> {_escape(q['answer'])}", styles["answer"])
        )
        if q.get("explanation"):
            story.append(
                Paragraph(
                    f"<b>Marking note:</b> {_escape(q['explanation'])}",
                    styles["note"],
                )
            )

        score = q.get("score")
        tag = (
            f"{q['type']} &middot; {q['difficulty']} &middot; "
            f"Bloom: {q['bloom_level']} &middot; {q.get('marks', 0)} marks"
        )
        if score is not None:
            tag += f" &middot; quality {score}/10"
        story.append(Paragraph(tag, styles["tag"]))

        if q.get("flagged"):
            bits = []
            if q.get("issues"):
                bits.append("Issues: " + "; ".join(q["issues"]))
            if q.get("duplicate_of"):
                bits.append(f"Duplicate of Q{q['duplicate_of']}")
            if q.get("suggested_fix"):
                bits.append("Suggested fix: " + q["suggested_fix"])
            story.append(
                Paragraph(
                    "<b>FLAGGED</b> &mdash; " + _escape(" | ".join(bits)),
                    styles["flag"],
                )
            )

    reviewed = [q for q in questions if q.get("score") is not None]
    if reviewed:
        average = round(sum(q["score"] for q in reviewed) / len(reviewed), 2)
        flagged = sum(1 for q in questions if q.get("flagged"))
        story.append(Spacer(1, 14))
        story.append(HRFlowable(width="100%", thickness=0.8, color="#999999"))
        story.append(
            Paragraph(
                f"<b>Quality summary</b> &mdash; average score {average}/10 "
                f"across {len(reviewed)} reviewed question(s); {flagged} "
                f"flagged below the threshold of {config.QUALITY_THRESHOLD}. "
                f"Reviewed by {config.MODEL_ID} in a second, independent pass.",
                styles["meta"],
            )
        )
    return _build_pdf(story, "Answer Key")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _docx_base(questions: list[dict[str, Any]], settings: Any, title: str) -> Document:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    meta = _meta(settings)

    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(meta["subject"] or "Question Paper")
    run.bold = True
    run.font.size = Pt(16)

    sub = document.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(title)
    sub_run.font.size = Pt(10)
    sub_run.italic = True

    info = document.add_paragraph()
    info_run = info.add_run(
        f"Questions: {len(questions)}  |  Total marks: {_total_marks(questions)}  |  "
        f"Difficulty: {meta['difficulty']}  |  Bloom's level: {meta['bloom_level']}"
    )
    info_run.font.size = Pt(9.5)

    document.add_paragraph("_" * 78)
    return document


def _docx_bytes(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def to_docx_question_paper(
    questions: list[dict[str, Any]], settings: Any
) -> bytes:
    document = _docx_base(questions, settings, "Question Paper")

    for n, q in enumerate(questions, start=1):
        para = document.add_paragraph()
        para.add_run(f"Q{n}. ").bold = True
        para.add_run(q["question"])
        marks = para.add_run(f"   [{q.get('marks', 0)} marks]")
        marks.font.size = Pt(9)
        marks.italic = True

        for i, option in enumerate(q.get("options") or []):
            label = _OPTION_LABELS[i] if i < len(_OPTION_LABELS) else str(i + 1)
            opt = document.add_paragraph(f"({label}) {option}")
            opt.paragraph_format.left_indent = Pt(18)

        if q["type"] in {"Short Answer", "Long Answer"}:
            document.add_paragraph("")

    closing = document.add_paragraph("End of question paper")
    closing.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return _docx_bytes(document)


def to_docx_answer_key(questions: list[dict[str, Any]], settings: Any) -> bytes:
    document = _docx_base(questions, settings, "Answer Key and Quality Report")

    for n, q in enumerate(questions, start=1):
        para = document.add_paragraph()
        para.add_run(f"Q{n}. ").bold = True
        para.add_run(q["question"])

        for i, option in enumerate(q.get("options") or []):
            label = _OPTION_LABELS[i] if i < len(_OPTION_LABELS) else str(i + 1)
            marker = "  <-- correct" if option == q.get("answer") else ""
            opt = document.add_paragraph(f"({label}) {option}{marker}")
            opt.paragraph_format.left_indent = Pt(18)

        ans = document.add_paragraph()
        ans.paragraph_format.left_indent = Pt(18)
        ans.add_run("Answer: ").bold = True
        ans.add_run(q["answer"])

        if q.get("explanation"):
            note = document.add_paragraph()
            note.paragraph_format.left_indent = Pt(18)
            note.add_run("Marking note: ").bold = True
            note_run = note.add_run(q["explanation"])
            note_run.font.size = Pt(10)

        tag_text = (
            f"{q['type']} | {q['difficulty']} | Bloom: {q['bloom_level']} | "
            f"{q.get('marks', 0)} marks"
        )
        if q.get("score") is not None:
            tag_text += f" | quality {q['score']}/10"
        tag = document.add_paragraph()
        tag.paragraph_format.left_indent = Pt(18)
        tag_run = tag.add_run(tag_text)
        tag_run.font.size = Pt(8.5)
        tag_run.italic = True

        if q.get("flagged"):
            bits = []
            if q.get("issues"):
                bits.append("Issues: " + "; ".join(q["issues"]))
            if q.get("duplicate_of"):
                bits.append(f"Duplicate of Q{q['duplicate_of']}")
            if q.get("suggested_fix"):
                bits.append("Suggested fix: " + q["suggested_fix"])
            flag = document.add_paragraph()
            flag.paragraph_format.left_indent = Pt(18)
            flag.add_run("FLAGGED - ").bold = True
            flag_run = flag.add_run(" | ".join(bits))
            flag_run.font.size = Pt(9.5)

    reviewed = [q for q in questions if q.get("score") is not None]
    if reviewed:
        average = round(sum(q["score"] for q in reviewed) / len(reviewed), 2)
        flagged = sum(1 for q in questions if q.get("flagged"))
        document.add_paragraph("_" * 78)
        summary = document.add_paragraph()
        summary.add_run("Quality summary: ").bold = True
        summary.add_run(
            f"average score {average}/10 across {len(reviewed)} reviewed "
            f"question(s); {flagged} flagged below the threshold of "
            f"{config.QUALITY_THRESHOLD}. Reviewed by {config.MODEL_ID} in a "
            "second, independent pass."
        )

    return _docx_bytes(document)
