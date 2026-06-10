"""SQLite-backed alert store — deduplicates by (symbol, rule, data_date)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DB = Path.home() / ".zerodha-monitor-state.db"


class Store:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self._db = db_path
        self._init()

    def _init(self) -> None:
        with self._conn() as cx:
            # Create table without data_date first (safe for new DBs)
            cx.execute("""
                CREATE TABLE IF NOT EXISTS alert_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol    TEXT NOT NULL,
                    rule      TEXT NOT NULL,
                    severity  TEXT NOT NULL,
                    title     TEXT,
                    payload   TEXT,
                    fired_at  TEXT NOT NULL
                )
            """)
            cx.execute("CREATE INDEX IF NOT EXISTS idx_sym_rule ON alert_log(symbol, rule)")
            # Migrate: add data_date column if it doesn't exist yet
            try:
                cx.execute("ALTER TABLE alert_log ADD COLUMN data_date TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists — nothing to do
            # Now safe to create the index on data_date
            cx.execute("CREATE INDEX IF NOT EXISTS idx_data_date ON alert_log(data_date)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def alerts_sent_for_date(self, data_date: date) -> set[tuple[str, str]]:
        """Return set of (symbol, rule) already dispatched for this data_date."""
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT symbol, rule FROM alert_log WHERE data_date=?",
                (data_date.isoformat(),),
            ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def in_cooldown(self, symbol: str, rule: str, days: int) -> bool:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as cx:
            row = cx.execute(
                "SELECT 1 FROM alert_log WHERE symbol=? AND rule=? AND fired_at>? LIMIT 1",
                (symbol.upper(), rule, cutoff),
            ).fetchone()
        return row is not None

    def record(self, *, symbol: str, rule: str, severity: str,
               title: str, payload: dict, data_date: date | None = None) -> None:
        with self._conn() as cx:
            cx.execute(
                "INSERT INTO alert_log(symbol,rule,severity,title,payload,fired_at,data_date)"
                " VALUES(?,?,?,?,?,?,?)",
                (symbol.upper(), rule, severity, title,
                 json.dumps(payload), datetime.now(tz=timezone.utc).isoformat(),
                 data_date.isoformat() if data_date else None),
            )

    def prune(self, retain_days: int = 90) -> None:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=retain_days)).isoformat()
        with self._conn() as cx:
            cx.execute("DELETE FROM alert_log WHERE fired_at<?", (cutoff,))
