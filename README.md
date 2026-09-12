# 🛡️ Compliance & Risk Agent (GRC)

An intelligent, lightweight **Governance, Risk, and Compliance (GRC) Agent** powered by a semantic **RAG pipeline** and **LangGraph**. It ingests compliance policies, detects compliance gaps via semantic retrieval, generates structured findings and inherent risks, and enforces a **human-in-the-loop approval gate** before making records official.

Works **100% offline out-of-the-box** using deterministic mock mode ($0, no API key required), or connects seamlessly to **Gemini, OpenAI, Anthropic, or Ollama**.

---

## ⚡ First-Time Setup & Localhost Launch

Follow the quick steps below for your operating system to set up your virtual environment and launch the web app on `http://localhost:8000`.

### 🍎 macOS & Linux

#### Option A: One-Command Automated Setup (Recommended)
The included shell script handles `.env` creation, virtual environment setup, package installation, sample PDF generation, and server startup automatically:

```bash
chmod +x run.sh
./run.sh
```

#### Option B: Step-by-Step Manual Setup
If you prefer running commands manually in your terminal:

```bash
# 1. Create a Python 3.9+ virtual environment
python3 -m venv .venv

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Upgrade pip & install dependencies
pip install -r requirements.txt

# 4. (Optional) Set up environment variables
# Note: Runs in offline 'mock' mode by default. Add API keys if you want real LLM reasoning.
cp .env.example .env

# 5. Generate sample compliance PDFs
python -c "import demo; demo.make_sample_pdfs()"

# 6. Launch the server
python app.py
# or: uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

---

### 🪟 Windows

#### Option A: One-Click / Script Setup (Recommended)
Open Command Prompt or PowerShell, navigate to the folder, and run:

- **Command Prompt (CMD):**
  ```cmd
  run.bat
  ```
- **PowerShell:**
  ```powershell
  .\run.ps1
  ```
*(You can also simply double-click `run.bat` in Windows File Explorer).*

#### Option B: Step-by-Step Manual Setup

**Using Windows Command Prompt (CMD):**
```cmd
:: 1. Create virtual environment
python -m venv .venv

:: 2. Activate virtual environment
.venv\Scripts\activate

:: 3. Install dependencies
pip install -r requirements.txt

:: 4. (Optional) Copy environment template
copy .env.example .env

:: 5. Generate sample compliance PDFs
python -c "import demo; demo.make_sample_pdfs()"

:: 6. Launch the server
python app.py
```

**Using Windows PowerShell:**
```powershell
# 1. Create virtual environment
python -m venv .venv

# 2. Activate virtual environment
# (If script execution is disabled, run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass)
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Copy environment template
Copy-Item .env.example .env

# 5. Generate sample compliance PDFs
python -c "import demo; demo.make_sample_pdfs()"

# 6. Launch the server
python app.py
```

---

### 🌐 Accessing on Localhost

Once the server starts, open your browser and access:

| Destination | URL | Description |
|---|---|---|
| **Web UI** | [http://localhost:8000](http://localhost:8000) | Single Page Application (Upload, Chat, Approvals, Registers) |
| **API Docs (Swagger)** | [http://localhost:8000/docs](http://localhost:8000/docs) | Interactive REST API documentation |
| **API Health Check** | [http://localhost:8000/api/health](http://localhost:8000/api/health) | Current active LLM provider, model, and status |

> **Custom Port:**
> - macOS/Linux: `./run.sh 8080` or `PORT=8080 python app.py`
> - Windows: `run.bat 8080` or `set PORT=8080 && python app.py`

---

## 🧭 How to Use the Web Application

The single-page web UI provides an intuitive 4-step workflow:

```
[1. Upload Docs] ➔ [2. Chat & Audit] ➔ [3. Review & Grade] ➔ [4. Live Registers]
```

1. **📁 Upload Tab:**
   - Click **Browse**, select the 3 generated sample PDFs in `sample_docs/` (or upload your own compliance PDFs), and click **Ingest Documents**.
2. **💬 Chat Tab:**
   - Ask compliance assessment questions, e.g.:
     > *"Can we onboard an EU customer under GDPR based on our uploaded docs?"*
   - The agent retrieves the relevant chunks, produces an evidence-backed answer with strict citation verification, and automatically drafts a **Finding** and **Risk** in `proposed` status.
3. **📋 Approvals Tab (Human-in-the-loop):**
   - Review drafted findings and risks.
   - Enter your mitigation strategy (e.g. *"Execute Standard Contractual Clauses (SCCs)"*), choose a **Residual Risk** grade (`Low`, `Medium`, or `High`), and click **Grade & Approve**.
4. **🗂️ Registers Tab:**
   - View official system-of-record registers: Ingested Documents, Approved Findings, and Graded Risks.
5. **🔄 Reset Demo:**
   - Use the **Reset Demo** button (top-right) at any time to clear SQLite records and vector index for a fresh start.

---

## 💻 Terminal / CLI Headless Demo

You can run the entire lifecycle in the terminal without opening a browser:

```bash
python demo.py
```
This script generates the sample documents, uploads them, sends the test query, simulates agent drafting, executes the human approval step, and prints the formatted JSON outputs at each step.

---

## 🧪 Running the Tests

Run the automated acceptance test suite using pytest:

```bash
# macOS/Linux:
pytest -q

