"""Idempotent runner — fires whenever the Mac wakes up, runs at most once per day.

Indian market closes at 3:30 PM IST = ~5:00 AM CDT / ~10:00 UTC.
We run any time after 10:30 UTC (buffer for data to settle) on a weekday.
The sentinel file prevents double-runs after the first successful execution.

Sentinel: ~/.zerodha-monitor-last-run[-<portfolio>]  (contains YYYY-MM-DD)

Usage:
  python -m zerodha_monitor.scripts.run_guarded                      # default portfolio
  python -m zerodha_monitor.scripts.run_guarded --portfolio surender  # Surender's portfolio
  python -m zerodha_monitor.scripts.run_guarded --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# Run after 10:30 UTC (= 4:00 PM IST, 30 min after market close)
RUN_AFTER_UTC_HOUR = 10
RUN_AFTER_UTC_MINUTE = 30

_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _sentinel(portfolio: str) -> Path:
    suffix = f"-{portfolio}" if portfolio else ""
    return Path.home() / f".zerodha-monitor-last-run{suffix}"


def already_ran_today(portfolio: str) -> bool:
    s = _sentinel(portfolio)
    if not s.exists():
        return False
    try:
        return s.read_text().strip() == date.today().isoformat()
    except OSError:
        return False


def mark_ran_today(portfolio: str) -> None:
    try:
        _sentinel(portfolio).write_text(date.today().isoformat())
    except OSError as exc:
        log.warning("Could not write sentinel: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_guarded")
    parser.add_argument(
        "--portfolio", default="",
        help="Portfolio name (e.g. 'surender'). Uses holdings_<name>.yaml and config_<name>.yaml.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip sending emails.")
    args = parser.parse_args()

    portfolio = args.portfolio.strip().lower()

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

    if already_ran_today(portfolio):
        log.info("Already ran today (%s) — skipping.", date.today().isoformat())
        return 0

    label = f"India monitor ({portfolio})" if portfolio else "India monitor"
    log.info("Conditions met (IST: %s) — starting %s run.", now_ist.strftime("%H:%M"), label)

    from zerodha_monitor.main import run_once

    # Resolve per-portfolio file paths
    config_file   = _PROJECT_ROOT / (f"config_{portfolio}.yaml" if portfolio else "config.yaml")
    holdings_file = _PROJECT_ROOT / (f"holdings_{portfolio}.yaml" if portfolio else "holdings.yaml")
    store_file    = Path.home() / (f".zerodha-monitor-state-{portfolio}.db" if portfolio else ".zerodha-monitor-state.db")

    try:
        run_once(
            dry_run=args.dry_run,
            config_path=config_file,
            holdings_path=holdings_file,
            store_path=store_file,
        )
        mark_ran_today(portfolio)
        log.info("Run completed and sentinel written.")
    except Exception:
        log.exception("%s run failed — sentinel NOT written (will retry).", label)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
