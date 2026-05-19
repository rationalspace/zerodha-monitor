"""Tests for holdings_loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zerodha_monitor.holdings_loader import Holding, load_holdings


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "holdings.yaml"
    p.write_text(yaml.dump(data))
    return p


class TestLoadHoldings:
    def test_loads_all_fields(self, tmp_path):
        p = _write(tmp_path, {"holdings": [{
            "symbol": "infy", "quantity": 100, "average_cost": 900.0,
            "sector": "IT", "long_term": True,
        }]})
        holdings = load_holdings(p)
        assert len(holdings) == 1
        h = holdings[0]
        assert h.symbol == "INFY"           # uppercased
        assert h.quantity == 100
        assert h.average_cost == 900.0
        assert h.sector == "IT"
        assert h.long_term is True

    def test_ns_symbol(self, tmp_path):
        p = _write(tmp_path, {"holdings": [
            {"symbol": "TCS", "quantity": 10, "average_cost": 3000.0}
        ]})
        h = load_holdings(p)[0]
        assert h.ns_symbol == "TCS.NS"

    def test_cost_basis_total(self, tmp_path):
        p = _write(tmp_path, {"holdings": [
            {"symbol": "SBIN", "quantity": 500, "average_cost": 625.60}
        ]})
        h = load_holdings(p)[0]
        assert h.cost_basis_total == pytest.approx(500 * 625.60)

    def test_long_term_defaults_false(self, tmp_path):
        p = _write(tmp_path, {"holdings": [
            {"symbol": "RELIANCE", "quantity": 10, "average_cost": 2500.0}
        ]})
        h = load_holdings(p)[0]
        assert h.long_term is False

    def test_missing_required_field_raises(self, tmp_path):
        p = _write(tmp_path, {"holdings": [{"symbol": "TCS", "quantity": 10}]})
        with pytest.raises(ValueError, match="average_cost"):
            load_holdings(p)

    def test_missing_holdings_key_raises(self, tmp_path):
        p = tmp_path / "holdings.yaml"
        p.write_text("something: else\n")
        with pytest.raises(ValueError, match="holdings"):
            load_holdings(p)

    def test_multiple_holdings(self, tmp_path):
        p = _write(tmp_path, {"holdings": [
            {"symbol": "INFY", "quantity": 100, "average_cost": 900.0, "long_term": True},
            {"symbol": "TCS",  "quantity": 50,  "average_cost": 3500.0, "long_term": False},
        ]})
        holdings = load_holdings(p)
        assert len(holdings) == 2
        syms = {h.symbol for h in holdings}
        assert syms == {"INFY", "TCS"}

    def test_real_holdings_yaml_loads(self):
        """Smoke test against the actual project holdings.yaml."""
        root = Path(__file__).resolve().parent.parent
        p = root / "holdings.yaml"
        if not p.exists():
            pytest.skip("holdings.yaml not found")
        holdings = load_holdings(p)
        assert len(holdings) == 29
        symbols = {h.symbol for h in holdings}
        assert "INFY" in symbols
        assert "SBIN" in symbols
        assert all(h.long_term for h in holdings), "All holdings should be long_term"
