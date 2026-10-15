"""Small statistics helpers shared by the eval harnesses (stdlib only).

An eval number without an error bar is a guess about how big the set was. With
39 queries one query is worth 2.6 points, so "0.82" really means "somewhere
between 0.67 and 0.92". Everything here exists to print that range next to the
number and to say whether a difference between two runs is bigger than the noise.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Optional, Sequence

# Share of cases that fall in the held-out ``test`` split. Assigned from a hash
# of the case id, so it is stable, needs no bookkeeping, and cannot be nudged
# by hand to make a number look better.
TEST_PERCENT = 30


def split_of(case_id: str, explicit: Optional[str] = None) -> str:
    """``dev`` or ``test``. An explicit ``split`` field on the case wins."""
    if explicit in {"dev", "test"}:
        return explicit
    digest = hashlib.sha1(str(case_id).encode("utf-8")).hexdigest()
    return "test" if int(digest[:8], 16) % 100 < TEST_PERCENT else "dev"


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion. Unlike the textbook
    normal-approximation interval it stays inside [0, 1] and is not degenerate
    at 0% or 100%: 39 hits out of 39 still only supports "at least ~91%"."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_mean(values: Sequence[float], iterations: int = 2000, seed: int = 20261014,
                   alpha: float = 0.05) -> tuple[float, float, float]:
    """(mean, low, high): percentile bootstrap interval for a mean. Seeded, so
    the same run prints the same interval twice."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, mean, mean
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    low = means[int((alpha / 2) * iterations)]
    high = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return mean, low, high


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact p-value for a paired comparison. ``only_a`` / ``only_b``
    count the cases one run got right and the other got wrong; cases both runs
    agree on carry no information about which run is better."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def pct_ci(successes: int, n: int) -> str:
    """``0.82 [0.67-0.92]`` -- a proportion with its Wilson interval."""
    if n == 0:
        return "-"
    low, high = wilson(successes, n)
    return f"{successes / n:.2f} [{low:.2f}-{high:.2f}]"


def mean_ci(values: Sequence[float]) -> str:
    if not values:
        return "-"
    mean, low, high = bootstrap_mean(values)
    return f"{mean:.2f} [{low:.2f}-{high:.2f}]"
