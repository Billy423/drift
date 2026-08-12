"""Queue routing: cells go to `drift.cells`, the frame and everything else to the default.

These assert against the routing Celery would actually apply — `celery_app.amqp.router.route(...)`,
the same call the publisher makes — rather than against the `task_routes` literal. A string grep of
the config would pass even if the routes never took effect (wrong task name, a later override, a
router that never sees them).

Offline: resolving a route touches no broker. Whether a live worker is actually *listening* on
`drift.cells` is a deployment fact, documented in the Makefile and README, not a test.
"""

from __future__ import annotations

import re
from pathlib import Path

from drift.tasks.celery_app import celery_app

_MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _queue_for(task_name: str) -> str:
    """The queue Celery would publish `task_name` to, per the app's own router."""
    return celery_app.amqp.router.route({}, task_name)["queue"].name


def test_the_cell_task_routes_to_its_own_queue():
    """Isolation is the warrant: the frame and its cells must not share one worker pool."""
    assert _queue_for("drift.cells.run_cell") == "drift.cells"


def test_the_frame_stays_on_the_default_queue():
    """Only cells are routed away; nothing else acquires a queue by accident.

    Asserted against the frame task's registered name rather than a string spelled here: that
    task is `enqueue_scan`'s unit, and this is what keeps it off the cells' pool. A frame on
    `drift.cells` would hold the only slot of a `--concurrency=1` worker and then wait on cells
    that can never start.
    """
    from drift.tasks.scan import scan_repo

    assert celery_app.conf.task_default_queue == "celery"
    assert scan_repo.name == "drift.scan.scan_repo"  # explicit, so routing is not module-path luck
    assert _queue_for(scan_repo.name) == "celery"
    # An unrouted name is the general case, and it must land on the default queue too.
    assert _queue_for("drift.some.future.task") == "celery"


def test_only_the_cell_task_is_routed():
    """The route table stays a one-entry exception, not a growing dispatch layer."""
    assert set(celery_app.conf.task_routes) == {"drift.cells.run_cell"}


def _makefile_dash_q(target: str) -> str:
    """The single `-Q` value in `target`'s recipe, as the Makefile actually spells it.

    A recipe is the tab-indented lines under `<target>:`, with `\\`-continuations joined.
    """
    body: list[str] = []
    in_recipe = False
    for line in _MAKEFILE.read_text(encoding="utf-8").splitlines():
        if re.match(rf"^{re.escape(target)}:", line):
            in_recipe = True
            continue
        if in_recipe:
            if line.startswith("\t"):
                body.append(line.removesuffix("\\"))
            elif line.strip():  # a new target/assignment ends the recipe; blank lines don't
                break
    assert body, f"no recipe found for target {target!r} in {_MAKEFILE}"
    found = re.findall(r"-Q\s+(\S+)", " ".join(body))
    assert len(found) == 1, f"expected exactly one -Q in {target!r}, got {found}"
    return found[0]


def test_the_makefile_queue_flags_match_the_app_config():
    """The deploy targets' `-Q` literals are pinned to the conf they are supposed to mirror.

    `celery_app.py` restates `task_default_queue` so the Makefile has a source of truth in code,
    but nothing linked them: renaming a queue in code would strand both `-Q` literals silently,
    leaving each worker consuming a queue nobody publishes to. This is that link.
    """
    assert _makefile_dash_q("worker") == celery_app.conf.task_default_queue
    assert _makefile_dash_q("worker-cells") == _queue_for("drift.cells.run_cell")


def test_the_default_queue_worker_consumes_the_frames_queue():
    """The link is load-bearing rather than incidental.

    Before the re-point, `make worker`'s queue and the frame had nothing to do with each other —
    the frame ran in the CLI's own process. Now `enqueue_scan` publishes it, so if the frame's
    queue and that target's `-Q` ever diverge the service path silently enqueues into a queue
    nobody consumes: `enqueue_scan` returns a task id, and the scan never happens.
    """
    from drift.tasks.scan import scan_repo

    assert _makefile_dash_q("worker") == _queue_for(scan_repo.name)
