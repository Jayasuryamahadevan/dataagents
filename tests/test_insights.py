from app.services.insights import evaluate_rule


def test_threshold_rule_triggers():
    rule = {
        "id": "r1",
        "name": "Large invoices",
        "kind": "threshold",
        "config": {"field": "amount", "entity_type": "invoice", "operator": ">", "value": 1000},
    }
    outcome = evaluate_rule(rule, [{"entity_type": "invoice", "amount": 1200}], {})
    assert outcome is not None
    assert outcome["severity"] == "warning"
