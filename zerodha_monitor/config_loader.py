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
    data_sources: DataSourcesConfig = field(default_factory=DataSourcesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


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

    ds_raw = raw.get("data_sources", {})
    ds_cfg = DataSourcesConfig(
        ath_history_period=ds_raw.get("ath_history_period", "max"),
    )

    log_raw = raw.get("logging", {})
    log_cfg = LoggingConfig(
        level=log_raw.get("level", "INFO"),
        retain_days=int(log_raw.get("retain_days", 90)),
    )

    return AppConfig(
        alerts=alerts_cfg,
        sell_near_high=snh_cfg,
        data_sources=ds_cfg,
        logging=log_cfg,
    )
