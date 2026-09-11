"""Sample-PDF generator + browserless end-to-end demo.

  python demo.py       -> (re)write sample_docs/, then run the full flow and print each step
  make_sample_pdfs()   -> imported by tests to (re)create the 3 sample PDFs
"""
import json
import os
import sys
import textwrap

# Ensure project root is always in sys.path so demo.py works from any directory
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

_HERE = os.path.join(_ROOT, "sample_docs")

_DOCS = {
    "1_GDPR_Art28_DPA_Requirements.pdf": """
    EU GDPR ARTICLE 28 - PROCESSOR OBLIGATIONS (REGULATORY REFERENCE)

    Under GDPR Article 28, a controller may only use a processor that provides
    sufficient guarantees to implement appropriate technical and organisational
    measures. Processing by a processor must be governed by a binding written
    contract - a Data Processing Agreement (DPA).

    Mandatory DPA terms include: subject matter and duration of processing;
    nature and purpose of processing; type of personal data and categories of
    data subjects; processing only on documented instructions of the controller;
    confidentiality commitments; security measures per Article 32; sub-processor
    authorisation; assistance with data-subject rights; breach notification;
    deletion or return of data; and audit rights.

    A Data Processing Agreement (DPA) MUST be executed BEFORE any EU personal
    data is shared with the processor. Failure to comply with Article 28 can
    result in administrative fines of up to EUR 20 million or 4% of total
    worldwide annual turnover, whichever is higher.
    """,
    "2_Acme_Security_and_Backup_Policy.pdf": """
    ACME CORP - INFORMATION SECURITY & BACKUP POLICY (INTERNAL CONTROL)

    Purpose: define the security controls Acme applies to customer data in order
    to satisfy GDPR Article 32 (security of processing).

    Encryption: All customer data is encrypted in transit using TLS 1.2+.
    All customer data and backups are encrypted at rest using AES-256 with keys
    managed by AWS KMS. Key rotation is enabled annually.

    Backups: Automated daily backups are stored in a separate AWS region.
    Backup encryption at rest via AWS KMS is verified quarterly. Restore tests
    are performed every six months.

    Access control: least-privilege IAM roles, MFA enforced, access reviewed
    quarterly. Audit logging via AWS CloudTrail is retained for 12 months.

    This policy demonstrates that Acme meets the Article 32 technical security
    measures expected of a GDPR processor.
    """,
    "3_EU_Customer_Onboarding_Assessment.pdf": """
    EU CUSTOMER ONBOARDING - COMPLIANCE ASSESSMENT (GAP ANALYSIS)

    Prospective customer: a German client (EU / EEA). Data involved: names,
    emails, and usage data of EU data subjects.

    Findings of this assessment:
    - Security controls: SATISFACTORY. Acme's Security & Backup Policy shows
      encryption at rest via AWS KMS, meeting GDPR Article 32.
    - Contractual basis: DEFICIENT. Acme has NOT executed a Data Processing
      Agreement (DPA) with the prospective German client. No signed DPA and no
      standard DPA template are on file for this engagement.

    Conclusion: There is a GAP. GDPR Article 28 requires a signed Data
    Processing Agreement before onboarding. Onboarding cannot proceed until a
    DPA is executed. Interim measure under consideration: Standard Contractual
    Clauses (SCCs).
    """,
}


def _write(path, body):
    c = canvas.Canvas(path, pagesize=LETTER)
    _width, height = LETTER
    y = height - 72
    for para in body.strip().split("\n\n"):
        for line in textwrap.wrap(" ".join(para.split()), 95):
            c.drawString(72, y, line)
            y -= 15
            if y < 72:
                c.showPage()
                y = height - 72
        y -= 10
    c.save()


def make_sample_pdfs():
    """Write the 3 demo PDFs into sample_docs/ and return their filenames."""
    os.makedirs(_HERE, exist_ok=True)
    for name, body in _DOCS.items():
        _write(os.path.join(_HERE, name), body)
    return sorted(_DOCS)


def main():
    if "--make-pdfs-only" in sys.argv or "--generate-only" in sys.argv:
        pdfs = make_sample_pdfs()
        print(f"[demo] Generated {len(pdfs)} sample PDFs into {_HERE}:")
        for p in pdfs:
            print(f"  - {p}")
        return

    # Always generate sample PDFs first so they exist regardless of app setup
    make_sample_pdfs()

    from fastapi.testclient import TestClient

    try:
        from app import SAMPLE_DOCS, app
    except (ImportError, ModuleNotFoundError):
        try:
            from app.main import SAMPLE_DOCS, app
        except Exception as err:
            raise RuntimeError(
                f"Could not import application from app.py or app/main.py: {err}"
            ) from err

    def show(title, obj):
        print(f"\n=== {title} ===")
        print(json.dumps(obj, indent=2, default=str))

    client = TestClient(app)
    client.post("/api/reset")

    docs_dir = SAMPLE_DOCS if (SAMPLE_DOCS and os.path.isdir(SAMPLE_DOCS)) else _HERE
    pdf_names = sorted(n for n in os.listdir(docs_dir) if n.endswith(".pdf"))
    opened_files = []
    try:
        for name in pdf_names:
            opened_files.append((name, open(os.path.join(docs_dir, name), "rb")))
        files = [("files", (name, f, "application/pdf")) for name, f in opened_files]
        show("UPLOAD", client.post("/api/upload", files=files).json().get("ingested", []))
    finally:
        for _, f in opened_files:
            f.close()

    chat = client.post("/api/chat", json={
        "message": "Can we onboard an EU customer under GDPR based on our uploaded docs?"
    }).json()
    show("CHAT", chat)

    pend = client.get("/api/approvals").json()
    show("PENDING APPROVALS", pend)

    if pend.get("findings"):
        fid = pend["findings"][0]["id"]
        show("APPROVE FINDING", client.post(f"/api/approvals/finding/{fid}/approve").json())

    if pend.get("risks"):
        rid = pend["risks"][0]["id"]
        show("GRADE RISK", client.post(f"/api/approvals/risk/{rid}/grade", json={
            "residual_score": "Medium",
            "mitigation": "Interim Standard Contractual Clauses (SCCs) in place; execute full DPA within 30 days.",
        }).json())

    show("REGISTERS", client.get("/api/registers").json())


if __name__ == "__main__":
    main()
