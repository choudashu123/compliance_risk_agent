"""Compliance & Risk Agent — the whole backend in one module.

Flow: upload PDFs -> semantic retrieval (Chroma + fastembed) -> an LLM returns a
structured Pydantic ``AgentAnswer`` -> auto-draft one Finding + one Risk ->
citation guard -> a human approves the finding and grades the residual risk.

Sections, in order:
  1. Config     — env-driven settings; LLM provider auto-detected from the .env key
  2. Schemas    — the AgentAnswer structured-output contract
  3. Store      — SQLite system-of-record + Chroma vector index + PDF ingest
  4. Agent      — LangChain tools + LangGraph graph (retrieve -> analyze -> draft -> guard)
  5. Approvals  — human-in-the-loop gate
  6. API        — FastAPI app + static SPA
"""
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
import warnings
from contextlib import contextmanager
from functools import lru_cache
from typing import List, Literal, Optional

# langchain-openai's structured-output wrapper triggers a benign pydantic
# serializer warning about its internal `parsed` field; the returned data is
# correct. Silence just that one so real warnings stay visible.
warnings.filterwarnings("ignore", message=r"Pydantic serializer warnings",
                        category=UserWarning, module=r"pydantic\..*")

import chromadb
from chromadb import EmbeddingFunction
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastembed import TextEmbedding
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from pypdf import PdfReader
from starlette.responses import Response
from typing_extensions import TypedDict

# =============================================================================
# 1. Config  —  drop ONE provider key into .env and that provider is used
# =============================================================================
log = logging.getLogger("grc")

_ROOT = os.path.dirname(os.path.abspath(__file__))

# Load .env by ABSOLUTE path so it is found no matter what directory the server
# (or a test, IDE run config, systemd unit, …) is started from. `load_dotenv()`
# with no argument only searches upward from the current working directory, which
# is the usual reason a key in .env silently fails to take effect.
try:
    from dotenv import load_dotenv

    _dotenv_path = os.path.join(_ROOT, ".env")
    _dotenv_loaded = load_dotenv(_dotenv_path)
except ImportError:  # optional; real environment variables still work
    _dotenv_path, _dotenv_loaded = None, False


