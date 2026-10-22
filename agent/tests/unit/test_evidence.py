from sre_agent.core.evidence import EvidenceLedger, verify_citations


def _ledger():
    ledger = EvidenceLedger()
    ledger.add("LOGS AGENT", "search_logs", {"query": "error"},
               "12x  2026-10-21T13:52:06Z → 2026-10-21T13:58:01Z  ERROR  GET /orders 503 — TimeoutError: "
               "QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 3.00")
    ledger.add("INFRA AGENT", "inspect_container", {"name": "poc-postgres"},
               "container poc-postgres\nstatus=exited running=False restarting=False\nexit_code=0 oom_killed=False")
    return ledger


def test_ids_are_sequential():
    ledger = _ledger()
    assert [e.id for e in ledger] == ["E1", "E2"]
    assert ledger.get("[e2]").tool == "inspect_container"


def test_verbatim_quote_is_verified():
    [c] = verify_citations([{"id": "E1", "quote": "QueuePool limit of size 5 overflow 10 reached"}], _ledger())
    assert c["verified"]


def test_whitespace_and_case_are_normalised():
    [c] = verify_citations([{"id": "E2", "quote": "STATUS=EXITED   running=False"}], _ledger())
    assert c["verified"]


def test_invented_fact_is_rejected():
    # The container output never mentions a non-zero exit code.
    [c] = verify_citations([{"id": "E2", "quote": "exited with non-zero status"}], _ledger())
    assert not c["verified"]
    assert "not found" in c["reason"]


def test_quote_must_come_from_the_cited_evidence():
    [c] = verify_citations([{"id": "E2", "quote": "QueuePool limit of size 5"}], _ledger())
    assert not c["verified"]


def test_ellipsis_fragments_must_all_appear_in_order():
    ok, wrong_order, invented = verify_citations([
        {"id": "E1", "quote": "GET /orders 503 … QueuePool limit of size 5"},
        {"id": "E1", "quote": "QueuePool limit of size 5 ... GET /orders 503"},
        {"id": "E1", "quote": "GET /orders 503 … pool was too small"},
    ], _ledger())
    assert ok["verified"]
    assert not wrong_order["verified"]
    assert not invented["verified"]


def test_unknown_id_and_trivial_quote_are_rejected():
    checked = verify_citations([{"id": "E9", "quote": "anything at all"},
                                {"id": "E1", "quote": "503"}], _ledger())
    assert [c["verified"] for c in checked] == [False, False]
