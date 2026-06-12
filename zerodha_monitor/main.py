"""Daily orchestrator for the India portfolio monitor."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config_loader import load_config
from .email_dispatch import EmailDispatcher
from .holdings_loader import load_holdings, project_root
from .market_data import IndiaMarketData, MarketDataForDate
from .rules import ALL_RULES
from .store import Store

try:
    from .compliance_checker import get_compliance_status  # local-only, not in public repo
    _COMPLIANCE_AVAILABLE = True
except ImportError:
    _COMPLIANCE_AVAILABLE = False

log = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

# How many past trading days to back-fill if alerts were missed
_BACKFILL_DAYS = 3


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _alert_sort_key(alert):
    """Profitable first (ascending analyst upside = most urgent), then losses (biggest first)."""
    pl     = alert.payload.get("unrealized_pl", 0) or 0
    upside = alert.payload.get("analyst_upside")
    if pl >= 0:
        # No analyst coverage → sort after stocks with coverage
        return (0, upside if upside is not None else 9.99)
    else:
        return (1, pl)   # more negative = bigger loss = comes first


def run_once(
    *,
    dry_run: bool = False,
    config_path: Path | None = None,
    holdings_path: Path | None = None,
    store_path: Path | None = None,
) -> int:
    """Single end-to-end pass. Returns total alert count across all dates processed."""
    root     = project_root()
    config   = load_config(config_path or root / "config.yaml")
    holdings = load_holdings(holdings_path or root / "holdings.yaml")

    configure_logging(config.logging.level)
    log.info("Starting India monitor run (dry_run=%s, %d holdings)", dry_run, len(holdings))

    market     = IndiaMarketData(ath_period=config.data_sources.ath_history_period)
    store      = Store(store_path) if store_path else Store()
    dispatcher = EmailDispatcher(
        config.alerts.email,
        portfolio_owner=config.portfolio_owner,
        zerodha_id=config.zerodha_id,
    )
    compliance = get_compliance_status() if _COMPLIANCE_AVAILABLE else None

    # ── Find available trading dates (probe via INFY — liquid, always has data) ──
    today_ist    = datetime.now(tz=_IST).date()
    probe_dates  = market.available_dates("INFY", n=_BACKFILL_DAYS + 2)
    if not probe_dates:
        log.error("Cannot determine available data dates — aborting")
        return 0

    # Only process dates on or before today (IST) and within the backfill window
    dates_to_process = [d for d in probe_dates if d <= today_ist][-_BACKFILL_DAYS:]
    log.info("Available dates: %s | Today IST: %s", [str(d) for d in probe_dates], today_ist)

    total_sent = 0

    for data_date in dates_to_process:          # oldest → newest
        already_sent = store.alerts_sent_for_date(data_date)
        mfd          = MarketDataForDate(market, data_date)

        date_alerts = []
        for rule_cls in ALL_RULES:
            rule = rule_cls(config)
            if not rule.enabled:
                continue
            try:
                alerts = rule.evaluate(holdings, mfd)
            except Exception:  # noqa: BLE001
                log.exception("Rule %s crashed — continuing.", rule.name)
                continue

            for alert in alerts:
                if (alert.symbol.upper(), alert.rule) in already_sent:
                    log.debug("Already sent %s/%s for %s — skipping", alert.symbol, alert.rule, data_date)
                    continue

                if compliance is not None:
                    alert.payload["compliance"] = {
                        "quarter":   compliance.quarter,
                        "used":      compliance.used,
                        "limit":     compliance.limit,
                        "remaining": compliance.remaining,
                        "warning":   compliance.warning,
                        "critical":  compliance.critical,
                    }

                date_alerts.append(alert)

        if not date_alerts:
            log.info("No new alerts for %s", data_date)
            continue

        # Sort: profitable (min analyst upside first) → losses (biggest first)
        date_alerts.sort(key=_alert_sort_key)

        profitable = [a for a in date_alerts if (a.payload.get("unrealized_pl") or 0) >= 0]
        losses     = [a for a in date_alerts if (a.payload.get("unrealized_pl") or 0) < 0]
        log.info(
            "%s → %d new alert(s) (%d profitable, %d loss) — sending digest",
            data_date, len(date_alerts), len(profitable), len(losses),
        )

        try:
            dispatcher.send_digest(date_alerts, data_date, dry_run=dry_run)
            total_sent += len(date_alerts)
            if not dry_run:
                for alert in date_alerts:
                    store.record(
                        symbol=alert.symbol,
                        rule=alert.rule,
                        severity=alert.severity.value,
                        title=alert.title,
                        payload=alert.payload,
                        data_date=data_date,
                    )
        except Exception:  # noqa: BLE001
            log.exception("Failed to send digest for %s — continuing.", data_date)

    store.prune(retain_days=config.logging.retain_days)
    log.info("Run complete — %d alert(s) dispatched", total_sent)
    return total_sent


def cli() -> None:
    import argparse, sys
    parser = argparse.ArgumentParser(prog="zerodha-monitor")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        run_once(dry_run=args.dry_run)
    except Exception:
        log.exception("Run failed")
        sys.exit(1)


if __name__ == "__main__":
    cli()
