# Zerodha Monitor

Alert utility for NSE/BSE equity holdings on Zerodha. Runs daily after Indian market close via macOS launchd and emails when a stock approaches its all-time high — the primary signal to consider selling LTCG-eligible positions.

## What it does

Monitors your NSE holdings and fires a **Gmail alert** when any stock's closing price reaches ≥85% of its all-time high — indicating a potential exit window, especially for long-term capital gains (LTCG) eligible lots.

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
- **Sentinel file** (`~/.zerodha-monitor-last-run`) ensures one run per day max
- **Cooldown store** (`~/.zerodha-monitor-state.db`) — 7-day cooldown per symbol

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

## Setup

### Install
```bash
cd ~/zerodha-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Secrets (macOS Keychain)
```bash
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_address', 'you@gmail.com')"
python -c "import keyring; keyring.set_password('portfolio-monitor', 'gmail_app_password', 'xxxx-xxxx-xxxx-xxxx')"
```

### Configure
Edit `config.yaml` to tune thresholds.

`holdings.yaml` is git-ignored (contains your private positions). Copy the template to get started:

```bash
cp holdings.example.yaml holdings.yaml   # then fill in your NSE tickers
```

A `realized_pnl.yaml` ledger tracks closed positions (also git-ignored). Copy from `realized_pnl.example.yaml` to start your own.

### Dry run
```bash
python -m zerodha_monitor.scripts.run_guarded --dry-run
```

### Schedule (macOS launchd)

Create `~/Library/LaunchAgents/com.zerodhamonitor.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.zerodhamonitor.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USERNAME/zerodha-monitor/.venv/bin/python</string>
    <string>-m</string>
    <string>zerodha_monitor.scripts.run_guarded</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOUR_USERNAME/zerodha-monitor</string>
  <key>StartCalendarInterval</key>
  <array>
    <!-- fires every 30 min, 5:30–9:00 AM CDT (after NSE close at 5:00 AM CDT) -->
    <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/Users/YOUR_USERNAME/Library/Logs/zerodha-monitor.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_USERNAME/Library/Logs/zerodha-monitor.log</string>
</dict>
</plist>
```

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.zerodhamonitor.daily.plist
launchctl list com.zerodhamonitor.daily   # verify loaded
```

### Logs
```
~/Library/Logs/zerodha-monitor.log
```

## Project layout

```
zerodha-monitor/
├── holdings.yaml               # Your positions (git-ignored — copy from holdings.example.yaml)
├── realized_pnl.yaml           # Closed position ledger (git-ignored — copy from realized_pnl.example.yaml)
├── config.yaml                 # Rule thresholds (edit freely)
├── zerodha_monitor/
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
└── tests/                      # Tests, no network required
    ├── test_holdings_loader.py
    └── test_sell_near_high.py
```

## Tests
```bash
pytest tests/   # all offline
```

---

*This is a notification tool, not a financial advisor. LTCG tax is 10% above ₹1 lakh annual gains — review your full LTCG book before deciding to sell. Nothing here is investment, tax, or legal advice.*
