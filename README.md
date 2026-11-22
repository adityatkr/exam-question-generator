# 📝 Exam Question Generator with Quality Check

**A tool for faculty and teaching assistants.** Enter a topic or paste your
lecture notes, choose the paper settings, and get exam questions that have been
**automatically quality-checked by a second, independent AI pass** before you
ever see them.

| | |
|---|---|
| **Course** | SCSE4041 — Prompt Engineering, Milestone 2 |
| **Team** | Alt+F4 (PE-B65-01) |
| **Members** | Aditya Thakur, Divyansh Vijayvargiya |
| **AI tool / model** | Anthropic Claude — `claude-opus-5-5`, via the official `anthropic` Python SDK |
| **Stack** | Python 3.12 · Streamlit · ReportLab · python-docx · pypdf |
| **Live prototype** | _paste your Streamlit Community Cloud URL here after deploying_ |

---

## Why a second AI pass?

A single "write me exam questions" call produces questions that *look* right.
In our own testing it reliably produced MCQs with two defensibly correct
options, answer keys that did not answer the question asked, recall questions
labelled "Apply", and near-duplicates — all of which a faculty member would
have to catch by hand.

So the app never shows you a first draft. Every question is reviewed by a
**second Claude call with a different prompt and a different persona** (an
examination-board moderator rather than a question setter). That reviewer did
not write the questions, scores each one out of 10 across six named criteria,
and flags anything below **7.0/10** for regeneration. You see the score, the
per-criterion breakdown, the specific defect and a suggested fix.

---

## Architecture

```mermaid
flowchart TD
    subgraph M1["Module 1 · Input & Settings (app.py + validators.py)"]
        A1["Topic / pasted notes"]
        A2[".txt / .pdf upload<br/>(pypdf text extraction)"]
        A3["Settings: subject, count 1-20,<br/>types, difficulty, Bloom level, marks"]
        A4{"validate_settings()<br/>empty · too short · too long<br/>· bad count · no type · conflicts"}
        A1 --> A4
        A2 --> A4
        A3 --> A4
    end

    A4 -- "errors" --> E1["Show blocking errors.<br/>No API call, no cost."]
    A4 -- "warnings" --> A5["Show warning, continue"]
    A4 -- "ok" --> M2

    subgraph M2["Module 2 · Question Generator (generator.py)"]
        B1["Render generator_user.md"]
        B2["CLAUDE CALL 1<br/>system: generator_system.md<br/>model: claude-opus-5-5"]
        B3{"extract_json()<br/>+ validate_questions()"}
        B1 --> B2 --> B3
        B3 -- "malformed / invalid" --> B4["Retry ONCE with the<br/>validator errors appended"]
        B4 --> B2
        B3 -- "unusable_source" --> E2["Refuse: no teachable<br/>content in the source"]
    end

    B3 -- "valid questions" --> M3

    subgraph M3["Module 3 · Quality Check (quality_checker.py)"]
        C1["Render quality_check_user.md<br/>with questions + source + spec"]
        C2["CLAUDE CALL 2 — independent reviewer<br/>system: quality_check_system.md"]
        C3["6 criteria scored 0-10 each:<br/>clarity · answer-key correctness<br/>· single answer · difficulty match<br/>· Bloom match · source relevance<br/><br/>score = min(criteria)<br/>+ duplicate detection"]
        C4{"score < 7.0<br/>or duplicate?"}
        C1 --> C2 --> C3 --> C4
    end

    C4 -- "yes" --> D1["🚩 FLAGGED"]
    C4 -- "no" --> D2["✅ Approved"]

    D1 --> R1["'Regenerate flagged' button"]
    R1 --> R2["CLAUDE CALL 3<br/>system: generator_system.md (reused)<br/>user: regenerate_user.md<br/>+ the defect and suggested fix"]
    R2 --> C1

    D1 --> M4
    D2 --> M4

    subgraph M4["Module 4 · Output & Export (app.py + exporter.py)"]
        F1["Cards / table view<br/>+ answer-key toggle<br/>+ per-criterion breakdown"]
        F2["Question paper → PDF, DOCX"]
        F3["Answer key → PDF, DOCX<br/>(answers + marking notes + scores)"]
        F4["JSON · Quality report CSV"]
    end
```

