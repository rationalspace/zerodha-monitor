"""SQLite-backed cooldown store — prevents repeat alerts for the same holding."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DB = Path.home() / ".zerodha-monitor-state.db"


class Store:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self._db = db_path
        self._init()

    def _init(self) -> None:
        with self._conn() as cx:
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

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def in_cooldown(self, symbol: str, rule: str, days: int) -> bool:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as cx:
            row = cx.execute(
                "SELECT 1 FROM alert_log WHERE symbol=? AND rule=? AND fired_at>? LIMIT 1",
                (symbol.upper(), rule, cutoff),
            ).fetchone()
        return row is not None

    def record(self, *, symbol: str, rule: str, severity: str,
               title: str, payload: dict) -> None:
        with self._conn() as cx:
            cx.execute(
                "INSERT INTO alert_log(symbol,rule,severity,title,payload,fired_at) VALUES(?,?,?,?,?,?)",
                (symbol.upper(), rule, severity, title,
                 json.dumps(payload), datetime.now(tz=timezone.utc).isoformat()),
            )

    def prune(self, retain_days: int = 90) -> None:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=retain_days)).isoformat()
        with self._conn() as cx:
            cx.execute("DELETE FROM alert_log WHERE fired_at<?", (cutoff,))
