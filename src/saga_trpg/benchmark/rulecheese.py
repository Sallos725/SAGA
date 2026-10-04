from __future__ import annotations

from saga_trpg.benchmark.models import BenchmarkRun, BenchmarkScenario


class RuleCheeseRunner:
    """Placeholder benchmark runner.

    The production runner should drive a model through the same runtime used by interactive play.
    It must not give models hidden validator state or retrospective judge information.
    """

    async def run(self, scenario: BenchmarkScenario, *, model: str) -> BenchmarkRun:
        raise NotImplementedError
