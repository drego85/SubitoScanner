# Subito Scanner & Notifier

## Overview

Subito Scanner is a Python tool that automatically monitors [Subito.it](https://www.subito.it) for new listings matching your search queries. Since Subito does not offer real-time alerts, this project fills the gap by sending notifications via **e-mail**, **Slack**, or **Telegram** as soon as a new matching item appears.

The scanner tracks already-seen items to prevent duplicate notifications, and can be controlled interactively through Telegram bot commands.

## Features

- **Automated search** — runs multiple custom Subito queries on every execution
- **Multi-channel notifications** — e-mail, Slack, and/or Telegram
- **Duplicate prevention** — item history is persisted per query in `scanner_state.json`
- **Telegram bot controls** — guided add wizard, edit, pause/resume, wipe; plain-text terms offer one-tap setup
- **Dry-run mode** — preview results without sending any notifications
- **Real-time bot responses** — persistent systemd service for instant command replies
- **Periodic execution** — hourly cron job for new listing scans

## Project Structure

```
SubitoScanner/
├── main.py               ← entry point
├── Config.py             ← non-secret settings (queries, api urls)
├── .env.example          ← template for secrets (copy → .env)
├── .env                  ← your secrets (gitignored — never commit)
├── subito-bot.service    ← systemd unit template (optional, always-on bot)
├── tests/                ← offline unit / smoke tests
└── scanner/
    ├── constants.py      ← HTTP timeout / browser headers
    ├── regions.py        ← Italian region ids + aliases
    ├── query.py          ← build / parse / patch Subito query strings
    ├── utils.py          ← compat re-exports of the above
    ├── state.py          ← persistent state (items seen, paused flag, etc.)
    ├── notifiers.py      ← EmailNotifier, SlackNotifier, TelegramNotifier
    ├── core.py           ← main scan loop
    └── bot/              ← Telegram bot (package)
        ├── app.py        ← TelegramBot facade (poll / dispatch)
        ├── keyboards.py  ← reply / inline keyboards
        ├── formatting.py ← list/status markup helpers
        ├── help.py       ← interactive help
        ├── wizard.py     ← guided add flow
        ├── callbacks.py  ← inline button handlers
        └── commands.py   ← slash / button commands
```

## Getting Started

### What you need to edit

| Setup | Files to edit | Result |
|-------|----------------|--------|
| **Minimum** (try it once) | `.env` only | Run `python3 main.py` by hand |
| **Recommended** (24/7) | `.env` + `subito-bot.service` + cron | Instant Telegram replies + hourly Subito scans |
| **Optional** | `Config.py` | Seed search queries on first run (or just use `/add` in Telegram later) |

- **`.env`** — always required for Telegram (bot token + chat id). Secrets stay here; never commit this file.
- **`subito-bot.service`** — only if you want the bot always listening. Copy to systemd and set your user + project path.
- **cron** — only if you want automatic scans on a schedule (use `--scan-only` when the bot service is running).
- **`Config.py`** — optional seed queries; after the first run, manage searches from Telegram.

### Prerequisites

- Python 3.8 or higher
- A machine that stays on if you want 24/7 monitoring (e.g. a Raspberry Pi)
- Linux with systemd + cron for the recommended setup

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/enricollen/SubitoScanner.git
   cd SubitoScanner
   ```

2. Install dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```

3. Create your secrets file and fill it in (see [Configuration](#configuration)):
   ```bash
   cp .env.example .env
   nano .env
   ```

4. **Quick test** (no service/cron yet):
   ```bash
   python3 main.py --dry-run
   ```
   If that works, either keep running manually, or continue with the [bot service](#bot-service-real-time-telegram-commands) and [cron](#automate-the-scanner-with-cron) sections below for 24/7 use.

## Configuration

Secrets live in `.env` (never committed). Non-secret settings like search queries live in `Config.py`.

### Email (optional)

In `.env`:

```
SMTP_USERNAME=your_email@example.com
SMTP_PASSWORD=your_password
SMTP_SERVER=smtp.example.com
SMTP_TOADDRS=Recipient <recipient@example.com>
```

### Slack (optional)

In `.env`:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### Telegram (optional but recommended)

**Step 1 — Create a bot**
- Open Telegram and search for `@BotFather`
- Send `/newbot` and follow the prompts
- Copy the **bot token** BotFather gives you (e.g. `123456789:ABCdef...`)

**Step 2 — Get your chat ID**
- Start a conversation with your bot (search for it and press **Start**)
- Run the following, replacing `YOUR_BOT_TOKEN`:
  ```bash
  curl https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
  ```
- Find `"chat":{"id":123456789}` in the response — that number is your **chat ID**

**Step 3 — Add to `.env`**
```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Search Queries

Define the initial queries in `Config.py`. They are used as a **seed** on the very first run and written into `scanner_state.json`. After that, `scanner_state.json` is the source of truth — you can add and remove queries at any time via the Telegram bot without touching `Config.py`.

```python
queries = [
    "q=raspberry+pi+4&t=s&qso=true&sort=datedesc&lim=10&start=0",
    "q=iphone+15&t=s&qso=false&r=9&sort=datedesc&lim=10&start=0",  # r=9 → Toscana
]
```

| Parameter | Description |
|-----------|-------------|
| `q`       | Search term — replace spaces with `+` |
| `t`       | Listing type: `s` sale · `g` gift · `u` rental · `h` vacation rental · `k` wanted |
| `shp`     | Shipping only (`true`/`false`). **Omit** for all listings — `true` hides most vehicles |
| `qso`     | Title-only / exact keywords (`true` = Subito "cerca solo nel titolo") |
| `r`       | Region id (optional). `9` = Toscana. Omit for all Italy. See table below or `/regions` in Telegram |
| `ps`      | Minimum price in euros (optional) |
| `pe`      | Maximum price in euros (optional) |
| `sort`    | Sort order — use `datedesc` for newest first |
| `lim`     | Results per page (e.g. `10`) |
| `start`   | Pagination offset (use `0` for the first page) |

**Where / price via Telegram**

```
/add wd red in toscana
/add sh 125 min 500 max 2000
/add sh 125 500-2000 in toscana
/exact wd red in toscana min 50
/add macbook pro          ← all Italy (no region / any price)
/edit 1 in toscana        ← add region to existing search #1
/edit 1 min 500 max 2000  ← then add price (keeps region)
/edit 1 anywhere          ← remove region filter
/regions                  ← full list of region names
```

| `r=` | Region |
|------|--------|
| 1 | Valle d'Aosta |
| 2 | Piemonte |
| 3 | Liguria |
| 4 | Lombardia |
| 5 | Trentino-Alto Adige |
| 6 | Veneto |
| 7 | Friuli-Venezia Giulia |
| 8 | Emilia-Romagna |
| 9 | Toscana |
| 10 | Umbria |
| 11 | Lazio |
| 12 | Marche |
| 13 | Abruzzo |
| 14 | Molise |
| 15 | Campania |
| 16 | Puglia |
| 17 | Basilicata |
| 18 | Calabria |
| 19 | Sardegna |
| 20 | Sicilia |

English aliases work too (`tuscany`, `lombardy`, `sicily`, …).

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│  subito-bot.service  (systemd, always on)               │
│                                                         │
│  long-polls Telegram every 30s ──► replies instantly    │
│  reloads state from disk on each cycle                  │
└─────────────────────────────────────────────────────────┘
                         │ reads/writes scanner_state.json
┌─────────────────────────────────────────────────────────┐
│  cron  (hourly, --scan-only)                            │
│                                                         │
│  fetches Subito listings ──► sends item notifications   │
│  saves new item IDs to scanner_state.json               │
└─────────────────────────────────────────────────────────┘
```

Both processes share `scanner_state.json` — the bot service reloads it on every poll cycle so it always reflects the latest scanner data.

## Usage

### Run manually

```bash
python3 main.py
```

### Dry-run mode

Preview results without sending any notifications — useful when testing new queries:

```bash
python3 main.py --dry-run
```

### Bot service (real-time Telegram commands)

Optional, but recommended for 24/7 use. The service only **listens** for Telegram commands; scans are still done by cron (or manual runs).

1. Edit the template in the repo (or edit after copying) — set your Linux user and absolute paths:
   ```bash
   nano subito-bot.service
   ```
   Replace `YOUR_USER` and `/path/to/SubitoScanner` (and the Python path in `ExecStart` if you use a venv/conda).

2. Install and start it:
   ```bash
   sudo cp subito-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable subito-bot
   sudo systemctl start subito-bot
   ```

3. Confirm it’s running, then send `/start` in Telegram:
   ```bash
   sudo systemctl status subito-bot
   ```

View live logs:

```bash
journalctl -u subito-bot -f
```

> **After any code update**, restart the service to load the new version:
> ```bash
> sudo systemctl restart subito-bot
> ```
> The cron job picks up changes automatically since it spawns a fresh process each time.

### Automate the scanner with cron

If the **bot service** is running, add a cron line with `--scan-only` so cron only fetches Subito listings (the service already handles Telegram):

```
0 * * * * YOUR_USER cd /path/to/SubitoScanner && /usr/bin/python3 /path/to/SubitoScanner/main.py --scan-only
```

Replace `YOUR_USER` and `/path/to/SubitoScanner` (and the Python binary if needed). Example: run at minute 0 of every hour.

> If you are **not** using the bot service, omit `--scan-only` — each cron run will both scan and do a single Telegram poll.

## Telegram Bot Commands

Open the chat with your bot and tap **/** (or type a command). The menu is registered automatically on every run.

| Command | What it does |
|---------|----------------|
| `/start` | Home overview |
| `/help` | Interactive guide (topic buttons) |
| `/list` | Your searches with Pause / Edit / Delete |
| `/status` | Status + Mute / Pause all / Delete all… |
| `/scan` | Run a Subito check **now** |
| `/add` | **Guided setup** (same as ➕ New): term → region → price → mode |
| `/add <term>` | Track a broad search (all Italy), e.g. `/add macbook pro` |
| `/add <term> in <region>` | Limit where to look, e.g. `/add wd red in toscana` |
| `/add <term> min <n> max <n>` | Price filter in €, e.g. `/add sh 125 min 500 max 2000` |
| `/add <term> <min>-<max>` | Price shorthand, e.g. `/add sh 125 500-2000 in toscana` |
| `/exact <term> [in <region>]` | Title-only / exact keywords, e.g. `/exact wd red in toscana` |
| `/edit <n> [filters…]` | Change an existing search — keep the term, add/change filters |
| `/cancel` | Abort the guided add wizard |
| `/regions` | List Italian regions you can use with `in …` |
| `/stop <n>` | Pause search #n (clears its history — resume notifies fresh) |
| `/stopall` | Pause **all** searches (`/stop all` also works) |
| `/resume <n>` | Re-enable a stopped search |
| `/resumeall` | Resume **all** stopped searches (`/resume all` / `/startall` also work) |
| `/remove <n>` | Delete search #n permanently |
| `/wipe` | Delete **all** searches (inline confirmation) |
| `/pause` | Mute all alerts (scanning still runs) |
| `/unpause` | Unmute alerts |

**Tips**
- Bottom keyboard: **➕ New** · **📋 Searches** · **🔎 Scan** · **📡 Status** · **📖 Help** (send `/start` to refresh).
- Tap **➕ New** for guided setup with progress, Back, price presets, and a live draft.
- Type a plain term (e.g. `iphone 15`) → **Continue setup** or **Add · All Italy**.
- **Searches**: Pause / Edit / Delete per item (delete asks to confirm). Bulk actions at the bottom.
- **Status**: Mute alerts, Pause/Resume all, Delete all… with confirmation.
- `/help` opens topic buttons instead of one long wall of text.
- `/edit 1 in toscana` then `/edit 1 min 500` — filters stack onto the existing search.
- `/add exact <term>` still works as an alias of `/exact`.
- Use `/exact` when broad search is too noisy (Subito *cerca solo nel titolo* / `qso=true`).
- Use `in toscana` (or any name from `/regions`) to search in one region only (`r=` in the API).
- Use `min 100 max 500` or `100-500` for price filters (`ps=` / `pe=` in the API).
- `/scan` triggers an immediate Subito check; cron still runs on schedule in the background.

> **Security:** only messages from the configured `TELEGRAM_CHAT_ID` are processed. Anyone else who messages the bot gets no response.

## State & Persistence

All runtime state is stored in `scanner_state.json`:

```json
{
  "paused": false,
  "queries": ["q=wd+red&t=s&...", "q=seagate+ironwolf&t=s&..."],
  "disabled_queries": [],
  "last_update_id": 0,
  "items_by_query": {
    "q=wd+red&t=s&...": ["item_id_1", "item_id_2"],
    "q=seagate+ironwolf&t=s&...": ["item_id_3"]
  }
}
```

- `queries` is the live list — edited via `/add`, `/edit` and `/remove`, never via `Config.py` after the first run.
- `items_by_query` uses the query string as key, so adding or removing queries never corrupts the history of other queries.
- `/stop` and `/edit` clear a query's item history so the next scan notifies you of current listings fresh.

**Upgrading from a previous version:** if a `subito_items.txt` file is present it is automatically migrated into `scanner_state.json` on the first run and renamed to `subito_items.txt.bak`.

## Logging

Logs go to `subito_scanner.log` with automatic **size-based rotation**:

- each file is capped at **2 MB**
- up to **3** backups are kept (`.log.1`, `.log.2`, `.log.3`)
- when the active log fills up, the oldest backup is deleted

Max disk use is about **6 MB**. Log files are gitignored and never committed.

To wipe logs manually on the device:

```bash
rm -f subito_scanner.log*
sudo systemctl restart subito-bot   # if the bot service is running
```

## Contributing and Supporting the Project

1. **Development contributions** — pull requests are welcome; please follow existing code style and structure.

2. **Donation support** — if you find this project useful, you can support it via [Buy Me a Coffee](https://buymeacoffee.com/andreadraghetti).

   [![Buy Me a Coffee](https://img.shields.io/badge/-Buy%20Me%20a%20Coffee-orange?logo=buy-me-a-coffee&logoColor=white&style=flat-square)](https://buymeacoffee.com/andreadraghetti)

## License

This project is licensed under the GNU General Public License v3.0.