**The flow in one line:** input → validate → generate (retry once) → **second
AI review** → flag → optionally regenerate the flagged ones → export.

---

## Modules

| # | Module | Files | What it does |
|---|--------|-------|--------------|
| 1 | **Input & settings** | `app.py`, `core/validators.py` | Topic box, notes paste, `.txt`/`.pdf` upload with text extraction. Subject, question count (1–20), four question types, difficulty, Bloom's level, marks. Blocking errors for empty / too-short / too-long input, out-of-range counts, no type selected, unsupported file types; non-blocking warnings for implausible combinations (10-mark True/False, 20 questions from 200 characters, "Create" level with MCQs only). |
| 2 | **Question generator** | `core/generator.py`, `prompts/generator_*.md` | System prompt + structured user prompt returning strict JSON: question, type, options, answer, explanation, difficulty, Bloom level, marks. Parsed, then validated field by field — MCQs must have exactly 4 options and an answer that appears verbatim among them. **Retries once** with the validator's errors appended if anything fails. Has an escape hatch: on gibberish input it returns `unusable_source` instead of inventing a topic. |
| 3 | **Quality check** | `core/quality_checker.py`, `prompts/quality_check_*.md` | A **second, independent Claude call**. Scores six criteria 0–10, takes the **minimum** as the question's score (one fatal defect sinks a question), returns issues + a suggested fix, detects duplicates, and writes set-level moderator notes. Anything under 7.0/10 is flagged. "Regenerate flagged questions" sends only the flagged ones back with their defects attached, keeps their ids so they drop into the same slots, then re-reviews the whole paper. |
| 4 | **Output & export** | `app.py`, `core/exporter.py` | Card view and table view, answer key behind a toggle, expandable per-criterion score breakdown. Six downloads: question paper (PDF, DOCX), answer key (PDF, DOCX — with marking notes and quality scores), full JSON, and a quality-report CSV. Plus a diagnostics panel showing calls, tokens and estimated cost. |

---

## Prompt design

**The final solution uses exactly 5 prompts**, one file each in `/prompts`
(see [`prompts/README.md`](prompts/README.md) for the full inventory):

| # | File | Kind | Sent on |
|---|------|------|---------|
| 1 | `generator_system.md` | System | Generate **and** Regenerate |
| 2 | `generator_user.md` | User template | Generate |
| 3 | `quality_check_system.md` | System | Quality check |
| 4 | `quality_check_user.md` | User template | Quality check |
| 5 | `regenerate_user.md` | User template | Regenerate flagged |

They compose **3 distinct Claude calls**, each a system + user pair. Prompt 1
is deliberately reused by the Regenerate call: if a repair were written under a
different persona or output contract, the quality checker would be judging it
against standards it was not written to.

### Techniques used, and why

| Technique | Where | Why it earned its place |
|---|---|---|
| **Role / persona assignment** | Both system prompts | The generator is a "fifteen-year examination setter"; the reviewer is a "moderation officer who did not write these questions". Giving the reviewer an adversarial persona is what stopped it rubber-stamping its own house style. |
| **Separation of concerns across two calls** | Modules 2 and 3 | Asking one call to both write and grade produced inflated self-assessment. Two calls with different system prompts produces a genuinely critical review. |
| **Explicit output contract** | Both system prompts | "First character must be `{`, last must be `}`, no markdown fences." This is what removed the conversational preamble that broke JSON parsing. |
| **Full JSON schema with enums** | Both user templates | Field names, types and allowed enum values given inline. Without it, field names drifted between calls (`correct_answer` vs `answer`) and the app could not parse reliably. |
| **Negative constraints** | `generator_system.md` | Named anti-patterns: no "all of the above", no "both A and B", no "according to the passage above", no invented facts. |
| **Scoring rubric with calibration** | `quality_check_system.md` | 9–10 / 7–8 / 4–6 / 1–3 bands with a described meaning each, plus "do not be generous" and an explicit instruction *not* to manufacture issues to look thorough. |
| **`score = min(criteria)`** | `quality_check_system.md` | Averaging six criteria let a fatal defect (wrong answer key, 2/10) hide behind five good scores and land above the threshold. Taking the minimum makes any single fatal defect flag the question. |
| **Structured delimiters** | User templates | Source material is wrapped in `<source_material>` tags so the model cannot confuse notes with instructions. |
| **Self-correction loop** | `core/generator.py`, `core/quality_checker.py` | On malformed output, the validator's own error list is appended to the prompt and the call is retried once. |
| **Escape hatch** | `generator_user.md` | An explicit `{"error": "unusable_source"}` shape, so gibberish input produces a refusal rather than hallucinated questions. |

