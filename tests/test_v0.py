"""Acceptance tests: ingest, discovery, citation guard, drafting, human gate."""
import os
import sys
import tempfile

# Ensure project root is in sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest
from fastapi.testclient import TestClient

# isolated DB per test session; acceptance tests assert on the deterministic
# offline analyzer, not a live LLM
os.environ["GRC_DB"] = os.path.join(tempfile.mkdtemp(), "test_grc.db")
os.environ["GRC_LLM_MODE"] = "mock"

import demo  # noqa: E402
from app import (  # noqa: E402
    SAMPLE_DOCS,
    all_chunks,
    app,
    guard_citations,
    list_documents,
)

client = TestClient(app)
QUESTION = "Can we onboard an EU customer under GDPR based on our uploaded docs?"


@pytest.fixture(autouse=True)
def fresh():
    demo.make_sample_pdfs()
    client.post("/api/reset")
    yield


def _upload():
    files = [("files", (n, open(os.path.join(SAMPLE_DOCS, n), "rb"), "application/pdf"))
             for n in sorted(os.listdir(SAMPLE_DOCS)) if n.endswith(".pdf")]
    return client.post("/api/upload", files=files).json()


def test_pdf_ingestion():
    res = _upload()
    assert len(res["documents"]) == 3
    assert all(d["n_chunks"] > 0 for d in res["ingested"])
    assert len(all_chunks()) >= 3


def test_compliance_discovery_no_hallucination():
    _upload()
    chat = client.post("/api/chat", json={"message": QUESTION}).json()
    assert "DPA" in chat["answer"]
    # cites only real uploaded files
    cited = {c["filename"] for c in chat["citations"]}
    assert cited and cited <= {d["filename"] for d in list_documents()}


def test_citation_guard_blocks_fabrication():
    _upload()
    real = list_documents()[0]["filename"]
    res = guard_citations([
        {"filename": real, "chunk_id": 1},
        {"filename": "totally_made_up_policy.pdf", "chunk_id": 999},
        {"filename": real, "chunk_id": 10_000_000},
    ])
    assert len(res["citations"]) == 1
    assert len(res["warnings"]) == 2


def test_drafting_creates_proposed_and_null_residual():
    _upload()
    client.post("/api/chat", json={"message": QUESTION})
    pend = client.get("/api/approvals").json()
    assert pend["findings"] and pend["findings"][0]["status"] == "proposed"
    assert pend["risks"] and pend["risks"][0]["residual_score"] is None
    assert pend["risks"][0]["inherent_score"] == "High"


def test_chat_follows_the_question_not_a_stock_gap():
    """Greetings and off-topic asks must not dump the DPA finding."""
    _upload()
    hi = client.post("/api/chat", json={"message": "hi"}).json()
    assert "DPA" not in hi["answer"]
    assert not (hi.get("drafted") or {}).get("finding_id")

    weather = client.post("/api/chat", json={"message": "what is the weather of patna"}).json()
    assert "DPA" not in weather["answer"]
    assert "patna" in weather["answer"].lower()
    assert not (weather.get("drafted") or {}).get("finding_id")

    encrypt = client.post("/api/chat", json={"message": "Do we encrypt data at rest?"}).json()
    low = encrypt["answer"].lower()
    assert "dpa" not in low
    assert "encrypt" in low or "kms" in low


def test_human_gate_requires_valid_residual():
    _upload()
    client.post("/api/chat", json={"message": QUESTION})
    pend = client.get("/api/approvals").json()
    rid = pend["risks"][0]["id"]

    bad = client.post(f"/api/approvals/risk/{rid}/grade", json={"residual_score": "Severe"})
    assert bad.status_code == 422

    ok = client.post(f"/api/approvals/risk/{rid}/grade",
                     json={"residual_score": "Medium", "mitigation": "Interim SCCs"})
    assert ok.status_code == 200 and ok.json()["status"] == "official"
    assert ok.json()["residual_score"] == "Medium"

    fid = pend["findings"][0]["id"]
    assert client.post(f"/api/approvals/finding/{fid}/approve").json()["status"] == "official"
