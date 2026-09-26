from app.services.normalizer import normalize


def test_normalizes_erpnext_invoice():
    record = normalize(
        {
            "doctype": "Sales Invoice",
            "name": "SINV-1",
            "grand_total": "1250.50",
            "posting_date": "2026-09-26",
        },
        "erp",
    )
    assert record["entity_type"] == "invoice"
    assert record["record_id"] == "SINV-1"
    assert record["amount"] == 1250.5
