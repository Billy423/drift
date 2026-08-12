"""One short-lived database read, shared by everything that polls.

Its own module so that every caller reaches it through the same attribute: a copy bound by
`from ... import` in each consumer is a copy each test would have to substitute separately.
"""

from __future__ import annotations

from drift.persistence.db import SessionLocal


def fresh_read(session_factory, fn):
    """Run one short-lived read against the database — never on a held snapshot.

    A long-lived session polling for `cell_result` rows can otherwise sit on one snapshot for the
    whole fan-in and never see a row another process committed, hanging the frame against a
    perfectly healthy run. Either branch below must leave no snapshot outliving the read.
    """
    owned = session_factory is None
    session = SessionLocal() if owned else session_factory()
    try:
        return fn(session)
    finally:
        if owned:
            session.close()
        else:
            session.commit()
