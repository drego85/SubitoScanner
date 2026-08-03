"""telegram reply/inline keyboards and small markup helpers."""
# short descriptions show in telegram's "/" menu
_COMMANDS = [
    {"command": "start",     "description": "Home — overview & shortcuts"},
    {"command": "help",      "description": "Guide & examples"},
    {"command": "list",      "description": "Your searches"},
    {"command": "status",    "description": "Scanner status"},
    {"command": "scan",      "description": "Scan Subito now"},
    {"command": "add",       "description": "New search (guided or one-liner)"},
    {"command": "exact",     "description": "Title-only search"},
    {"command": "edit",      "description": "Edit a search — /edit 1 in toscana"},
    {"command": "cancel",    "description": "Cancel guided setup"},
    {"command": "regions",   "description": "Italian regions"},
    {"command": "stop",      "description": "Pause one search"},
    {"command": "stopall",   "description": "Pause all searches"},
    {"command": "resume",    "description": "Resume one search"},
    {"command": "resumeall", "description": "Resume all searches"},
    {"command": "remove",    "description": "Delete one search"},
    {"command": "wipe",      "description": "Delete all searches"},
    {"command": "flush",     "description": "Clear seen listings (rescan as new)"},
    {"command": "pause",     "description": "Mute alerts"},
    {"command": "unpause",   "description": "Unmute alerts"},
]

_BOT_SHORT = "Get Telegram alerts when new Subito.it listings match your searches."
_BOT_DESCRIPTION = (
    "Subito Scanner watches Subito.it and pings you on new matches.\n\n"
    "Start with ➕ New (guided), or /add sh 125 in toscana min 500.\n"
    "Manage everything from Searches. Tap /help anytime."
)

# primary bottom keyboard — keep rare/destructive actions in context menus
_BTN_ADD = "➕ New"
_BTN_LIST = "📋 Searches"
_BTN_SCAN = "🔎 Scan"
_BTN_STATUS = "📡 Status"
_BTN_HELP = "📖 Help"
_BTN_FLUSH = "🧹 Clear seen"

_MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": _BTN_ADD}, {"text": _BTN_LIST}, {"text": _BTN_SCAN}],
        [{"text": _BTN_STATUS}, {"text": _BTN_FLUSH}, {"text": _BTN_HELP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

# map reply-keyboard labels → command (include legacy labels after keyboard refresh)
_BUTTON_COMMANDS = {
    _BTN_ADD: "new",
    "➕ Add": "new",
    _BTN_LIST: "list",
    "Searches": "list",
    "📋 List": "list",
    _BTN_SCAN: "scan",
    "Scan": "scan",
    "🔎 Scan": "scan",
    _BTN_STATUS: "status",
    "Status": "status",
    "📡 Status": "status",
    _BTN_HELP: "help",
    "Help": "help",
    "📖 Help": "help",
    _BTN_FLUSH: "flush",
    "Clear seen": "flush",
    "🧹 Flush": "flush",
    # legacy bulk buttons still work if the old keyboard is cached
    "▶️ Resume all": "resumeall",
    "⏹ Stop all": "stopall",
    "🗑 Wipe all": "wipe",
}

_WIZ_CANCEL_KB = {
    "inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "wiz:cancel"}]],
}

# price presets shown in the guided wizard (min, max) — none means unbound
_PRICE_PRESETS = [
    ("Under 100€", 0, 100),
    ("100–500€", 100, 500),
    ("500–1.5k€", 500, 1500),
    ("1.5–3k€", 1500, 3000),
]


def _step_bar(current: int, total: int = 4) -> str:
    """compact progress indicator, e.g. ●●○○  2/4."""
    dots = "".join("●" if i <= current else "○" for i in range(1, total + 1))
    return f"{dots}  <b>{current}/{total}</b>"


def _inline(*rows) -> dict:
    return {"inline_keyboard": [list(r) for r in rows if r]}


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}
