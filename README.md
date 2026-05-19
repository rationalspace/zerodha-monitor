# India Monitor (NSE — Zerodha)

Personal alert system for 30 Indian equity holdings on Zerodha/NSE. Runs daily after Indian market close via macOS launchd and emails when a stock approaches its all-time high — the primary signal to consider selling LTCG-eligible positions.

## What it does

Monitors 30 NSE holdings and fires a **Gmail alert** when any stock's closing price reaches ≥85% of its all-time high — indicating a potential exit window, especially for long-term capital gains (LTCG) eligible lots.

Alert includes:
- Current price vs ATH (₹ and %)
- **Day % change + ₹ value change** (total position impact)
- 52-week high for context
- Unrealized P&L (% and ₹)
- Position value and cost basis
- LTCG reminder (10% tax above ₹1L annual gains)

## The rule

```
sell_near_high:
  ath_threshold_pct: 0.85     # Fire at ≥85% of all-time high
  long_term_only: true        # Only LTCG-eligible holdings
  min_history_days: 365       # Need ≥1yr of data (filters unreliable ATH)
  profitable_only: true       # Skip if current price < average cost
```

Four guard conditions before an alert fires:
1. Price ≥ 85% of ATH (configurable)
2. At least 365 days of price history (short-history = unreliable ATH)
3. Current price > average cost (no point flagging underwater positions)
4. Not in 7-day cooldown for this symbol

## Schedule

- **Indian market closes** 3:30 PM IST = 10:00 UTC = ~5:00 AM CDT
- **launchd fires** every 30 min from 5:30–9:00 AM CDT, Mon–Fri
- **Catch-up logic**: if Mac was asleep, fires on next wake-up
- **Sentinel file** (`~/.india-monitor-last-run`) ensures one run per day max
- **Cooldown store** (`~/.india-monitor-state.db`) — 7-day cooldown per symbol

## Architecture

```
holdings.yaml  ──► holdings_loader.py
config.yaml    ──► config_loader.py
                        │
                        ▼
              market_data.py (yfinance .NS/.BO)
                        │
                        ▼
           rules/sell_near_high.py
                        │
                        ▼
              store.py (SQLite cooldown)
                        │
                        ▼
           email_dispatch.py → Gmail SMTP
```

## Holdings

All 30 holdings are in `holdings.yaml` with symbol, quantity, average cost, sector, and `long_term: true`. Update this file manually when you buy or sell.

```yaml
holdings:
  - symbol: INFY
    quantity: 100
    average_cost: 900.0
    sector: IT
    long_term: true
```

## Setup

### Install
```bash
cd ~/india-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Secrets (macOS Keychain — shared with US monitor)
```bash
# Gmail (reuses portfolio-monitor keychain service)
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_address', 'you@gmail.com')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_app_password', 'xxxx-xxxx-xxxx-xxxx')"
```

### Configure
Edit `config.yaml` to tune thresholds. Edit `holdings.yaml` to update positions.

### Dry run
```bash
python -m india_monitor.scripts.run_guarded --dry-run
```

### Install launchd job
```bash
cp launchd/com.indiamonitor.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.indiamonitor.daily.plist
launchctl list com.indiamonitor.daily   # verify loaded
```

### Logs
```
~/Library/Logs/india-monitor.log
```

## Project layout

```
india-monitor/
├── holdings.yaml               # 30 NSE positions (edit when you buy/sell)
├── config.yaml                 # Rule thresholds (edit freely)
├── launchd/
│   └── com.indiamonitor.daily.plist
├── india_monitor/
│   ├── main.py                 # Daily orchestration
│   ├── config_loader.py        # YAML → AppConfig
│   ├── holdings_loader.py      # holdings.yaml → Holding dataclasses
│   ├── market_data.py          # yfinance .NS/.BO with ATH + day change
│   ├── store.py                # SQLite cooldown log
│   ├── email_dispatch.py       # Jinja2 HTML → Gmail SMTP
│   ├── secrets.py              # macOS Keychain wrapper
│   ├── rules/
│   │   └── sell_near_high.py   # ATH proximity rule with all guards
│   ├── templates/
│   │   └── india_alert.html.j2 # HTML email template
│   └── scripts/
│       └── run_guarded.py      # IST time-gate + sentinel → main.run_once()
└── tests/                      # 27 tests, no network required
    ├── test_holdings_loader.py
    └── test_sell_near_high.py
```

## Tests
```bash
pytest tests/   # 27 tests, all offline
```

---

*This is a notification tool, not a financial advisor. LTCG tax is 10% above ₹1 lakh annual gains — review your full LTCG book before deciding to sell. Nothing here is investment, tax, or legal advice.*
