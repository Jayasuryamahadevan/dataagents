from collections import defaultdict
from datetime import UTC, datetime
from typing import Any


def metric(records: list[dict[str, Any]], field: str, entity_type: str | None = None) -> float:
    selected = [r for r in records if not entity_type or r.get("entity_type") == entity_type]
    if field == "count":
        return float(len(selected))
    return round(sum(float(r.get(field) or 0) for r in selected), 2)


def evaluate_rule(
    rule: dict[str, Any], records: list[dict[str, Any]], source_syncs: dict[str, str | None]
) -> dict[str, Any] | None:
    cfg = rule["config"]
    if rule["kind"] == "threshold":
        value = metric(records, cfg.get("field", "amount"), cfg.get("entity_type"))
        target = float(cfg["value"])
        operator = cfg.get("operator", ">")
        matched = {
            ">": value > target,
            ">=": value >= target,
            "<": value < target,
            "<=": value <= target,
        }.get(operator)
        if matched:
            return {
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "severity": cfg.get("severity", "warning"),
                "title": cfg.get("title", rule["name"]),
                "detail": f"{cfg.get('field', 'amount')} is {value}, which is {operator} {target}.",
                "metrics": {"actual": value, "threshold": target, "operator": operator},
            }
    if rule["kind"] == "reconciliation":
        left = metric(records, cfg.get("left_field", "amount"), cfg.get("left_entity_type"))
        right = metric(records, cfg.get("right_field", "amount"), cfg.get("right_entity_type"))
        delta = round(left - right, 2)
        allowed = float(cfg.get("allowed_delta", 0))
        if abs(delta) > allowed:
            return {
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "severity": cfg.get("severity", "critical"),
                "title": cfg.get("title", rule["name"]),
                "detail": f"Reconciliation delta is {delta}; allowed delta is {allowed}.",
                "metrics": {"left": left, "right": right, "delta": delta, "allowed_delta": allowed},
            }
    if rule["kind"] == "freshness":
        source_id = cfg["source_id"]
        synced = source_syncs.get(source_id)
        age = (
            float("inf")
            if not synced
            else (datetime.now(UTC) - datetime.fromisoformat(synced)).total_seconds() / 60
        )
        maximum = float(cfg.get("max_age_minutes", 60))
        if age > maximum:
            return {
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "severity": cfg.get("severity", "warning"),
                "title": cfg.get("title", rule["name"]),
                "detail": f"Source has not synced within {maximum} minutes.",
                "metrics": {"age_minutes": age, "max_age_minutes": maximum},
            }
    return None


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    amounts: dict[str, float] = defaultdict(float)
    for record in records:
        kind = record.get("entity_type", "event")
        by_type[kind] += 1
        amounts[kind] += float(record.get("amount") or 0)
    return {
        "record_count": len(records),
        "by_entity_type": dict(by_type),
        "amount_by_entity_type": {key: round(value, 2) for key, value in amounts.items()},
    }
