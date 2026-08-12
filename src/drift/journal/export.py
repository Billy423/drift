"""The journal's export path: one run's rows on a file, answerable without a database.

Nothing here adds or interprets: rows are selected by run id and ride out verbatim with their
stamps, ordered by id, which is insertion order.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from drift.persistence.models import JournalRecord

__all__ = ["export_run"]


def export_run(session, run_id: int, path: str) -> int:
    """Write every journal row of `run_id` to `path` as JSONL; returns the row count."""
    rows = session.scalars(
        select(JournalRecord).where(JournalRecord.run_id == run_id).order_by(JournalRecord.id)
    )
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(
                json.dumps(
                    {
                        "run_id": r.run_id,
                        "repo": r.repo,
                        "commit_sha": r.commit_sha,
                        "component": r.component,
                        "record_type": r.record_type,
                        "payload": r.payload,
                        "agent_ver": r.agent_ver,
                        "judge_ver": r.judge_ver,
                        "model": r.model,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n
