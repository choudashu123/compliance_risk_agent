# Compliance & Risk Agent — Demo Prototype

A lightweight, fully functional demo of a **Compliance & Risk (GRC) agent**
built as a generalized **LLM-based RAG** pipeline (not keyword rules):

1. Upload compliance PDFs — extracted, chunked, and embedded into a vector store.
2. Ask a compliance question in chat — a **LangGraph** agent retrieves the
   most semantically relevant chunks, an **LLM produces a structured (Pydantic)
   analysis** citing exact chunk ids, and a guardrail blocks any citation it
   can't trace back to a real uploaded chunk.
3. When the LLM's analysis flags a gap, it **dynamically drafts a Finding and
   a Risk** in `proposed` status (inherent risk scored High/Medium/Low,
   residual risk left blank).
4. A human reviews the **Approvals** tab: approves the finding, records a
   mitigation, and grades the **residual risk** — only then does it become
   `official`.
5. The **Registers** tab is the live system of record.

```
┌────────────────────────────────────────────────────────────┐
│         Static SPA  (Upload · Chat · Approvals · Registers) │
└───────────────────────────┬────────────────────────────────┘
                            │  REST / JSON
┌───────────────────────────▼────────────────────────────────┐
│                    FastAPI  (app.py)                        │
│  /api/upload   /api/chat   /api/approvals   /api/registers  │
└─────────────┬──────────────┬────────────────────┬───────────┘
              │              │                    │
   ┌──────────▼──────┐ ┌─────▼───────────┐ ┌──────▼──────────────┐
   │ SQLite grc.db    │ │ ChromaDB +      │ │ LangGraph agent      │
   │ documents        │ │ fastembed       │ │ retrieve → analyze → │
   │ findings / risks │ │ (semantic index)│ │ draft → guard        │
   └──────────────────┘ └─────────────────┘ └──────────┬───────────┘
                                                         │
                                     LangChain init_chat_model
                                     (Gemini / OpenAI / Anthropic /
                                     Ollama / offline mock)
                                     → structured Pydantic AgentAnswer
```

> The entire backend lives in one file — **`app.py`** — in six labelled
> sections: Config · Schemas · Store · Agent · Approvals · API.

---

## 1. Prerequisites

- **Python 3.9+** (developed and tested on 3.9)
- No database server, no Node.js, no build step.
- No API key required by default (offline `mock` LLM mode). Drop a
  `GEMINI_API_KEY` (or OpenAI/Anthropic) into `.env` for real LLM reasoning.
- First run downloads the small (~130 MB) `BAAI/bge-small-en-v1.5` embedding
  model from Hugging Face and caches it locally — needs internet once.

---

## 2. Quick Start (One Command)

### macOS / Linux
```bash
./run.sh
```

### Windows
```cmd
run.bat
```
*(or in PowerShell: `.\run.ps1`)*

The startup scripts automatically:
1. Create `.env` from `.env.example` (if not present)
2. Set up the `.venv` virtual environment and install `requirements.txt`
3. Generate sample PDFs in `sample_docs/`
4. Free the port and start the server on **http://localhost:8000**

