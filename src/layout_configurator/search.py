"""One place that decides how much search happens, and whether it repeats.

The default budget is wall clock: ``--time-limit`` means seconds, and a run that
hits the limit stops wherever the machine happened to be. That is fine for
exploring options and it is what the CLI has always meant, but it is not
repeatable — two runs of the same seed can return different layouts because they
stopped at different points in the search.

The deterministic budget limits counted solver work instead. Repeatability
requires the same model, seed, budget, solver build and platform; identical
results across operating systems are not guaranteed. It is opt-in because it
changes what a budget number means.
"""

from __future__ import annotations

# CP-SAT treats a very large wall-clock limit as "no limit". Setting it
# explicitly keeps the deterministic path from inheriting a cap from anywhere
# else and makes the intent visible in the parameters.
_NO_WALL_CLOCK_LIMIT = 1e15


def configure_solver(
    solver,
    *,
    seed: int,
    time_limit_seconds: float,
    deterministic_units: float | None = None,
    workers: int = 1,
) -> None:
    """Fix every setting that decides how much search happens and in what order.

    ``deterministic_units`` is CP-SAT deterministic time: an abstract work unit,
    not seconds. Wall-clock duration depends on the machine. Repeatability also
    depends on the solver build and platform, beyond its version number.

    ``workers`` above one enables CP-SAT's parallel portfolio, but only for the
    wall-clock budget: a wall-clock run does not repeat anyway, while the
    deterministic budget always searches with one worker.
    """

    solver.parameters.random_seed = int(seed)
    # A single worker keeps the search order fixed. The portfolio search
    # interleaves threads and would defeat repeatability whatever the budget.
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False
    if deterministic_units is None:
        solver.parameters.max_time_in_seconds = float(time_limit_seconds)
        solver.parameters.num_search_workers = max(1, int(workers))
        return
    if deterministic_units <= 0:
        raise ValueError("deterministic_units must be positive")
    solver.parameters.max_deterministic_time = float(deterministic_units)
    # Leaving the wall clock in place would still allow a machine-dependent cut.
    solver.parameters.max_time_in_seconds = _NO_WALL_CLOCK_LIMIT
