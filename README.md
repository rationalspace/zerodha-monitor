# Zerodha Monitor

Alert system for NSE/BSE equity holdings on Zerodha. Runs daily after Indian market close via macOS launchd and sends an HTML digest email covering all active holdings — exit signals, recovery signals, and trend changes.

## What it does

Monitors your NSE holdings across four rules and sends a single **HTML digest email** after each trading day, with per-holding alerts when conditions are met. Covers the full lifecycle: when to hold, when to bounce-exit, when a recovery is real, and when a trend is deteriorating.

## The 4 rules

| # | Rule | Applies to | Fires when |
|---|---|---|---|
| 1 | **Sell Near High** | Long-term profitable holdings | Price ≥ 85% of all-time high — LTCG-eligible exit window |
| 2 | **Exit Momentum** | `exit_now`, `exit_soon`, `sell_on_rally` tiers | RSI surge, day pop, 5-day rally, or consecutive up days — hunt the exit |
| 3 | **Bounce Alert** | `wait_for_bounce` tier | RSI recovering, consecutive up days, 5-day rally, or MA50 reclaimed — with bounce quality tier |
| 4 | **MA Crossover** | All held positions | 50-day average crosses 200-day — golden or death cross |

### Rule 3 — Bounce Alert quality tiers

When a `wait_for_bounce` stock shows recovery signals, the alert title and severity reflect how strong the recovery actually is:

| Tier | Condition | Example title |
|---|---|---|
| `MA50_RECLAIM` | Price above 50-day average | "DMART — Recovery confirmed: past the 50-day average — exit into this strength" |
| `ABOVE_MA200` | Above 200-day, not yet at MA50 | "DMART — Bounce underway, long-term trend intact — exit window opening" |
| `BELOW_BOTH_MAS` | Below both averages | "DMART — Recovery starting, still below key averages — watch for confirmation" |

MA50 reclaim is also an explicit trigger: *"Above 50-day average (₹4,323) — key recovery milestone"*

### Rule 4 — MA Crossover

Detects the exact day the 50-day average crosses the 200-day average:

- **Golden cross** on `wait_for_bounce`: `HIGH` — *"Recovery fully confirmed — exit into this strength"*
- **Golden cross** on other holdings: `MEDIUM` — informational, recovery confirmed
- **Death cross** on exit-tier holdings: `HIGH` — *"Trend worsening — consider acting sooner"*
- **Death cross** on other holdings: `MEDIUM` — watch signal

30-day cooldown prevents re-alerting when averages hover near each other.

## Technical indicators

Every `PriceSnapshot` carries technical indicators computed at no extra API cost:

| Field | Source | What it means |
|---|---|---|
| `ma50` | tail-50 mean of price history | 50-day simple moving average (₹) |
| `ma200` | tail-200 mean of price history | 200-day simple moving average (₹) |
| `bb_upper` / `bb_lower` | 20-day Bollinger Bands (2σ), pandas rolling | Upper/lower bounds of the 20-day price range |
| `bb_pct_b` | `(price − bb_lower) / (bb_upper − bb_lower)` | 0 = at floor, 0.5 = mid, 1 = at ceiling |
| `above_ma50` / `above_ma200` | Boolean flags | Is price above the 50/200-day average? |

Historical snapshots (`snapshot_for_date`) compute technicals from the price slice up through the target date — accurate for backfill runs.

## Digest email

The daily email is a single HTML table grouped by profitable vs. loss positions, sorted by analyst upside. Each row shows:

- Symbol, tier badge (color-coded by exit urgency)
- Trigger description + **bounce quality badge** for `wait_for_bounce` stocks
- Price, day change %, unrealized gain/loss
- LTCG tax estimate, analyst target + upside, analyst recommendation
- % of ATH

## Holdings tier system

Holdings are classified in `holdings.yaml` (kept local) by `exit_tier`:

| Tier | Description |
|---|---|
| `exit_now` | Exit immediately — fire on any upward signal |
| `exit_soon` | Exit in the near term — moderate signal threshold |
| `sell_on_rally` | Exit when price rallies to target — higher bar |
| `wait_for_bounce` | Underwater / stuck — wait for recovery, then exit into it |
| (none) | Profitable hold — `sell_near_high` only |