def _env(*names: str, default: Optional[str] = None) -> Optional[str]:
    """First non-empty value among the given env var names, else ``default``."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


GEMINI_API_KEY = _env("GEMINI_API_KEY", "GOOGLE_API_KEY")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")

# Each LangChain integration reads its own canonical var — mirror ours into them,
# and remove empty keys from os.environ so SDKs do not fail trying to use empty strings.
for _canonical, _value in (
    ("GOOGLE_API_KEY", GEMINI_API_KEY),
    ("OPENAI_API_KEY", OPENAI_API_KEY),
    ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
):
    if _value:
        os.environ[_canonical] = _value
    elif _canonical in os.environ and not os.environ[_canonical].strip():
        del os.environ[_canonical]

GEMINI_MODEL = _env("GEMINI_MODEL", "GRC_GEMINI_MODEL", default="gemini-2.5-flash")
OPENAI_MODEL = _env("OPENAI_MODEL", "GRC_OPENAI_MODEL", default="gpt-4o-mini")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "GRC_ANTHROPIC_MODEL", default="claude-haiku-4-5-20251001")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "GRC_OLLAMA_MODEL", default="qwen3:8b")
OLLAMA_URL = _env("OLLAMA_URL", "GRC_OLLAMA_URL", default="http://localhost:11434")


def _detect_mode() -> str:
    explicit = os.getenv("GRC_LLM_MODE")
    if explicit and explicit != "auto":
        return explicit
    if GEMINI_API_KEY:
        return "gemini"
    if OPENAI_API_KEY:
        return "openai"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    return "mock"


LLM_MODE = _detect_mode()
# our mode -> (LangChain provider id, model id) for init_chat_model()
_PROVIDER_MAP = {
    "gemini": ("google_genai", GEMINI_MODEL),
    "openai": ("openai", OPENAI_MODEL),
    "anthropic": ("anthropic", ANTHROPIC_MODEL),
    "ollama": ("ollama", OLLAMA_MODEL),
    "mock": ("mock", "mock"),
}
LLM_PROVIDER, LLM_MODEL = _PROVIDER_MAP.get(LLM_MODE, ("mock", "mock"))
LLM_MODEL_KWARGS: dict = {} if LLM_MODE in ("mock", "ollama") else {"temperature": 0}
if LLM_MODE == "ollama":
    LLM_MODEL_KWARGS["base_url"] = OLLAMA_URL


def llm_status() -> dict:
    """What provider will actually be used, and why — surfaced at startup and at
    GET /api/health so a misconfigured key is obvious instead of silent."""
    keys = [n for n, v in (("gemini", GEMINI_API_KEY), ("openai", OPENAI_API_KEY),
                            ("anthropic", ANTHROPIC_API_KEY)) if v]
    return {
        "mode": LLM_MODE,
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "forced_by_GRC_LLM_MODE": bool(os.getenv("GRC_LLM_MODE") and os.getenv("GRC_LLM_MODE") != "auto"),
        "api_keys_detected": keys,
        "dotenv_path": _dotenv_path,
        "dotenv_loaded": _dotenv_loaded,
    }


_status = llm_status()
# Printed to stderr so it always shows in the server console regardless of how
# logging is configured — the fastest way to see what the server will actually do.
if _status["mode"] == "mock" and not _status["forced_by_GRC_LLM_MODE"]:
    print(
        f"[grc] LLM: no API key found (looked in {_dotenv_path}, loaded={_dotenv_loaded}) — "
        f"using the offline 'mock' analyzer. Put GEMINI_API_KEY / OPENAI_API_KEY / "
        f"ANTHROPIC_API_KEY in that .env file for real reasoning.",
        file=sys.stderr,
    )
else:
    print(
        f"[grc] LLM: mode={_status['mode']} provider={_status['provider']} "
        f"model={_status['model']} keys={_status['api_keys_detected'] or 'none'} "
        f"(.env loaded={_dotenv_loaded})",
        file=sys.stderr,
    )

EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "GRC_EMBEDDING_MODEL", default="BAAI/bge-small-en-v1.5")
CHUNK_SIZE = int(_env("CHUNK_SIZE", "GRC_CHUNK_SIZE", default="700"))
CHUNK_OVERLAP = int(_env("CHUNK_OVERLAP", "GRC_CHUNK_OVERLAP", default="100"))

DB_PATH = os.getenv("GRC_DB", os.path.join(_ROOT, "data", "grc.db"))
CHROMA_DIR = os.getenv("GRC_CHROMA_DIR", os.path.join(os.path.dirname(DB_PATH), "chroma"))
SAMPLE_DOCS = os.path.join(_ROOT, "sample_docs")
STATIC_DIR = os.path.join(_ROOT, "app", "static") if os.path.isdir(os.path.join(_ROOT, "app", "static")) else os.path.join(_ROOT, "static")


# =============================================================================
# 2. Schemas  —  what the LLM must return for every compliance question
# =============================================================================
class Citation(BaseModel):
    filename: str
    chunk_id: int


class FindingDraft(BaseModel):
    title: str
    description: str


class RiskDraft(BaseModel):
    title: str
    description: str
    inherent_score: Literal["Low", "Medium", "High"]


class Drafts(BaseModel):
    finding: FindingDraft
    risk: RiskDraft


class AgentAnswer(BaseModel):
    answer: str = Field(..., description="Answer grounded ONLY in the provided evidence chunks.")
    citations: List[Citation] = Field(
        default_factory=list,
        description="Evidence used. filename/chunk_id MUST come from the provided chunks — never invent one.",
    )
    has_gap: bool = Field(
        ..., description="True if the evidence reveals a compliance gap, deficiency, or unmitigated risk worth tracking."
    )
    drafts: Optional[Drafts] = Field(
        None, description="A Finding + Risk to draft. Present only when has_gap is true; never set a residual score.",
    )


# =============================================================================
# 3. Store  —  SQLite is the system-of-record; Chroma only holds embeddings
# =============================================================================
_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT UNIQUE,
    uploaded_at  REAL,
    n_chunks     INTEGER
);
CREATE TABLE IF NOT EXISTS doc_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER,
    filename     TEXT,
    chunk_index  INTEGER,
    text         TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id           TEXT PRIMARY KEY,
    title        TEXT,
    description  TEXT,
    citations    TEXT,          -- JSON list of {filename, chunk_id}
    status       TEXT,          -- proposed | official
    created_at   REAL,
    approved_at  REAL
);
CREATE TABLE IF NOT EXISTS risks (
    id             TEXT PRIMARY KEY,
    title          TEXT,
    description    TEXT,
    inherent_score TEXT,        -- Low | Medium | High
    residual_score TEXT,        -- NULL until a human grades it
    mitigation     TEXT,
    finding_id     TEXT,
    status         TEXT,        -- proposed | official
    created_at     REAL,
    approved_at    REAL
);
"""


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as c:
        c.executescript(_SCHEMA)


