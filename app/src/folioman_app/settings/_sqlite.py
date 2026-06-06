"""SQLite concurrency settings shared by the dev (base) and desktop run modes.

Folioman runs the valuation scheduler in a thread alongside the request handler
(see ``scheduler.py`` / ``apps.py``), so two connections — request and scheduler
— write the same SQLite file. These ``OPTIONS`` are what keep that contention from
surfacing as ``database is locked``:

- ``journal_mode=WAL`` — readers and the single writer proceed concurrently
  (rollback-journal mode would lock the whole file on every write).
- ``synchronous=NORMAL`` — the safe + fast durability pairing under WAL.
- ``busy_timeout=5000`` — a writer waiting on another writer's lock retries for
  up to 5s instead of erroring immediately. This is the setting that turns a
  collision into a short wait.
- ``transaction_mode=IMMEDIATE`` (Django 5.2) — take the write lock at ``BEGIN``
  rather than deferring, avoiding the deferred-then-upgrade deadlock window where
  two connections both hold a read txn and both try to become the writer.

Django 5.2 splits ``init_command`` on ``;`` and runs each PRAGMA per connection.
Postgres (server mode) needs none of this and defines its own ``DATABASES``.
"""

from __future__ import annotations

from typing import Any


def sqlite_concurrency_options() -> dict[str, Any]:
    """The ``OPTIONS`` block for a SQLite ``DATABASES['default']`` shared by a
    request thread and the in-process scheduler thread."""
    return {
        "init_command": (
            "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000;"
        ),
        "transaction_mode": "IMMEDIATE",
    }
