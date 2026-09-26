from datetime import UTC, datetime
from typing import Any

ENTITY_ALIASES = {
    "invoice": {"invoice", "sales_invoice", "bill"},
    "order": {"order", "sales_order", "purchase_order"},
    "payment": {"payment", "transaction", "receipt"},
    "inventory": {"inventory", "stock", "item", "warehouse_item"},
}


def first(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def entity_type(record: dict[str, Any]) -> str:
    supplied = str(first(record, "entity_type", "doctype", "type", "resource") or "").lower()
    for normalized, aliases in ENTITY_ALIASES.items():
        if supplied in aliases:
            return normalized
    if first(record, "qty", "quantity", "stock_qty") is not None:
        return "inventory"
    if first(record, "paid_amount", "payment_amount") is not None:
        return "payment"
    if first(record, "invoice_number", "grand_total", "total_amount") is not None:
        return "invoice"
    return "event"


def numeric(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def normalize(record: dict[str, Any], source_id: str) -> dict[str, Any]:
    occurred = first(record, "occurred_at", "posting_date", "transaction_date", "date", "modified")
    if occurred is None:
        occurred = datetime.now(UTC).isoformat()
    return {
        **record,
        "source_id": source_id,
        "entity_type": entity_type(record),
        "record_id": str(first(record, "id", "name", "invoice_number", "order_id") or ""),
        "amount": numeric(
            first(record, "amount", "grand_total", "total_amount", "paid_amount", "payment_amount")
        ),
        "quantity": numeric(first(record, "quantity", "qty", "stock_qty", "actual_qty")),
        "currency": first(record, "currency", "currency_code") or "INR",
        "occurred_at": str(occurred),
    }
