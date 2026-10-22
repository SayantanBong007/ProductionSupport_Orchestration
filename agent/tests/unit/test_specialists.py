from sre_agent.agents import SPECIALISTS


def test_parallel_specialists_do_not_share_a_rate_limit():
    # Specialists are dispatched in parallel; each needs its own per-model quota.
    models = [s.model for s in SPECIALISTS.values()]
    assert len(set(models)) == len(models)
