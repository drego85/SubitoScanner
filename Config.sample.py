# SMTP Settings for e-mail notification
smtp_username = ""
smtp_psw = ""
smtp_server = ""
smtp_toaddrs = ["User <example@example.com>"]

# Slack WebHook for notification
slack_webhook_url = ""

# Telegram Token and ChatID for notification
telegram_bot_token = ""
telegram_chat_id = ""

# Subito URL
subito_url = "https://www.subito.it/"
subito_api_url = "https://hades.subito.it/v1/search/items?"

# Subito queries for research
# `q`: Free-text search (e.g., q=raspberry+8gb) - Spaces should be replaced with +.
# `t`: Listing type (s=sale, g=gift, u=rental, h=vacation rental, k=wanted)
# `shp`: Filter by shipping availability (true/false)
# `sort`: Sort order (e.g., datedesc for newest first)
# `lim`: Limit of items per page (e.g., 30)
# `start`: Index for pagination (e.g., 0 for the first page)
queries = ["q=raspberry+8gb&t=s&shp=true&sort=datedesc&lim=30&start=0"]