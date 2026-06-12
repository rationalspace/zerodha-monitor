"""Load and validate config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class EmailConfig:
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    from_address: str = ""
    to_address: str = ""


@dataclass
class AlertsConfig:
    cooldown_days: int = 7
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class SellNearHighConfig:
    enabled: bool = True
    ath_threshold_pct: float = 0.85
    long_term_only: bool = True
    min_history_days: int = 365   # Require at least this many calendar days of data
    profitable_only: bool = True  # Skip if current price < average cost


@dataclass
class ExitMomentumConfig:
    enabled: bool = True
    # Exit-now tier: fire urgent on any signal
    exit_now_rsi_threshold: float = 65.0    # Fire when RSI >= this
    exit_now_day_pop_pct: float = 0.02      # Or 2% daily pop
    exit_now_five_day_pct: float = 0.04     # Or 4% 5-day
    exit_now_consecutive_days: int = 2      # Or 2+ consecutive up days
    # Exit-soon / sell-on-rally tiers: slightly higher bar
    rally_rsi_threshold: float = 60.0
    rally_day_pop_pct: float = 0.03
    rally_five_day_pct: float = 0.06
    rally_consecutive_days: int = 3
    # Analyst proximity gate for profitable positions:
    # Suppress alert if analyst upside > this. Default 7% = only alert when ≤7% upside remains.
    profitable_max_analyst_upside: float = 0.07
    # Break-even gate for loss positions:
    # Fire when price >= avg_cost × (1 + break_even_buffer). Default 0.0 = exactly at break-even.
    break_even_buffer: float = 0.0


@dataclass
class BounceAlertConfig:
    enabled: bool = True
    rsi_recovery_threshold: float = 35.0   # Fire when RSI >= this (recovering from oversold)
    consecutive_up_days: int = 3            # Or 3+ consecutive up days
    five_day_pct: float = 0.05             # Or 5% 5-day rally
    ma50_reclaim_as_trigger: bool = True    # Phase 3: also trigger when price reclaims 50-day average
    # Stricter gates (disabled by default — enable per-portfolio in config)
    require_consecutive_days: bool = False  # When True: consecutive up days must be present (AND, not OR)
    require_near_breakeven: bool = False    # When True: only fire if price is within near_breakeven_threshold of cost
    near_breakeven_threshold: float = 0.10 # "Near breakeven" = price >= avg_cost × (1 - this). 0.10 = within 10%


@dataclass
class MaCrossoverConfig:
    """Phase 4 — golden/death cross detection for held India positions."""
    enabled: bool = True
    cooldown_days: int = 30    # Crossovers are rare; suppress re-alerts for 30 days


@dataclass
class DataSourcesConfig:
    ath_history_period: str = "max"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    retain_days: int = 90


@dataclass
class AppConfig:
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    sell_near_high: SellNearHighConfig = field(default_factory=SellNearHighConfig)
    exit_momentum: ExitMomentumConfig = field(default_factory=ExitMomentumConfig)
    bounce_alert: BounceAlertConfig = field(default_factory=BounceAlertConfig)
    ma_crossover: MaCrossoverConfig = field(default_factory=MaCrossoverConfig)
    data_sources: DataSourcesConfig = field(default_factory=DataSourcesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    portfolio_owner: str = ""   # Display name shown in email header, e.g. "Surender Kaur"
    zerodha_id: str = ""        # Zerodha client ID shown in email header, e.g. "XFG529"


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) or {}

    email_raw = raw.get("alerts", {}).get("email", {})
    email_cfg = EmailConfig(
        smtp_host=email_raw.get("smtp_host", "smtp.gmail.com"),
        smtp_port=int(email_raw.get("smtp_port", 587)),
        from_address=email_raw.get("from_address", ""),
        to_address=email_raw.get("to_address", ""),
    )
    alerts_raw = raw.get("alerts", {})
    alerts_cfg = AlertsConfig(
        cooldown_days=int(alerts_raw.get("cooldown_days", 7)),
        email=email_cfg,
    )

    snh_raw = raw.get("sell_near_high", {})
    snh_cfg = SellNearHighConfig(
        enabled=bool(snh_raw.get("enabled", True)),
        ath_threshold_pct=float(snh_raw.get("ath_threshold_pct", 0.85)),
        long_term_only=bool(snh_raw.get("long_term_only", True)),
        min_history_days=int(snh_raw.get("min_history_days", 365)),
        profitable_only=bool(snh_raw.get("profitable_only", True)),
    )

    em_raw = raw.get("exit_momentum", {})
    em_cfg = ExitMomentumConfig(
        enabled=bool(em_raw.get("enabled", True)),
        exit_now_rsi_threshold=float(em_raw.get("exit_now_rsi_threshold", 65.0)),
        exit_now_day_pop_pct=float(em_raw.get("exit_now_day_pop_pct", 0.02)),
        exit_now_five_day_pct=float(em_raw.get("exit_now_five_day_pct", 0.04)),
        exit_now_consecutive_days=int(em_raw.get("exit_now_consecutive_days", 2)),
        rally_rsi_threshold=float(em_raw.get("rally_rsi_threshold", 60.0)),
        rally_day_pop_pct=float(em_raw.get("rally_day_pop_pct", 0.03)),
        rally_five_day_pct=float(em_raw.get("rally_five_day_pct", 0.06)),
        rally_consecutive_days=int(em_raw.get("rally_consecutive_days", 3)),
        profitable_max_analyst_upside=float(em_raw.get("profitable_max_analyst_upside", 0.07)),
        break_even_buffer=float(em_raw.get("break_even_buffer", 0.0)),
    )

    ba_raw = raw.get("bounce_alert", {})
    ba_cfg = BounceAlertConfig(
        enabled=bool(ba_raw.get("enabled", True)),
        rsi_recovery_threshold=float(ba_raw.get("rsi_recovery_threshold", 35.0)),
        consecutive_up_days=int(ba_raw.get("consecutive_up_days", 3)),
        five_day_pct=float(ba_raw.get("five_day_pct", 0.05)),
        ma50_reclaim_as_trigger=bool(ba_raw.get("ma50_reclaim_as_trigger", True)),
        require_consecutive_days=bool(ba_raw.get("require_consecutive_days", False)),
        require_near_breakeven=bool(ba_raw.get("require_near_breakeven", False)),
        near_breakeven_threshold=float(ba_raw.get("near_breakeven_threshold", 0.10)),
    )

    mac_raw = raw.get("ma_crossover", {})
    mac_cfg = MaCrossoverConfig(
        enabled=bool(mac_raw.get("enabled", True)),
        cooldown_days=int(mac_raw.get("cooldown_days", 30)),
    )

    ds_raw = raw.get("data_sources", {})
    ds_cfg = DataSourcesConfig(
        ath_history_period=ds_raw.get("ath_history_period", "max"),
    )

    log_raw = raw.get("logging", {})
    log_cfg = LoggingConfig(
        level=log_raw.get("level", "INFO"),
        retain_days=int(log_raw.get("retain_days", 90)),
    )

    portfolio_raw = raw.get("portfolio", {})

    return AppConfig(
        alerts=alerts_cfg,
        sell_near_high=snh_cfg,
        exit_momentum=em_cfg,
        bounce_alert=ba_cfg,
        ma_crossover=mac_cfg,
        data_sources=ds_cfg,
        logging=log_cfg,
        portfolio_owner=str(portfolio_raw.get("owner", "")),
        zerodha_id=str(portfolio_raw.get("zerodha_id", "")),
    )