Four logged versions of each of the two main prompts, with the full text, the
deliberate change, the reason and the **measured** effect of a real API run,
are in [`prompts/iteration_log.md`](prompts/iteration_log.md).

---

## Setup

### 1. Get an Anthropic API key

1. Go to **<https://console.anthropic.com>** and sign in (or create an account).
2. Billing is required before the API will serve requests: **Settings →
   Billing → Add payment method**, then buy credits (US$5 is plenty — a
   20-question paper with a full quality check costs well under one cent).
3. Go to **Settings → API keys → Create Key**, name it (e.g. `exam-generator`),
   and copy the key. It starts with `sk-ant-api03-…`.
4. **Copy it immediately** — the console will not show it again.

### 2. Run it locally

```bash
git clone https://github.com/adityatkr/exam-question-generator.git
cd exam-question-generator

python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Create `.streamlit/secrets.toml` (copy `.streamlit/secrets.toml.example`) and
paste your key:

```toml
ANTHROPIC_API_KEY = "sk-ant-api03-your-real-key-here"
```

Then:

```bash
streamlit run app.py
```

It opens at <http://localhost:8501>.

> `.streamlit/secrets.toml` is in `.gitignore` and must never be committed.
> The key is read from `st.secrets`, the `ANTHROPIC_API_KEY` environment
> variable, or that file — never hard-coded.

### 3. Run the tests

```bash
python tests/run_tests.py --offline   # the 9 validation cases, no API calls
python tests/run_tests.py             # all 16 cases (makes real API calls)
python tests/run_iterations.py        # all 8 prompt versions against the API
```

---

## Deploy to Streamlit Community Cloud (free public link)

1. Push this repo to GitHub as a **public** repository.
2. Go to **<https://share.streamlit.io>** and sign in with GitHub. Authorise
   Streamlit to read your repositories.
3. Click **Create app** → **Deploy a public app from GitHub**.
4. Fill in:
   - **Repository**: `adityatkr/exam-question-generator`
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL**: choose your subdomain (this becomes your public link)
5. **Before clicking Deploy**, open **Advanced settings → Secrets** and paste
   exactly this, with your real key:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-api03-your-real-key-here"
   ```

   This is the same content as your local `.streamlit/secrets.toml`. Streamlit
   stores it encrypted; it is not visible in the repo.
6. Click **Deploy**. First build takes 2–4 minutes while it installs
   `requirements.txt`.
7. Your app is live at `https://<your-subdomain>.streamlit.app` — anyone with
   the link can open it. Paste that URL into the table at the top of this file.

> **Changing the key later:** app page → **⋮ → Settings → Secrets**, edit, save.
> The app reboots automatically.
> **Logs:** **⋮ → Manage app** shows the build and runtime logs.

---

## Screenshots

> Replace these placeholders with real screenshots before submitting.
> Save them into `assets/` with these exact names and they will render here.

| | |
|---|---|
| **Input and settings** | ![Input and settings](assets/01-input-settings.png) |
| **Generated questions with quality scores** | ![Generated questions](assets/02-questions-scored.png) |
| **A flagged question and its per-criterion breakdown** | ![Flagged question](assets/03-flagged-breakdown.png) |
| **Export buttons** | ![Exports](assets/04-exports.png) |
| **Exported question paper PDF** | ![Question paper PDF](assets/05-question-paper-pdf.png) |
| **Exported answer key PDF** | ![Answer key PDF](assets/06-answer-key-pdf.png) |

---

## Test summary

