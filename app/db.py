"""Database connection — SQLite (WAL mode).

Provides ``get_connection()`` which returns a ``sqlite3.Connection``
configured with WAL journal, busy timeout, and foreign keys.

Data is stored in ``data/vauxtra.db`` by default.
"""

from __future__ import annotations

import os
import sqlite3

from app.config import DB_PATH, DATA_DIR


def get_sqlite_connection() -> sqlite3.Connection:
    """Open a SQLite connection with the standard Vauxtra pragmas."""
    os.makedirs(DATA_DIR, exist_ok=True)

    # Migrate legacy database filename (dns_manager.db → vauxtra.db)
    _legacy_path = os.path.join(DATA_DIR, "dns_manager.db")
    if not os.path.exists(DB_PATH) and os.path.exists(_legacy_path):
        os.rename(_legacy_path, DB_PATH)
        for suffix in ("-wal", "-shm"):
            old = _legacy_path + suffix
            if os.path.exists(old):
                os.rename(old, DB_PATH + suffix)

    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_connection() -> sqlite3.Connection:
    """Return a SQLite DB connection."""
    return get_sqlite_connection()