## Architecture

```
holdings.yaml  ──► holdings_loader.py
config.yaml    ──► config_loader.py
                        │
                        ▼
   yfinance (.NS/.BO) ──► market_data.py
   (prices, ATH, MA50/200,   (IndiaMarketData)
    Bollinger Bands, RSI,          │
    fundamentals, analysts)        ▼
                      rules/ (4 rules)
                              │
                              ▼
                    store.py (per-date dedup)
                              │
                              ▼
              email_dispatch.py → Gmail SMTP
                (india_digest.html.j2)
```

## Scheduling

- **Indian market closes** 3:30 PM IST = ~5:00 AM CDT
- **launchd fires** every 30 min from 5:30–9:00 AM CDT, Mon–Fri
- **Catch-up logic**: fires on Mac wake-up if the window was missed
- **Per-date deduplication**: each trading date's digest is only sent once
- **7-day cooldown** per (symbol, rule) pair — MA Crossover uses 30-day cooldown

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

### Configure holdings
`holdings.yaml` is kept local (contains your private positions). Copy the template:
```bash
cp holdings.example.yaml holdings.yaml   # then fill in your NSE tickers, quantities, costs
```

A `realized_pnl.yaml` ledger tracks closed positions (also kept local). Copy from `realized_pnl.example.yaml`.

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
    <!-- fires every 30 min, 5:30–9:00 AM CDT (after NSE close at ~5:00 AM CDT) -->
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

### Dry run
```bash
python -m zerodha_monitor.scripts.run_guarded --dry-run
```

### Logs
```
~/Library/Logs/zerodha-monitor.log
```

## Configuration

Edit `config.yaml` to tune thresholds. Key knobs:

```yaml
bounce_alert:
  rsi_recovery_threshold: 35    # Fire when RSI recovers above this
  consecutive_up_days: 3        # Or 3+ consecutive up days
  five_day_pct: 0.05            # Or 5% 5-day rally
  ma50_reclaim_as_trigger: true # Also trigger when price reclaims MA50

ma_crossover:
  enabled: true
  cooldown_days: 30
```

## Project layout

```
zerodha-monitor/
├── holdings.yaml                  # Your positions (local — copy from holdings.example.yaml)
├── realized_pnl.yaml              # Closed position ledger (local)
├── config.yaml                    # Rule thresholds (edit freely)
├── zerodha_monitor/
│   ├── main.py                    # Daily orchestration; per-date backfill loop
│   ├── config_loader.py           # YAML → AppConfig (BounceAlertConfig, MaCrossoverConfig...)
│   ├── holdings_loader.py         # holdings.yaml → Holding dataclasses (sector, exit_tier, cost basis)
│   ├── market_data.py             # yfinance .NS/.BO: prices, ATH, MA50/200, BB, RSI, fundamentals
│   ├── store.py                   # SQLite: per-date alert dedup + cooldown log
│   ├── email_dispatch.py          # Jinja2 HTML → Gmail SMTP
│   ├── secrets.py                 # macOS Keychain wrapper
│   ├── rules/
│   │   ├── sell_near_high.py      # Rule 1 — ATH proximity (long-term profitable)
│   │   ├── exit_momentum.py       # Rule 2 — momentum exit (exit_now / exit_soon / sell_on_rally)
│   │   ├── bounce_alert.py        # Rule 3 — bounce quality tiers (wait_for_bounce)
│   │   └── ma_crossover.py        # Rule 4 — golden/death cross for all holdings
│   ├── templates/
│   │   ├── india_alert.html.j2    # Per-alert email (position detail, triggers, P&L)
│   │   └── india_digest.html.j2   # Daily digest (profitable vs loss tables, bounce badges)
│   └── scripts/
│       └── run_guarded.py         # IST time-gate + sentinel → main.run_once()
└── tests/                         # All offline — no network required
```

## Tests
```bash
pytest tests/   # all offline
```

---

*This is a notification tool, not a financial advisor. LTCG tax is 12.5% above ₹1.25 lakh annual gains (post Jul-2024). Review your full LTCG book before deciding to sell. Nothing here is investment, tax, or legal advice.*