def reset_db():
    with _conn() as c:
        for t in ("documents", "doc_chunks", "findings", "risks"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('documents','doc_chunks')")


# --- documents / chunks --------------------------------------------------
def add_document(filename, chunks):
    """Insert a document and its chunks. Returns (doc_id, chunk_rows) where
    chunk_rows = [{id, chunk_index, text}, ...] in input order — the caller needs
    those ids to index the chunks into the vector store."""
    with _conn() as c:
        c.execute("DELETE FROM doc_chunks WHERE filename=?", (filename,))
        c.execute("DELETE FROM documents WHERE filename=?", (filename,))
        cur = c.execute(
            "INSERT INTO documents(filename, uploaded_at, n_chunks) VALUES (?,?,?)",
            (filename, time.time(), len(chunks)),
        )
        doc_id = cur.lastrowid
        rows = []
        for i, t in enumerate(chunks):
            cur2 = c.execute(
                "INSERT INTO doc_chunks(doc_id, filename, chunk_index, text) VALUES (?,?,?,?)",
                (doc_id, filename, i, t),
            )
            rows.append({"id": cur2.lastrowid, "chunk_index": i, "text": t})
    return doc_id, rows


def get_chunks_by_ids(ids):
    """Fetch chunks by id, preserving the order of `ids` (similarity rank)."""
    if not ids:
        return []
    with _conn() as c:
        placeholders = ",".join("?" * len(ids))
        rows = {
            r["id"]: dict(r)
            for r in c.execute(f"SELECT * FROM doc_chunks WHERE id IN ({placeholders})", ids)
        }
    return [rows[i] for i in ids if i in rows]


def list_documents():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM documents ORDER BY id")]


def all_chunks():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM doc_chunks ORDER BY id")]


def chunk_exists(chunk_id):
    with _conn() as c:
        return c.execute("SELECT 1 FROM doc_chunks WHERE id=?", (chunk_id,)).fetchone() is not None


def document_exists(filename):
    with _conn() as c:
        return c.execute("SELECT 1 FROM documents WHERE filename=?", (filename,)).fetchone() is not None


# --- findings / risks --------------------------------------------------
def _next_id(table, prefix):
    with _conn() as c:
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return f"{prefix}-{n + 1:02d}"


def _finding_by_title(title):
    with _conn() as c:
        r = c.execute("SELECT * FROM findings WHERE title=?", (title,)).fetchone()
        return dict(r) if r else None


def create_finding(title, description, citations):
    existing = _finding_by_title(title)
    if existing:
        return existing["id"]
    fid = _next_id("findings", "FND")
    with _conn() as c:
        c.execute(
            "INSERT INTO findings(id,title,description,citations,status,created_at) VALUES (?,?,?,?,?,?)",
            (fid, title, description, json.dumps(citations), "proposed", time.time()),
        )
    return fid


def _risk_by_title(title):
    with _conn() as c:
        r = c.execute("SELECT * FROM risks WHERE title=?", (title,)).fetchone()
        return dict(r) if r else None


def create_risk(title, description, inherent_score, finding_id):
    existing = _risk_by_title(title)
    if existing:
        return existing["id"]
    rid = _next_id("risks", "RSK")
    with _conn() as c:
        c.execute(
            "INSERT INTO risks(id,title,description,inherent_score,residual_score,finding_id,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (rid, title, description, inherent_score, None, finding_id, "proposed", time.time()),
        )
    return rid


def get_finding(fid):
    with _conn() as c:
        r = c.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
        return dict(r) if r else None


def get_risk(rid):
    with _conn() as c:
        r = c.execute("SELECT * FROM risks WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def list_findings():
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM findings ORDER BY id")]
    for r in rows:
        r["citations"] = json.loads(r["citations"] or "[]")
    return rows


def list_risks():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM risks ORDER BY id")]


def _set_finding_official(fid):
    with _conn() as c:
        c.execute("UPDATE findings SET status='official', approved_at=? WHERE id=?", (time.time(), fid))


def _set_risk_graded(rid, residual_score, mitigation):
    with _conn() as c:
        c.execute(
            "UPDATE risks SET residual_score=?, mitigation=?, status='official', approved_at=? WHERE id=?",
            (residual_score, mitigation, time.time(), rid),
        )


# --- vector index: ChromaDB (persistent) + fastembed --------------------
_VS_COLLECTION_NAME = "doc_chunks"
_vs_embedder = None
_vs_client = None
_vs_collection = None


class _FastEmbedFunction(EmbeddingFunction):
    """Chroma embedding function backed by a local fastembed ONNX model."""

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        self.model_name = model_name

    def __call__(self, input):
        global _vs_embedder
        if _vs_embedder is None:
            try:
                _vs_embedder = TextEmbedding(self.model_name)
            except Exception as e:
                log.error("Failed to initialize FastEmbed model %s: %s", self.model_name, e)
                raise RuntimeError(
                    f"Failed to load embedding model '{self.model_name}'. "
                    "Ensure internet access to Hugging Face or pre-download the model."
                ) from e
        return [vec.tolist() for vec in _vs_embedder.embed(list(input))]

    @staticmethod
    def name():
        return "fastembed"

    def get_config(self):
        return {"model_name": self.model_name}

    @staticmethod
    def build_from_config(config):
        return _FastEmbedFunction(model_name=config["model_name"])


def _vs():
    global _vs_client, _vs_collection
    if _vs_collection is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        _vs_client = chromadb.PersistentClient(path=CHROMA_DIR)
        _vs_collection = _vs_client.get_or_create_collection(
            _VS_COLLECTION_NAME, embedding_function=_FastEmbedFunction()
        )
    return _vs_collection


def index_chunks(filename: str, rows: list):
    """(Re)index all chunks for `filename`. `rows` = [{id, chunk_index, text}, ...]."""
    col = _vs()
    col.delete(where={"filename": filename})
    if not rows:
        return
    col.add(
        ids=[str(r["id"]) for r in rows],
        documents=[r["text"] for r in rows],
        metadatas=[{"filename": filename, "chunk_index": r["chunk_index"]} for r in rows],
    )


def query_similar(query: str, n_results: int = 6) -> list:
    """Chunk ids ranked by semantic similarity to `query` (best first)."""
    col = _vs()
    count = col.count()
    if count == 0:
        return []
    res = col.query(query_texts=[query], n_results=min(n_results, count))
    return [int(i) for i in res["ids"][0]]


def vector_reset():
    """Drop and recreate the Chroma collection (used by /api/reset)."""
    global _vs_client, _vs_collection
    if _vs_client is None:
        _vs()
    try:
        _vs_client.delete_collection(_VS_COLLECTION_NAME)
    except Exception:
        pass
    _vs_collection = None
    _vs()


# --- PDF ingest --------------------------------------------------------
def extract_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def chunk_text(text: str, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = " ".join(text.split())
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def ingest_pdf(filename: str, data: bytes) -> dict:
    text = extract_text(data)
    chunks = chunk_text(text)
    _doc_id, rows = add_document(filename, chunks)
    index_chunks(filename, rows)  # embed + store alongside SQLite
    return {"filename": filename, "n_chunks": len(chunks), "chars": len(text)}


# =============================================================================
# 4. Agent  —  LangChain tools + LangGraph: retrieve -> analyze -> draft -> guard
# =============================================================================
SYSTEM_PROMPT = (
    "You are a compliance and risk analyst. You are given a set of evidence chunks "
    "retrieved from the organization's uploaded policy/regulatory/assessment documents. "
    "Answer the user's question using ONLY those chunks — never invent facts or documents. "
    "Cite the exact chunk_id(s) you relied on.\n"
    "Decide has_gap: if the evidence shows a compliance gap, deficiency, missing control, "
    "or unmitigated risk (e.g. a required agreement not executed, a control not in place), "
    "set has_gap=true; otherwise false. Be consistent — if your answer text says something "
    "is missing, unmet, or blocking, then has_gap MUST be true.\n"
    "When has_gap=true you MUST populate drafts with exactly one Finding and one Risk, "
    "the Risk's inherent_score being High/Medium/Low by severity. Never assign a residual "
    "risk score — that is reserved for a human reviewer. When has_gap=false, omit drafts."
)


def _build_prompt(question: str, chunks: list) -> str:
    context = "\n\n".join(
        f"[chunk_id={c['id']} file={c['filename']}]\n{c['text']}" for c in chunks
    )
    return f"Evidence chunks:\n{context}\n\nQuestion: {question}"


@lru_cache(maxsize=1)
def _structured_model():
    """Build the chat model once and bind our Pydantic output schema to it."""
    from langchain.chat_models import init_chat_model

    model = init_chat_model(LLM_MODEL, model_provider=LLM_PROVIDER, **LLM_MODEL_KWARGS)
    return model.with_structured_output(AgentAnswer)


def _llm_analyze(question: str, chunks: list) -> AgentAnswer:
    return _structured_model().invoke(
        [("system", SYSTEM_PROMPT), ("human", _build_prompt(question, chunks))]
    )


# Ordered most-specific-first so a strong deficiency phrase always wins over a
# weak generic word like "gap" that might just appear in a section heading.
_GAP_SIGNALS = (
    "not executed", "has not", "have not", "has failed to", "failure to",
    "no signed", "not been executed", "no dpa", "does not have", "lacks",
    "non-compliant", "deficient", "missing", "not in place", "unresolved", "gap",
)


def _find_gap(chunks):
    """Return (chunk, sentence) for the first, most-specific gap signal found — or
    (None, None) if none match."""
    for signal in _GAP_SIGNALS:
        for c in chunks:
            for sentence in re.split(r"(?<=[.!?])\s+", c["text"]):
                if signal in sentence.lower():
                    return c, sentence.strip()
    return None, None


def _mock_analyze(question: str, chunks: list) -> AgentAnswer:
    """Deterministic, offline stand-in for a real LLM call — used when no API key
    is configured so the demo and tests never need a key or network. Reasons
    generically over whatever chunks vector search returned."""
    top = chunks[:4]
    citations = [Citation(filename=c["filename"], chunk_id=c["id"]) for c in top[:3]]

    gap_chunk, sentence = _find_gap(top)
    if gap_chunk:
        answer = (
            f"The evidence points to an open compliance gap: {sentence} "
            f"(source: {gap_chunk['filename']}#{gap_chunk['id']})."
        )
        drafts = Drafts(
            finding=FindingDraft(
                title=f"Compliance gap identified in {gap_chunk['filename']}",
                description=sentence,
            ),
            risk=RiskDraft(
                title=f"Risk arising from gap in {gap_chunk['filename']}",
                description=f"An unremediated gap may expose the organization to regulatory or "
                            f"operational risk: {sentence}",
                inherent_score="High",
            ),
        )
        return AgentAnswer(answer=answer, citations=citations, has_gap=True, drafts=drafts)

    if not top:
        return AgentAnswer(answer="No relevant documents were found for this question.",
                            citations=[], has_gap=False, drafts=None)

    snippet = " ".join(top[0]["text"].split()[:60])
    answer = f"Based on the uploaded documents: {snippet}… (source: {top[0]['filename']})"
    return AgentAnswer(answer=answer, citations=citations, has_gap=False, drafts=None)


def _to_state(result: AgentAnswer) -> dict:
    drafts = None
    if result.has_gap and result.drafts:
        drafts = {
            "finding": {
                "title": result.drafts.finding.title,
                "description": result.drafts.finding.description,
                "citations": [c.model_dump() for c in result.citations],
            },
            "risk": {
                "title": result.drafts.risk.title,
                "description": result.drafts.risk.description,
                "inherent_score": result.drafts.risk.inherent_score,
            },
        }
    return {
        "answer": result.answer,
        "citations": [c.model_dump() for c in result.citations],
        "drafts": drafts,
    }


def analyze(question: str, chunks: list) -> dict:
    """RAG reasoning step: chunks -> structured AgentAnswer -> graph state dict.

    A failure in the LLM call (bad/missing key, no quota, rate limit, network,
    missing provider package) is caught here and returned as a normal answer with
    a warning, so /api/chat stays HTTP 200 and the UI shows *why* it failed
    instead of a blank "undefined" bubble."""
    if not chunks:
        return {
            "answer": "No documents have been ingested yet. Upload the sample PDFs first.",
            "citations": [],
            "drafts": None,
        }
    if LLM_PROVIDER == "mock":
        return _to_state(_mock_analyze(question, chunks))
    try:
        return _to_state(_llm_analyze(question, chunks))
    except Exception as e:  # noqa: BLE001 — surface any provider failure to the user
        log.exception("LLM call failed (mode=%s model=%s)", LLM_MODE, LLM_MODEL)
        return {
            "answer": (
                f"The {LLM_MODE} model ({LLM_MODEL}) could not be reached, so no analysis was produced. "
                f"Check the API key in {_dotenv_path} and the account's quota/access. "
                f"Details: {type(e).__name__}: {e}"
            ),
            "citations": [],
            "drafts": None,
            "error": f"{type(e).__name__}: {e}",
        }


# --- LangChain tools the agent can call --------------------------------
@tool
def search_documents(query: str) -> list:
    """Semantic search over ingested PDF chunks (fastembed + ChromaDB). Returns a
    ranked list of {id, filename, chunk_index, text}, best match first."""
    return get_chunks_by_ids(query_similar(query, n_results=6))


@tool
def draft_finding(title: str, description: str, citations: list) -> str:
    """Create a compliance finding in 'proposed' status. Returns its id (e.g. FND-01)."""
    return create_finding(title, description, citations)


@tool
def draft_risk(title: str, description: str, inherent_score: str, finding_id: str = "") -> str:
    """Create a risk in 'proposed' status with an inherent score but NO residual score
    (residual grading is reserved for a human). Returns its id (e.g. RSK-01)."""
    return create_risk(title, description, inherent_score, finding_id or None)


# --- citation guardrail ----------------------------------------------
def guard_citations(citations: list) -> dict:
    """Every citation must point at a real ingested doc / chunk; drop the rest."""
    clean, warnings = [], []
    for c in citations or []:
        fn, cid = c.get("filename"), c.get("chunk_id")
        if not document_exists(fn):
            warnings.append(f"Dropped citation to unknown document '{fn}'.")
            continue
        if cid is not None and not chunk_exists(cid):
            warnings.append(f"Dropped citation with fabricated chunk id {cid} ('{fn}').")
            continue
        clean.append(c)
    if citations and not clean:
        warnings.append("All citations were rejected — answer is unsupported.")
    return {"ok": bool(clean) or not citations, "citations": clean, "warnings": warnings}


# --- LangGraph orchestrator -----------------------------------------
class AgentState(TypedDict, total=False):
    question: str
    chunks: list
    answer: str
    citations: list
    drafts: Optional[dict]
    drafted: dict
    warnings: list
    error: Optional[str]


def _node_retrieve(state: AgentState) -> AgentState:
    return {"chunks": search_documents.invoke({"query": state["question"]})}


def _node_analyze(state: AgentState) -> AgentState:
    return analyze(state["question"], state["chunks"])


def _node_draft(state: AgentState) -> AgentState:
    drafts = state.get("drafts")
    if not drafts:
        return {"drafted": {}}
    # A Finding/Risk with the same title is reused rather than duplicated. Detect
    # that up front so the UI can say "already tracked" instead of "drafted" —
    # otherwise a repeat question looks like it silently did nothing.
    pre_finding = _finding_by_title(drafts["finding"]["title"])
    pre_risk = _risk_by_title(drafts["risk"]["title"])
    fid = draft_finding.invoke({
        "title": drafts["finding"]["title"],
        "description": drafts["finding"]["description"],
        "citations": drafts["finding"]["citations"],
    })
    rid = draft_risk.invoke({
        "title": drafts["risk"]["title"],
        "description": drafts["risk"]["description"],
        "inherent_score": drafts["risk"]["inherent_score"],
        "finding_id": fid,
    })
    f, r = get_finding(fid), get_risk(rid)
    return {"drafted": {
        "finding_id": fid,
        "risk_id": rid,
        "finding_status": (f or {}).get("status"),
        "risk_status": (r or {}).get("status"),
        "reused": bool(pre_finding or pre_risk),
    }}


def _node_guard(state: AgentState) -> AgentState:
    res = guard_citations(state.get("citations", []))
    return {"citations": res["citations"], "warnings": res["warnings"]}


def _build_graph():
    g = StateGraph(AgentState)
    g.add_node("retrieve", _node_retrieve)
    g.add_node("analyze", _node_analyze)
    g.add_node("draft", _node_draft)
    g.add_node("guard", _node_guard)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "analyze")
    g.add_edge("analyze", "draft")
    g.add_edge("draft", "guard")
    g.add_edge("guard", END)
    return g.compile()


_GRAPH = _build_graph()


def run_agent(question: str) -> dict:
    out = _GRAPH.invoke({"question": question})
    warnings = list(out.get("warnings", []))
    if out.get("error"):
        warnings.append(f"LLM error: {out['error']}")
    return {
        "answer": out.get("answer", ""),
        "citations": out.get("citations", []),
        "warnings": warnings,
        "drafted": out.get("drafted", {}),
    }


# =============================================================================
# 5. Approvals  —  human-in-the-loop gate
# =============================================================================
VALID_SCORES = {"Low", "Medium", "High"}


def pending_approvals() -> dict:
    return {
        "findings": [f for f in list_findings() if f["status"] == "proposed"],
        "risks": [r for r in list_risks() if r["status"] == "proposed"],
    }


def approve_finding(fid: str) -> dict:
    if not get_finding(fid):
        raise KeyError(f"unknown finding {fid}")
    _set_finding_official(fid)
    return get_finding(fid)


def grade_risk(rid: str, residual_score: str, mitigation: str = "") -> dict:
    if not get_risk(rid):
        raise KeyError(f"unknown risk {rid}")
    if residual_score not in VALID_SCORES:
        raise ValueError(f"residual_score must be one of {sorted(VALID_SCORES)}")
    _set_risk_graded(rid, residual_score, mitigation)
    return get_risk(rid)


# =============================================================================
# 6. API  —  FastAPI + static SPA
# =============================================================================
class _NoCacheStatic(StaticFiles):
    """Serve static assets with no caching — handy while iterating on the demo UI."""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store"
        return resp


app = FastAPI(title="Compliance & Risk Agent")
init_db()


class ChatIn(BaseModel):
    message: str


class GradeIn(BaseModel):
    residual_score: str
    mitigation: str = ""


@app.post("/api/upload")
async def upload(files: list[UploadFile]):
    if not files:
        raise HTTPException(400, "No files selected. Please select at least one PDF file.")
    results = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"'{f.filename or 'Unnamed file'}' is not a PDF")
        try:
            results.append(ingest_pdf(f.filename, await f.read()))
        except Exception as e:
            log.exception("Ingestion failed for %s", f.filename)
            # Roll back any partial database insertions for this document
            try:
                with _conn() as c:
                    c.execute("DELETE FROM doc_chunks WHERE filename=?", (f.filename,))
                    c.execute("DELETE FROM documents WHERE filename=?", (f.filename,))
            except Exception:
                pass
            raise HTTPException(500, f"Ingestion failed for '{f.filename}': {e}")
    return {"ingested": results, "documents": list_documents()}


@app.get("/api/health")
def health():
    """Which LLM the server resolved and whether .env was picked up."""
    return llm_status()


@app.post("/api/chat")
def chat(body: ChatIn):
    return run_agent(body.message)


@app.get("/api/approvals")
def get_approvals():
    return pending_approvals()


@app.post("/api/approvals/finding/{fid}/approve")
def approve_finding_endpoint(fid: str):
    try:
        return approve_finding(fid)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/approvals/risk/{rid}/grade")
def grade_risk_endpoint(rid: str, body: GradeIn):
    try:
        return grade_risk(rid, body.residual_score, body.mitigation)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/registers")
def registers():
    return {
        "documents": list_documents(),
        "findings": list_findings(),
        "risks": list_risks(),
    }


@app.post("/api/reset")
def reset():
    reset_db()
    vector_reset()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-store"})


app.mount("/", _NoCacheStatic(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "true").lower() in ("1", "true", "yes")
    print(f"[grc] Starting server at http://localhost:{port} (reload={reload})")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=reload)

