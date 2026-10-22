from sre_agent.config import BACKEND_CONTAINER, DATABASE_CONTAINER
from sre_agent.graphs.remediation.runbooks import ACTIONS, load_runbooks, validate_proposal


def test_catalog_loads_and_uses_only_known_actions():
    books = load_runbooks()
    assert books
    for rb in books.values():
        assert rb.allowed_targets
        assert {s["action"] for s in rb.steps} <= set(ACTIONS)


def test_config_values_are_substituted():
    books = load_runbooks()
    assert books["restart_service"].allowed_targets == [BACKEND_CONTAINER]
    steps = books["recover_database"].rendered_steps(DATABASE_CONTAINER)
    assert steps[0]["target"] == DATABASE_CONTAINER          # default: the chosen target
    assert steps[2]["target"] == BACKEND_CONTAINER           # explicit target in the step


def test_validation_enforces_the_allowlist():
    books = load_runbooks()
    assert validate_proposal({"runbook_id": "restart_service", "target": BACKEND_CONTAINER}, books) is None
    assert validate_proposal({"runbook_id": "none"}, books) is None
    assert "not allowed" in validate_proposal({"runbook_id": "restart_service", "target": DATABASE_CONTAINER}, books)
    assert "unknown runbook" in validate_proposal({"runbook_id": "rm_rf", "target": BACKEND_CONTAINER}, books)
