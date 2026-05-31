"""Daily orchestrator for the India portfolio monitor."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config_loader import load_config
from .email_dispatch import EmailDispatcher
from .holdings_loader import load_holdings, project_root
from .market_data import IndiaMarketData
from .rules import ALL_RULES
from .store import Store

# COE trade log lives in the US portfolio-monitor repo (single source of truth).
_COE_TRADE_LOG = Path.home() / "portfolio-monitor" / "coe_trade_log.yaml"
sys.path.insert(0, str(Path.home() / "portfolio-monitor"))
try:
    from portfolio_monitor.compliance_checker import get_compliance_status as _get_compliance
except ImportError:
    _get_compliance = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # yfinance logs expected 404s at ERROR — suppress them.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def run_once(*, dry_run: bool = False) -> int:
    """Single end-to-end pass. Returns count of alerts dispatched."""
    root = project_root()
    config = load_config(root / "config.yaml")
    holdings = load_holdings(root / "holdings.yaml")

    configure_logging(config.logging.level)
    log.info("Starting India monitor run (dry_run=%s, %d holdings)", dry_run, len(holdings))

    market = IndiaMarketData(ath_period=config.data_sources.ath_history_period)
    store = Store()
    dispatcher = EmailDispatcher(config.alerts.email)

    compliance = _get_compliance(_COE_TRADE_LOG) if _get_compliance else None
    sent_count = 0

    for rule_cls in ALL_RULES:
        rule = rule_cls(config)
        if not rule.enabled:
            log.info("Rule %s disabled — skipping.", rule.name)
            continue
        try:
            alerts = rule.evaluate(holdings, market)
        except Exception:  # noqa: BLE001
            log.exception("Rule %s crashed — continuing.", rule.name)
            continue

        for alert in alerts:
            if store.in_cooldown(alert.symbol, alert.rule, config.alerts.cooldown_days):
                log.debug("Cooldown skip: %s/%s", alert.symbol, alert.rule)
                continue

            # Attach compliance status to every alert payload before dispatch.
            if compliance:
                alert.payload["compliance"] = compliance

            log.info("Dispatch: %s [%s]", alert.title, alert.severity.value)
            try:
                dispatcher.send_alert(alert, dry_run=dry_run)
                sent_count += 1
                if not dry_run:
                    store.record(
                        symbol=alert.symbol,
                        rule=alert.rule,
                        severity=alert.severity.value,
                        title=alert.title,
                        payload=alert.payload,
                    )
            except Exception:  # noqa: BLE001
                log.exception("Failed to send alert for %s — continuing.", alert.symbol)

    store.prune(retain_days=config.logging.retain_days)
    log.info("Run complete — %d alert(s) dispatched", sent_count)
    return sent_count


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