_Filled in from a real run — see [`tests/test_cases.md`](tests/test_cases.md)
for the full table and `tests/output/results.json` for raw output._

<!-- TEST_SUMMARY -->

---

## Limitations

1. **Needs a funded API key.** No key, or no credits, and nothing generates.
   The app says so clearly rather than failing silently.
2. **The quality checker is itself an LLM.** It catches the defects we planted
   and tested for (see the iteration log), but it is a second opinion, not a
   proof. **A human must still approve any paper before it reaches students.**
3. **No OCR.** Scanned PDFs have no extractable text layer; the app detects
   this and asks you to paste the notes instead.
4. **20,000-character source limit** (~5,000 tokens), to keep cost predictable.
   Longer notes must be split across papers.
5. **20-question ceiling**, mainly to bound output tokens and latency. A
   20-question paper plus review takes roughly 1–2 minutes.
6. **No diagrams, equations or images** in questions. Output is plain text, so
   subjects that need circuit diagrams or rendered mathematics are poorly
   served. LaTeX is passed through as literal text.
7. **No persistence.** Everything lives in Streamlit session state; refreshing
   the page loses the current paper. Export before you refresh.
8. **Non-English support is best-effort.** It works (tested in Hindi), but the
   quality checker's criteria were written and calibrated in English.
9. **Costs money per click.** Roughly US$0.01–0.05 per paper at
   `claude-opus-5-5` list prices. The diagnostics panel shows the real figure
   for each run.
10. **Single user.** Streamlit Community Cloud apps share one process; heavy
    concurrent use will queue.

---

## Team and contributions

| Member | Contribution |
|---|---|
| **Aditya Thakur** | Project setup and repository structure; `config.py`; the Claude API integration layer (`core/claude_client.py`) including key resolution, streaming, token budgets and error translation; Module 2 (question generator) and its retry loop; the generator prompt and its four logged iterations; Module 4 export pipeline (`core/exporter.py` — PDF, DOCX, JSON, CSV); deployment to Streamlit Community Cloud. |
| **Divyansh Vijayvargiya** | Module 1 (input, settings and `core/validators.py`, including both input validation and model-output validation); Module 3 (quality check — `core/quality_checker.py`); the quality-check prompt, its six-criteria rubric and its four logged iterations; the Streamlit review UI (cards, table, answer-key toggle, score breakdown, regenerate flow); the test harness (`tests/run_tests.py`) and the 16 documented test cases; README and documentation. |

Both members jointly designed the two-pass generate-then-review architecture,
reviewed each other's prompts, and ran the iteration and test suites.

---

## Repository layout

```
.
├── app.py                      # Streamlit app: modules 1 and 4
├── config.py                   # model id, limits, thresholds — change here
├── requirements.txt
├── README.md
├── .gitignore                  # ignores .streamlit/secrets.toml
├── .streamlit/
│   ├── config.toml             # theme + 10 MB upload cap
│   └── secrets.toml.example    # copy to secrets.toml and add your key
├── core/
│   ├── prompt_loader.py        # reads /prompts, renders user templates
│   ├── validators.py           # input validation + model-output validation
│   ├── claude_client.py        # the one place that calls the Anthropic SDK
│   ├── generator.py            # module 2: generate + regenerate
│   ├── quality_checker.py      # module 3: the second AI pass
│   └── exporter.py             # module 4: PDF / DOCX / JSON / CSV
├── prompts/                    # 5 prompts — the graded deliverable
│   ├── README.md               # prompt inventory
│   ├── generator_system.md
│   ├── generator_user.md
│   ├── quality_check_system.md
│   ├── quality_check_user.md
│   ├── regenerate_user.md
│   └── iteration_log.md        # v1 → v4 of both main prompts, with real results
├── tests/
│   ├── test_cases.md           # 16 cases with real actual-output column
│   ├── run_tests.py            # drives the same functions app.py calls
│   ├── run_iterations.py       # runs every prompt version against the API
│   └── output/                 # results.json, iterations.json (git-ignored)
└── assets/                     # screenshots
```

---

Built with Claude (`claude-opus-5-5`) via the official `anthropic` Python SDK.
