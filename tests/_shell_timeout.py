"""Budgets for tests that drive a *real* shell, and the retry that makes them honest.

Why this exists
---------------
Several tests assert on a fallback contract (``sandboxed: false`` +
``fallback_reason``) whose last step is "the command still ran locally". That step
really does start a shell: on Windows ``LocalExecutor`` spawns a persistent
``powershell.exe`` REPL and waits for a trailer line carrying the exit code.

That spawn is not free, and its cost is dominated by machine load rather than by
anything the test controls. Measured on a 4-core Windows box (2026-11-18), time from
``Popen`` to the first line of output:

    idle                          0.69s .. 0.86s   (n=6)
    2x cores saturated            8.94s .. 12.70s  (n=6)

So a 5s budget -- what these tests used to pass -- is *below the contended median*.
That is exactly how ``test_openshell_falls_back_when_cli_missing`` failed on main
(2743edc): ``exit_code=None`` with an empty ``output``, i.e. ``run()`` hit its
deadline before the marker arrived. Nothing was broken; the machine was busy.

Why a constant AND a retry
--------------------------
The constant raises the ceiling to ``REAL_SHELL_TIMEOUT`` (30s = 2.4x the worst
contended spawn observed) so a normal-but-loaded runner passes on the first try.

The retry covers the remaining tail: a runner can be oversubscribed far beyond 2x,
and no constant stays large forever. Critically, the retry fires *only* on a
timeout that produced no output at all -- a pure scheduling artifact, never a wrong
answer. If the shell is genuinely broken the second attempt fails the same way and
the test still fails, so this cannot mask a real regression. What it stops doing is
asserting "the machine was fast enough" when the test means "the fallback worked".

What does NOT belong here
-------------------------
``test_shell.py::test_timeout_kills_command`` passes ``timeout=1`` *as the subject
of the test*. Raising that would delete the assertion. Timeouts that are the thing
being measured stay hard-coded at their measurement point.
"""

from __future__ import annotations

from typing import Any, Callable

# Ceiling for "start a real shell and run one trivial command", not a sleep.
# 30s leaves 2.4x headroom over the worst contended spawn measured above.
REAL_SHELL_TIMEOUT = 30.0


def run_real_shell(
    run: Callable[..., dict[str, Any]],
    command: str,
    *,
    attempts: int = 2,
) -> dict[str, Any]:
    """Run ``command`` through a real shell, retrying a *starved* timeout once.

    A starved timeout is one that produced no output at all: the shell never got
    scheduled, so no work happened and a retry is safe. A timeout that produced
    partial output is returned as-is -- something was actually running, and that is
    not this helper's business to paper over.
    """

    def starved(res: dict[str, Any]) -> bool:
        return bool(res.get("timed_out")) and not res.get("output")

    result = run(command, timeout=REAL_SHELL_TIMEOUT)
    for _ in range(attempts - 1):
        if not starved(result):
            return result
        result = run(command, timeout=REAL_SHELL_TIMEOUT)
    return result
