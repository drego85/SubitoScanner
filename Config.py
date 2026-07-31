import os
from dotenv import load_dotenv

load_dotenv()

# ── e-mail notifications (optional) ──────────────────────────────────────────
# leave all fields empty in .env to disable e-mail
smtp_username = os.getenv("SMTP_USERNAME", "")
smtp_psw = os.getenv("SMTP_PASSWORD", "")
smtp_server = os.getenv("SMTP_SERVER", "")
_smtp_toaddrs = os.getenv("SMTP_TOADDRS", "")
smtp_toaddrs = [a.strip() for a in _smtp_toaddrs.split(",") if a.strip()]

# ── slack notifications (optional) ───────────────────────────────────────────
# leave empty in .env to disable slack
slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")

# ── telegram notifications (recommended) ─────────────────────────────────────
# set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (see .env.example)
telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

# ── subito api (do not change) ────────────────────────────────────────────────
subito_url = "https://www.subito.it/"
subito_api_url = "https://hades.subito.it/v1/search/items?"

# ── initial search queries ────────────────────────────────────────────────────
# these are loaded once on the first run and saved to scanner_state.json
# after that, use /add and /remove in telegram to manage queries without editing this file
#
# parameters:
#   q    search term, spaces replaced with +
#   t    listing type: s=sale, g=gift, u=rental, h=vacation rental, k=wanted
#   shp  shipping available: true/false
#   qso  title-only / exact keywords: true/false (subito "cerca solo nel titolo")
#   r    region id (optional) — e.g. 9=Toscana; omit for all italy (/regions in telegram)
#   ps   min price in euros (optional)
#   pe   max price in euros (optional)
#   sort datedesc = newest first
#   lim  results per page
#   start pagination offset (use 0)
queries = [
    "q=raspberry+pi+4&t=s&shp=true&qso=true&sort=datedesc&lim=10&start=0",
    "q=iphone+15&t=s&shp=true&qso=false&r=9&sort=datedesc&lim=10&start=0",  # toscana
]
