"""Exam Question Generator with Quality Check - Streamlit app.

Modules 1 and 4 (input/settings and output/export) live here; modules 2 and 3
(generation and quality check) live in /core and are importable without
Streamlit so the test script can drive them.

Team Alt+F4 (PE-B65-01) - SCSE4041 Prompt Engineering, Milestone 2.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

import config
from core import exporter, quality_checker
from core.gemini_client import GeminiError, UsageTally, get_api_key
from core.generator import generate_questions, regenerate_flagged
from core.validators import (
    Settings,
    extract_text_from_upload,
    validate_settings,
)

st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon="📝",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "questions": [],        # merged question + review dicts
    "settings": None,       # the Settings used for the current paper
    "set_notes": "",        # set-level moderator notes
    "quality_ran": False,
    "usage": None,          # UsageTally
    "log": [],              # run log lines shown in the diagnostics expander
    "show_answers": False,
    "upload_text": "",
    "upload_name": "",
}
for key, value in _DEFAULTS.items():
    st.session_state.setdefault(key, value)


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.log.append(f"[{stamp}] {message}")


def reset_paper() -> None:
    st.session_state.questions = []
    st.session_state.set_notes = ""
    st.session_state.quality_ran = False
    st.session_state.usage = None
    st.session_state.log = []


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("📝 Exam Question Generator with Quality Check")
st.caption(
    f"For faculty and teaching assistants · {config.COURSE} · {config.TEAM}"
)

api_key_present = bool(get_api_key())
if not api_key_present:
    st.error(
        "**No Gemini API key configured.** Add `GEMINI_API_KEY` to "
        "`.streamlit/secrets.toml` when running locally, or paste it into the "
        "app's **Settings → Secrets** box on Streamlit Community Cloud. "
        "Get a free key at https://aistudio.google.com/apikey"
    )

# ---------------------------------------------------------------------------
# Module 1: input and settings
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Paper settings")

    subject = st.text_input(
        "Subject / course",
        value="Operating Systems",
        max_chars=config.MAX_SUBJECT_CHARS,
        help="Printed as the heading of the question paper.",
    )
    num_questions = st.slider(
        "Number of questions",
        min_value=config.MIN_QUESTIONS,
        max_value=config.MAX_QUESTIONS,
        value=5,
    )
    question_types = st.multiselect(
        "Question types",
        options=config.QUESTION_TYPES,
        default=["MCQ"],
        help="Pick one or more. Mixed selections are distributed evenly.",
    )
    difficulty = st.selectbox(
        "Difficulty", options=config.DIFFICULTIES, index=1
    )
    bloom_level = st.selectbox(
        "Bloom's taxonomy level",
        options=config.BLOOM_LEVELS,
        index=config.BLOOM_LEVELS.index("Understand"),
    )
    marks = st.number_input(
        "Marks per question",
        min_value=config.MIN_MARKS,
        max_value=config.MAX_MARKS,
        value=2,
        step=1,
    )

    st.divider()
    run_quality = st.toggle(
        "Run quality check",
        value=True,
        help=(
            "A second, independent Gemini call that scores every question out "
            "of 10 and flags anything below "
            f"{config.QUALITY_THRESHOLD}."
        ),
    )

    st.divider()
    st.caption(
        f"**Model:** `{config.MODEL_ID}`  \n"
        f"**Thinking:** {config.GENERATOR_THINKING} (generate) / "
        f"{config.QUALITY_THINKING} (review)  \n"
        f"**Flag threshold:** {config.QUALITY_THRESHOLD}/10  \n"
        f"**API key:** {'✅ found' if api_key_present else '❌ missing'}"
    )

st.subheader("1 · Source material")

tab_topic, tab_upload = st.tabs(["Topic or pasted notes", "Upload notes (.txt / .pdf)"])

with tab_topic:
    typed_text = st.text_area(
        "Topic, syllabus extract, or lecture notes",
        height=200,
        placeholder=(
            "Either a short topic — 'CPU scheduling algorithms: FCFS, SJF, "
            "Round Robin' — or paste a full block of lecture notes."
        ),
        help=(
            f"Between {config.MIN_SOURCE_CHARS} and "
            f"{config.MAX_SOURCE_CHARS:,} characters."
        ),
    )

with tab_upload:
    uploaded = st.file_uploader(
        "Upload a .txt or .pdf of your notes", type=["txt", "pdf"]
    )
    if uploaded is not None:
        text, upload_errors = extract_text_from_upload(
            uploaded.name, uploaded.getvalue()
        )
        if upload_errors:
            for message in upload_errors:
                st.error(message)
            st.session_state.upload_text = ""
            st.session_state.upload_name = ""
        else:
            st.session_state.upload_text = text
            st.session_state.upload_name = uploaded.name
            st.success(
                f"Extracted {len(text):,} characters from **{uploaded.name}**."
            )
            with st.expander("Preview extracted text"):
                st.text(text[:3000] + ("\n\n…truncated preview…" if len(text) > 3000 else ""))
    elif st.session_state.upload_name:
        st.info(
            f"Using previously uploaded **{st.session_state.upload_name}** "
            f"({len(st.session_state.upload_text):,} characters)."
        )

# Uploaded text wins when the topic box is empty; typed text always wins when
# it is filled, so the user can override an upload without clearing it.
if typed_text.strip():
    source_text, source_kind = typed_text, "notes"
elif st.session_state.upload_text:
    source_text, source_kind = (
        st.session_state.upload_text,
        f"uploaded file: {st.session_state.upload_name}",
    )
else:
    source_text, source_kind = "", "topic"

if source_text:
    st.caption(
        f"Source: {len(source_text):,} / {config.MAX_SOURCE_CHARS:,} characters"
    )

validation = validate_settings(
    source_text=source_text,
    subject=subject,
    num_questions=num_questions,
    question_types=question_types,
    difficulty=difficulty,
    bloom_level=bloom_level,
    marks=marks,
)

for warning in validation.warnings:
    st.warning(warning, icon="⚠️")

st.subheader("2 · Generate")

generate_clicked = st.button(
    "Generate questions",
    type="primary",
    disabled=not api_key_present,
    use_container_width=False,
)

if generate_clicked:
    if not validation.ok:
        for message in validation.errors:
            st.error(message, icon="🚫")
    else:
        reset_paper()
        settings = Settings(
            source_text=source_text.strip(),
            subject=subject.strip(),
            num_questions=int(num_questions),
            question_types=list(question_types),
            difficulty=difficulty,
            bloom_level=bloom_level,
            marks=int(marks),
            source_kind=source_kind,
        )
        st.session_state.settings = settings
        usage = UsageTally()

        try:
            with st.status("Generating questions…", expanded=True) as status:
                status.write(
                    f"Call 1 · {config.MODEL_ID} · "
                    f"generator_system.md + generator_user.md"
                )
                result = generate_questions(settings)
                for response in result.responses:
                    usage.add("generate", response)

                if result.unusable_source:
                    status.update(label="Source unusable", state="error")
                    st.error(
                        "Gemini could not find teachable subject content in "
                        "that material: "
                        + "; ".join(result.errors),
                        icon="🚫",
                    )
                    log("generation rejected the source as unusable")
                elif not result.ok:
                    status.update(label="Generation failed", state="error")
                    st.error(
                        "Generation failed after "
                        f"{result.attempts} attempt(s): "
                        + "; ".join(result.errors),
                        icon="🚫",
                    )
                    log(f"generation failed: {result.errors}")
                else:
                    if result.repaired:
                        status.write(
                            "First reply was malformed; the retry succeeded."
                        )
                        log("generation needed its one retry")
                    status.write(
                        f"Got {len(result.questions)} questions in "
                        f"{result.attempts} attempt(s)."
                    )

                    questions = quality_checker.merge(result.questions, {})

                    if run_quality:
                        status.write(
                            f"Call 2 · {config.MODEL_ID} · "
                            "quality_check_system.md + quality_check_user.md"
                        )
                        review = quality_checker.check_questions(
                            result.questions, settings
                        )
                        for response in review.responses:
                            usage.add("quality check", response)

                        if review.ok:
                            questions = quality_checker.merge(
                                result.questions, review.reviews
                            )
                            st.session_state.set_notes = review.set_level_notes
                            st.session_state.quality_ran = True
                            status.write(
                                f"Reviewed · average "
                                f"{review.average_score}/10 · "
                                f"{len(review.flagged_ids)} flagged."
                            )
                            log(
                                f"quality check ok: avg {review.average_score}, "
                                f"flagged {review.flagged_ids}"
                            )
                        else:
                            st.warning(
                                "Questions were generated, but the quality "
                                "check failed: " + "; ".join(review.errors),
                                icon="⚠️",
                            )
                            log(f"quality check failed: {review.errors}")

                    st.session_state.questions = questions
                    st.session_state.usage = usage
                    status.update(label="Done", state="complete")
        except GeminiError as exc:
            st.error(str(exc), icon="🚫")
            log(f"API error: {exc}")

# ---------------------------------------------------------------------------
# Module 4: output and export
# ---------------------------------------------------------------------------

questions = st.session_state.questions
settings = st.session_state.settings

if questions and settings:
    st.divider()
    st.subheader("3 · Review")

    reviewed = [q for q in questions if q.get("score") is not None]
    flagged = [q for q in questions if q.get("flagged")]
    total_marks = sum(int(q.get("marks", 0) or 0) for q in questions)

    cols = st.columns(4)
    cols[0].metric("Questions", len(questions))
    cols[1].metric("Total marks", total_marks)
    cols[2].metric(
        "Average quality",
        f"{round(sum(q['score'] for q in reviewed) / len(reviewed), 2)}/10"
        if reviewed
        else "not run",
    )
    cols[3].metric("Flagged", len(flagged))

    if st.session_state.set_notes:
        st.info(f"**Moderator notes on the set:** {st.session_state.set_notes}")

    left, right = st.columns([1, 2])
    with left:
        st.session_state.show_answers = st.toggle(
            "Show answer key",
            value=st.session_state.show_answers,
            help="Hidden by default so the screen can be shown to students.",
        )
    with right:
        if flagged:
            if st.button(
                f"🔁 Regenerate {len(flagged)} flagged question(s)",
                disabled=not api_key_present,
            ):
                try:
                    with st.status(
                        "Regenerating flagged questions…", expanded=True
                    ) as status:
                        status.write(
                            f"{config.MODEL_ID} · generator_system.md + "
                            "regenerate_user.md"
                        )
                        regen = regenerate_flagged(settings, flagged)
                        usage = st.session_state.usage or UsageTally()
                        for response in regen.responses:
                            usage.add("regenerate", response)

                        if not regen.ok:
                            status.update(label="Regeneration failed", state="error")
                            st.error(
                                "Regeneration failed: " + "; ".join(regen.errors),
                                icon="🚫",
                            )
                            log(f"regeneration failed: {regen.errors}")
                        else:
                            replacements = {q["id"]: q for q in regen.questions}
                            rebuilt = [
                                replacements.get(q["id"], q) for q in questions
                            ]
                            status.write(
                                f"Replaced {len(replacements)} question(s); "
                                "re-reviewing the whole paper."
                            )

                            plain = [
                                {
                                    k: q[k]
                                    for k in (
                                        "id",
                                        "question",
                                        "type",
                                        "options",
                                        "answer",
                                        "explanation",
                                        "difficulty",
                                        "bloom_level",
                                        "marks",
                                    )
                                }
                                for q in rebuilt
                            ]
                            review = quality_checker.check_questions(
                                plain, settings
                            )
                            for response in review.responses:
                                usage.add("re-review", response)

                            if review.ok:
                                st.session_state.questions = (
                                    quality_checker.merge(plain, review.reviews)
                                )
                                st.session_state.set_notes = review.set_level_notes
                                status.write(
                                    f"New average "
                                    f"{review.average_score}/10 · "
                                    f"{len(review.flagged_ids)} still flagged."
                                )
                                log(
                                    "regeneration ok: new avg "
                                    f"{review.average_score}, still flagged "
                                    f"{review.flagged_ids}"
                                )
                            else:
                                st.session_state.questions = (
                                    quality_checker.merge(plain, {})
                                )
                                st.warning(
                                    "Regenerated, but the re-review failed: "
                                    + "; ".join(review.errors),
                                    icon="⚠️",
                                )

                            st.session_state.usage = usage
                            status.update(label="Done", state="complete")
                    st.rerun()
                except GeminiError as exc:
                    st.error(str(exc), icon="🚫")
        elif st.session_state.quality_ran:
            st.success("No questions fell below the quality threshold.", icon="✅")

    questions = st.session_state.questions
    show_answers = st.session_state.show_answers

    view_cards, view_table = st.tabs(["Cards", "Table"])

    with view_cards:
        for n, q in enumerate(questions, start=1):
            score = q.get("score")
            badge = f"{score}/10" if score is not None else "not reviewed"
            flag = " · 🚩 FLAGGED" if q.get("flagged") else ""
            with st.container(border=True):
                head_left, head_right = st.columns([5, 1])
                head_left.markdown(f"**Q{n}.** {q['question']}")
                head_right.markdown(f"**{badge}**{flag}")

                if q.get("options"):
                    for i, option in enumerate(q["options"]):
                        label = "ABCDEF"[i] if i < 6 else str(i + 1)
                        correct = show_answers and option == q.get("answer")
                        prefix = "✅" if correct else "　"
                        st.markdown(f"{prefix} **({label})** {option}")

                st.caption(
                    f"{q['type']} · {q['difficulty']} · Bloom: "
                    f"{q['bloom_level']} · {q.get('marks', 0)} marks"
                )

                if show_answers:
                    st.markdown(f"**Answer:** {q['answer']}")
                    if q.get("explanation"):
                        st.markdown(f"**Marking note:** {q['explanation']}")

                if q.get("scores"):
                    with st.expander("Quality breakdown"):
                        labels = {
                            "clarity": "Clarity",
                            "answer_correctness": "Answer-key correctness",
                            "single_answer": "Single unambiguous answer",
                            "difficulty_match": "Difficulty match",
                            "bloom_match": "Bloom's level match",
                            "source_relevance": "Relevance to source",
                        }
                        for key, label in labels.items():
                            if key in q["scores"]:
                                st.write(f"- {label}: **{q['scores'][key]}/10**")
                        if q.get("issues"):
                            st.markdown("**Issues**")
                            for issue in q["issues"]:
                                st.write(f"- {issue}")
                        if q.get("duplicate_of"):
                            st.write(
                                f"- Flagged as a duplicate of Q{q['duplicate_of']}"
                            )
                        if q.get("suggested_fix"):
                            st.markdown(f"**Suggested fix:** {q['suggested_fix']}")

    with view_table:
        rows = []
        for n, q in enumerate(questions, start=1):
            row = {
                "#": n,
                "Question": q["question"],
                "Type": q["type"],
                "Difficulty": q["difficulty"],
                "Bloom": q["bloom_level"],
                "Marks": q.get("marks"),
                "Score": q.get("score"),
                "Flagged": "🚩" if q.get("flagged") else "",
                "Issues": "; ".join(q.get("issues") or []),
            }
            if show_answers:
                row["Answer"] = q["answer"]
            rows.append(row)
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # --- exports ---
    st.divider()
    st.subheader("4 · Export")
    st.caption(
        "The question paper excludes answers. The answer key includes answers, "
        "marking notes and the quality scores."
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    slug = "".join(
        c if c.isalnum() else "-" for c in settings.subject.lower()
    ).strip("-") or "paper"
    usage_dict = {}
    if st.session_state.usage:
        tally = st.session_state.usage
        usage_dict = {
            "calls": tally.calls,
            "input_tokens": tally.input_tokens,
            "output_tokens": tally.output_tokens,
            "estimated_cost_usd": round(tally.estimated_cost_usd, 4),
            "per_call": tally.details,
        }

    paper_col, key_col, data_col = st.columns(3)

    with paper_col:
        st.markdown("**Question paper**")
        st.download_button(
            "PDF",
            data=exporter.to_pdf_question_paper(questions, settings),
            file_name=f"{slug}-question-paper-{stamp}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        st.download_button(
            "DOCX",
            data=exporter.to_docx_question_paper(questions, settings),
            file_name=f"{slug}-question-paper-{stamp}.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )

    with key_col:
        st.markdown("**Answer key**")
        st.download_button(
            "PDF",
            data=exporter.to_pdf_answer_key(questions, settings),
            file_name=f"{slug}-answer-key-{stamp}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        st.download_button(
            "DOCX",
            data=exporter.to_docx_answer_key(questions, settings),
            file_name=f"{slug}-answer-key-{stamp}.docx",
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )

    with data_col:
        st.markdown("**Data**")
        st.download_button(
            "JSON",
            data=exporter.to_json(
                questions, settings, st.session_state.set_notes, usage_dict
            ),
            file_name=f"{slug}-{stamp}.json",
            mime="application/json",
            use_container_width=True,
        )
        st.download_button(
            "Quality report (CSV)",
            data=exporter.to_quality_csv(questions),
            file_name=f"{slug}-quality-{stamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # --- diagnostics ---
    with st.expander("Run diagnostics (tokens, cost, log)"):
        if st.session_state.usage:
            tally = st.session_state.usage
            st.write(
                f"**{tally.calls} API call(s)** · "
                f"{tally.input_tokens:,} input tokens · "
                f"{tally.output_tokens:,} output tokens · "
                f"≈ ${tally.estimated_cost_usd:.4f} at "
                f"`{config.MODEL_ID}` list pricing "
                f"(${config.PRICE_INPUT_PER_MTOK}/${config.PRICE_OUTPUT_PER_MTOK} "
                "per MTok)"
            )
            st.dataframe(tally.details, hide_index=True, use_container_width=True)
        if st.session_state.log:
            st.code("\n".join(st.session_state.log), language="text")

else:
    st.info(
        "Enter a topic or paste notes, choose your settings in the sidebar, "
        "then press **Generate questions**.",
        icon="👈",
    )

st.divider()
st.caption(
    f"Built with Google Gemini (`{config.MODEL_ID}`) via the official "
    f"`google-genai` Python SDK · 5 prompts in /prompts · {config.TEAM}"
)