# Windows:
pytest -q

# Or explicitly via the virtual environment:
.venv/bin/pytest -q          # macOS/Linux
.venv\Scripts\pytest.exe -q  # Windows
```

Tests run against an isolated temporary SQLite database and local Chroma directory in offline `mock` mode (no API keys consumed).

---

## 🏗️ Architecture & Pipeline

The backend is contained in **`app.py`**, structured into six modular components:

```
┌────────────────────────────────────────────────────────────┐
│      Static SPA (Upload · Chat · Approvals · Registers)    │
└───────────────────────────┬────────────────────────────────┘
                            │  REST / JSON
┌───────────────────────────▼────────────────────────────────┐
│                    FastAPI Backend (app.py)                │
│  /api/upload   /api/chat   /api/approvals   /api/registers  │
└─────────────┬──────────────┬────────────────────┬───────────┘
              │              │                    │
   ┌──────────▼──────┐ ┌─────▼───────────┐ ┌──────▼──────────────┐
   │  SQLite grc.db   │ │ ChromaDB +      │ │ LangGraph Agent     │
   │  - Documents     │ │ FastEmbed       │ │ retrieve -> analyze │
   │  - Findings      │ │ (bge-small-en)  │ │ -> draft -> guard   │
   │  - Risks         │ │                 │ │                     │
   └──────────────────┘ └─────────────────┘ └──────────┬───────────┘
                                                       │
                                   LangChain init_chat_model
                                   (Gemini / OpenAI / Anthropic / Ollama / Mock)
                                   ➔ Structured Pydantic AgentAnswer
```

1. **Config:** Loads `.env`, auto-detects LLM provider, and sets model parameters.
2. **Schemas:** Defines Pydantic validation contracts (`AgentAnswer`, `Finding`, `Risk`).
3. **Store:** Text extraction via `pypdf`, chunk storage in SQLite, and semantic vector embeddings via `fastembed` (`BAAI/bge-small-en-v1.5`) in `ChromaDB`.
4. **Agent:** LangGraph state machine orchestrating semantic retrieval, LLM analysis, draft synthesis, and citation verification guardrails.
5. **Approvals:** Human gate transitions items from `proposed` to `official`.
6. **API:** FastAPI endpoints and static file serving.

---

## ⚙️ Configuration & LLM Providers

All configuration is driven by environment variables via `.env`.

### Supported LLM Providers

The app automatically selects the provider based on which API key is present in `.env`:

| Provider | Setup in `.env` | Default Model |
|---|---|---|
| **Offline Mock** *(Default)* | No key required | Heuristic rule-based simulator ($0, offline) |
| **Google Gemini** | `GEMINI_API_KEY=your_key` | `gemini-2.5-flash` |
| **OpenAI** | `OPENAI_API_KEY=your_key` | `gpt-4o-mini` |
| **Anthropic** | `ANTHROPIC_API_KEY=your_key` | `claude-haiku-4-5-20251001` |
| **Ollama (Local)** | `GRC_LLM_MODE=ollama` | `qwen3:8b` (at `http://localhost:11434`) |

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GRC_LLM_MODE` | Provider override (`auto`, `gemini`, `openai`, `anthropic`, `ollama`, `mock`) | `auto` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini API key | – |
| `OPENAI_API_KEY` | OpenAI API key | – |
| `ANTHROPIC_API_KEY` | Anthropic API key | – |
| `GEMINI_MODEL` | Gemini model name | `gemini-2.5-flash` |
| `OPENAI_MODEL` | OpenAI model name | `gpt-4o-mini` |
| `ANTHROPIC_MODEL` | Anthropic model name | `claude-haiku-4-5-20251001` |
| `OLLAMA_MODEL` | Ollama model name | `qwen3:8b` |
| `OLLAMA_URL` | Ollama API endpoint | `http://localhost:11434` |
| `CHUNK_SIZE` | Text chunk character limit | `700` |
| `CHUNK_OVERLAP` | Overlap character count between chunks | `100` |
| `GRC_DB` | SQLite database file location | `data/grc.db` |
| `GRC_CHROMA_DIR` | Chroma persistence directory | `data/chroma/` |

