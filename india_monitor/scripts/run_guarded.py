"""Idempotent runner — fires whenever the Mac wakes up, runs at most once per day.

Indian market closes at 3:30 PM IST = ~5:00 AM CDT / ~10:00 UTC.
We run any time after 10:30 UTC (buffer for data to settle) on a weekday.
The sentinel file prevents double-runs after the first successful execution.

Sentinel: ~/.india-monitor-last-run  (contains YYYY-MM-DD)
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SENTINEL = Path.home() / ".india-monitor-last-run"

# Run after 10:30 UTC (= 4:00 PM IST, 30 min after market close)
RUN_AFTER_UTC_HOUR = 10
RUN_AFTER_UTC_MINUTE = 30


def already_ran_today() -> bool:
    if not SENTINEL.exists():
        return False
    try:
        return SENTINEL.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def mark_ran_today() -> None:
    try:
        SENTINEL.write_text(date.today().isoformat())
    except OSError as exc:
        log.warning("Could not write sentinel: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    now_utc = datetime.now(tz=UTC)
    now_ist = now_utc.astimezone(IST)

    # Skip weekends (IST weekday — Indian market is Mon–Fri)
    if now_ist.weekday() >= 5:
        log.info("Weekend in IST — skipping.")
        return 0

    # Skip if before 10:30 UTC (market not yet closed)
    if (now_utc.hour, now_utc.minute) < (RUN_AFTER_UTC_HOUR, RUN_AFTER_UTC_MINUTE):
        log.info(
            "Before market-close buffer (%02d:%02dUTC) — skipping. Now: %02d:%02dUTC",
            RUN_AFTER_UTC_HOUR, RUN_AFTER_UTC_MINUTE,
            now_utc.hour, now_utc.minute,
        )
        return 0

    if already_ran_today():
        log.info("Already ran today (%s) — skipping.", date.today().isoformat())
        return 0

    log.info("Conditions met (IST: %s) — starting India monitor run.", now_ist.strftime("%H:%M"))

    from india_monitor.main import run_once

    try:
        run_once()
        mark_ran_today()
        log.info("Run completed and sentinel written.")
    except Exception:
        log.exception("India monitor run failed — sentinel NOT written (will retry).")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