*(Manual setup if preferred: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python3 app.py`)*

---

## 3. How to run the app

### Option A — Web UI (recommended)

```bash
python3 app.py
# or
./run.sh
```

Then open **http://localhost:8000**. `run.sh` frees the port first, so running it
again is a clean **restart**.

```bash
./run.sh 8080        # different port
./run.sh --no-reload # disable autoreload (extra flags pass through to uvicorn)
# or via python directly:
PORT=8080 python3 app.py
```

Equivalent manual form:

```bash
python -c "import demo; demo.make_sample_pdfs()"   # once
python -m uvicorn app:app --port 8000 --reload
```

Then, in the browser:

| Tab | Action |
|-----|--------|
| **📁 Upload**     | Select the 3 files from `sample_docs/`, click **Ingest**. |
| **💬 Chat**       | Ask: *"Can we onboard an EU customer under GDPR based on our uploaded docs?"* |
| **📋 Approvals**  | Approve the drafted finding; type a mitigation (e.g. *interim SCCs*); pick **Residual: Medium**; click **Grade & approve**. |
| **🗂️ Registers**  | See documents, findings and graded risks update live. |

Use **Reset demo** (top-right) to wipe SQLite + the vector store and start over.

### Option B — Automated end-to-end demo (no browser)

Runs the whole flow in the terminal and prints each step's JSON:

```bash
python demo.py
```

It generates the sample PDFs, ingests them, asks the GDPR question, lets the
LLM draft a finding + risk, approves the finding, grades the residual risk,
and dumps the final registers.

---

## 4. Running the tests

```bash
pytest -q
```

Five acceptance tests covering:

1. **PDF ingestion** — `pypdf` extracts text, chunks are stored in SQLite and embedded into the vector store.
2. **Compliance discovery** — semantic search surfaces the DPA gap and the answer cites only real uploaded files.
3. **Citation guard** — fabricated document names / chunk ids are rejected.
4. **Drafting** — finding is `proposed`, risk has an inherent score and `residual_score = None`.
5. **Human gate** — a risk becomes `official` only when a human supplies a valid
   residual score (`Low` / `Medium` / `High`); anything else returns HTTP 422.

Tests run against an isolated temp SQLite DB + temp Chroma dir (`GRC_DB` env
var) and default to the offline `mock` LLM mode, so they need no API key and
never touch `data/`.

---

## 5. Sample documents

`demo.py` (`make_sample_pdfs()`) writes three PDFs into `sample_docs/`:

| File | Purpose |
|------|---------|
| `1_GDPR_Art28_DPA_Requirements.pdf`      | The obligation — GDPR Art. 28 requires a signed DPA; fines up to €20M / 4% turnover. |
| `2_Acme_Security_and_Backup_Policy.pdf`  | A satisfied control — encryption at rest via AWS KMS (GDPR Art. 32). |
| `3_EU_Customer_Onboarding_Assessment.pdf`| The gap — Acme has **not** executed a DPA with the prospective German client. |

You can also upload your own PDFs — retrieval is fully semantic (embeddings,
not keywords) and the LLM's gap/finding/risk analysis is domain-agnostic, so
it isn't tied to GDPR or to these specific filenames.

---

## 6. Architecture: RAG + structured LLM output

Everything below is a section of **`app/main.py`** (top to bottom):

- **Config** — reads `.env` / environment only (no config file). Auto-detects the
  LLM provider from whichever API key is present and maps it to a LangChain
  provider id.
- **Schemas** — the `AgentAnswer` Pydantic model every LLM call must return:
  `answer`, `citations` (filename + chunk_id), `has_gap`, and an optional
  `drafts` (one Finding + one Risk, inherent score only).
- **Store** — a persistent ChromaDB collection over a `fastembed`
  (`BAAI/bge-small-en-v1.5`) embedding function, plus the SQLite system-of-record
  and the `pypdf` extract/chunk step. SQLite owns the chunk text; Chroma only
  stores embeddings keyed by the SQLite chunk id.
- **Agent** — `search_documents` / `draft_finding` / `draft_risk` LangChain
  tools; `analyze()` runs one shared path for every real provider —
  `init_chat_model(model, provider).with_structured_output(AgentAnswer)` — with
  an offline, deterministic `mock` (default when no key is set, and forced by the
  test suite) that swaps the network call for generic gap-signal heuristics.
  `guard_citations()` drops any citation not tied to a real ingested chunk. A
  LangGraph state machine wires it together: `retrieve → analyze → draft → guard`.
- **Approvals** — the human gate: approve a finding, grade residual risk.
- **API** — FastAPI routes + static SPA.

---

## 7. Configuration

All config is environment variables — copy `.env.example` to `.env`. There is no
config file. Set **one** of `GEMINI_API_KEY`, `OPENAI_API_KEY`, or
`ANTHROPIC_API_KEY` and it is auto-selected; `ollama` needs no key
(`GRC_LLM_MODE=ollama`); with no key set it falls back to **`mock`** —
deterministic, $0, 100% reproducible. `GRC_LLM_MODE` forces a provider
regardless of keys present.

Every mode shares the same retrieval, drafting, human-gate, and citation guard
code paths — only how `AgentAnswer` gets produced changes.

| Env var | Purpose (default) |
|---------|-------------------|
| `GRC_LLM_MODE`         | `auto` (detect from key) \| `gemini` \| `openai` \| `anthropic` \| `ollama` \| `mock` |
| `GEMINI_MODEL` / `GRC_GEMINI_MODEL`       | `gemini-2.5-flash` |
| `OPENAI_MODEL` / `GRC_OPENAI_MODEL`       | `gpt-4o-mini` |
| `ANTHROPIC_MODEL` / `GRC_ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` |
| `OLLAMA_MODEL` / `GRC_OLLAMA_MODEL`       | `qwen3:8b` |
| `OLLAMA_URL` / `GRC_OLLAMA_URL`           | `http://localhost:11434` |
| `EMBEDDING_MODEL` / `GRC_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` (fastembed) |
| `CHUNK_SIZE` / `GRC_CHUNK_SIZE`           | `700` |
| `CHUNK_OVERLAP` / `GRC_CHUNK_OVERLAP`     | `100` |
| `GRC_DB`               | SQLite file path (default `data/grc.db`) |
| `GRC_CHROMA_DIR`       | Chroma persistence dir (default alongside `GRC_DB`) |

Examples:

```bash
# usual case: put GEMINI_API_KEY=... in .env, then just:
python -m uvicorn app.main:app --port 8000
# or override per-run:
GRC_LLM_MODE=openai OPENAI_API_KEY=sk-... python -m uvicorn app.main:app --port 8000
GRC_LLM_MODE=anthropic ANTHROPIC_API_KEY=sk-ant-... python -m uvicorn app.main:app --port 8000
GRC_LLM_MODE=ollama python -m uvicorn app.main:app --port 8000
```

---

## 8. API reference

| Method & path | Body | Returns |
|---------------|------|---------|
| `GET  /api/health` | – | resolved LLM `{mode, provider, model, api_keys_detected, dotenv_loaded}` |
| `POST /api/upload` | multipart `files[]` (PDF) | ingest summary + document list |
| `POST /api/chat` | `{"message": "..."}` | `{answer, citations[], warnings[], drafted{}}` |
| `GET  /api/approvals` | – | `{findings[], risks[]}` still `proposed` |
| `POST /api/approvals/finding/{id}/approve` | – | the updated finding |
| `POST /api/approvals/risk/{id}/grade` | `{"residual_score": "Medium", "mitigation": "..."}` | the graded risk |
| `GET  /api/registers` | – | `{documents[], findings[], risks[]}` |
| `POST /api/reset` | – | `{"ok": true}` — wipes SQLite + the vector store |

Interactive API docs are served at `http://localhost:8000/docs`. All request/response
contracts are unchanged from the pre-RAG version.

---

## 9. Project layout

```
.
├── app/
│   ├── main.py              entire backend: Config · Schemas · Store · Agent · Approvals · API
│   └── static/
│       ├── index.html       single-page UI
│       ├── style.css
│       └── app.js
├── sample_docs/             generated demo PDFs
├── data/grc.db              SQLite database (created at runtime)
├── data/chroma/             vector store persistence (created at runtime)
├── run.sh                   start / restart the server (macOS/Linux)
├── run.bat                  start / restart the server (Windows Command Prompt)
├── run.ps1                  start / restart the server (Windows PowerShell)
├── demo.py                  sample-PDF generator + CLI end-to-end demo
├── tests/test_v0.py         acceptance tests
├── .env / .env.example      configuration
└── requirements.txt
```

---

## 10. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Form data requires "python-multipart"` | `pip install -r requirements.txt` (it's included). |
| Chat says *"No documents have been ingested yet"* | Upload the PDFs first (Upload tab, or `python demo.py`). |
| Chat says *"Drafted / Already tracked FND-01"* but **Approvals is empty** | You already approved that finding/risk in an earlier session — a same-titled item is reused, not duplicated. It's now in **Registers** as `official`. Click **Reset demo** to start a fresh proposed cycle. |
| First upload is slow / needs internet | `fastembed` downloads the embedding model once and caches it locally; subsequent runs are offline and fast. |
| Chat answer says the model "could not be reached" | The key is wrong or has no quota/model access — the exact provider error is in the answer, the `warnings`, and the server console. Check `GET /api/health`. |
| Key is in `.env` but `GET /api/health` shows `mock` / `dotenv_loaded: false` | Fixed: `.env` is now loaded by absolute path. If still stale, confirm the file is at the repo root next to `app/` and restart the server. |
| Port 8000 in use | Add `--port 8001` and open that port. |
| Want a clean slate | Click **Reset demo**, or `curl -X POST localhost:8000/api/reset`, or delete `data/grc.db` and `data/chroma/`. |

---

## 11. Scope & limitations

This is a **prototype**, not production software:

- `mock` mode is a heuristic stand-in for a real LLM (used offline / in tests);
  switch to `openai`, `anthropic`, or `ollama` for genuine LLM reasoning.
- Single-file SQLite + local Chroma, no auth, no multi-user concurrency handling.
- Text-based PDFs only (no OCR for scanned documents).
