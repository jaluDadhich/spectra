import fitz

from app.evidence.source_viewer import find_pdf_highlights
from app.schemas.findings import EvidenceRef


def test_pdf_highlight_finds_currency_display_of_numeric_evidence():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Building replacement value: $12,500,000")
    evidence = EvidenceRef(
        evidence_id="EV-1",
        document_id="DOC-1",
        raw_value=12500000,
        normalized_value=12500000.0,
    )

    matches = find_pdf_highlights(page, evidence)

    assert len(matches) == 1
    assert matches[0].width > 0
    document.close()
