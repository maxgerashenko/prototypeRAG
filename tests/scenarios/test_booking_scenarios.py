"""LLM-driven booking scenarios: a local model plays the caller, the real bot answers over
POST /chat, the verdict is database assertions only. Needs LM Studio with both models and
Postgres; skipped otherwise. Run: uv run pytest -q tests/scenarios -m scenario
"""

import os
import warnings

import pytest

from tests.scenarios import harness

pytestmark = pytest.mark.scenario
COMMON, SCENARIOS = harness.load()


@pytest.fixture(scope="session")
def lm_ready(request):
    """Opt-in (they take minutes): run with `-m scenario` (or -m P5 ...) or RUN_SCENARIOS=1."""
    expr = request.config.option.markexpr or ""
    if not os.environ.get("RUN_SCENARIOS") and (not expr or "not scenario" in expr):
        pytest.skip("scenario tests: select them with -m scenario (or RUN_SCENARIOS=1)")
    why = harness.lm_missing()
    if why:
        pytest.skip(why)


def _params():
    """Each scenario carries its known-problem ids as markers: `-m P5` runs the brevity ones."""
    return [
        pytest.param(s, id=s["id"], marks=[getattr(pytest.mark, p) for p in s.get("problems", [])])
        for s in SCENARIOS
    ]


@pytest.mark.parametrize("scenario", _params())
def test_scenario(scenario, lm_ready):
    results = harness.run_scenario(scenario, COMMON)
    last = results[-1]
    if last.passed:
        if last.attempt > 1:
            warnings.warn(f"{scenario['id']}: passed on attempt {last.attempt} (flaky)")
        print(f"{scenario['id']}: passed on attempt {last.attempt}")
        return
    pytest.fail("\n\n".join(r.text() for r in results), pytrace=False)