---

## 📡 REST API Reference

| Method & Route | Request Body | Description |
|---|---|---|
| `GET /api/health` | – | Returns active LLM mode, provider, model, and `.env` status |
| `POST /api/upload` | `multipart/form-data` (`files[]`) | Ingests PDF documents, chunks text, and builds vector index |
| `POST /api/chat` | `{"message": "..."}` | Runs agent audit query; returns answer, citations, and drafts |
| `GET /api/approvals` | – | Lists all `proposed` findings and risks awaiting approval |
| `POST /api/approvals/finding/{id}/approve` | – | Approves a drafted finding |
| `POST /api/approvals/risk/{id}/grade` | `{"residual_score": "...", "mitigation": "..."}` | Assigns residual score and approves risk |
| `GET /api/registers` | – | Returns all official documents, findings, and risks |
| `POST /api/reset` | – | Wipes SQLite database and vector store for clean restart |

---

## 📁 Project Structure

```
.
├── app.py                   # Complete backend: Config, Schemas, Vector Store, Agent, API
├── static/                  # Frontend SPA assets
│   ├── index.html           # UI structure (Upload, Chat, Approvals, Registers)
│   ├── style.css            # Modern styling & responsive layout
│   └── app.js               # Frontend API client & reactive view logic
├── sample_docs/             # Synthetic compliance sample PDFs
│   ├── 1_GDPR_Art28_DPA_Requirements.pdf
│   ├── 2_Acme_Security_and_Backup_Policy.pdf
│   └── 3_EU_Customer_Onboarding_Assessment.pdf
├── data/                    # Generated at runtime (git-ignored)
│   ├── grc.db               # SQLite system of record
│   └── chroma/              # ChromaDB vector index
├── demo.py                  # Sample PDF generator & CLI end-to-end demo
├── run.sh                   # Startup / restart script for macOS & Linux
├── run.bat                  # Startup / restart script for Windows CMD
├── run.ps1                  # Startup / restart script for Windows PowerShell
├── tests/
│   └── test_v0.py           # Acceptance test suite (pytest)
├── requirements.txt         # Project dependencies
├── .env.example             # Environment template
└── README.md                # Documentation & quick start guide
```

---

## ❓ Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| **Port 8000 already in use** | An existing server instance is running on port 8000 | Run `./run.sh 8080` (macOS/Linux) or `run.bat 8080` (Windows) to use another port, or let `run.sh` / `run.bat` automatically terminate the existing process. |
| **PowerShell script execution disabled** | Windows security policy restricts `.ps1` execution | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in PowerShell, or use `run.bat`. |
| **First upload or start takes time** | FastEmbed downloading embedding model | FastEmbed downloads `BAAI/bge-small-en-v1.5` (~130MB) once on first run. It is cached locally for all subsequent runs. |
| **Chat shows offline mock answer** | API key is missing or not detected | Ensure `.env` exists in the root folder with a valid key (e.g. `GEMINI_API_KEY=...`), then verify via `http://localhost:8000/api/health`. |
| **Want to start over completely** | Need a clean database and vector store | Click **Reset Demo** in the top navigation bar of the web UI, or run `curl -X POST http://localhost:8000/api/reset`. |
