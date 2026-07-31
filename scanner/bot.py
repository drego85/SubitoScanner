import html
import logging
import time
from typing import Optional

from .notifiers import TelegramNotifier
from .state import State
from .core import SubitoScanner
from .utils import (
    REGION_IDS,
    apply_query_patch,
    build_query,
    format_price_range,
    is_exact_query,
    parse_edit_args,
    parse_search_args,
    query_label,
    query_price_range,
    query_region_name,
    query_title,
    resolve_region,
)

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
_BTN_LIST = "Searches"
_BTN_SCAN = "Scan"
_BTN_STATUS = "Status"
_BTN_HELP = "Help"

_MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": _BTN_ADD}, {"text": _BTN_LIST}, {"text": _BTN_SCAN}],
        [{"text": _BTN_STATUS}, {"text": _BTN_HELP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

# map reply-keyboard labels → command (include legacy labels after keyboard refresh)
_BUTTON_COMMANDS = {
    _BTN_ADD: "new",
    "➕ Add": "new",
    _BTN_LIST: "list",
    "📋 List": "list",
    _BTN_SCAN: "scan",
    "🔎 Scan": "scan",
    _BTN_STATUS: "status",
    "📡 Status": "status",
    _BTN_HELP: "help",
    "📖 Help": "help",
    # legacy bulk buttons still work if the old keyboard is cached
    "▶️ Resume all": "resumeall",
    "⏹ Stop all": "stopall",
    "🗑 Wipe all": "wipe",
}

_WIZ_CANCEL_KB = {
    "inline_keyboard": [[{"text": "Cancel", "callback_data": "wiz:cancel"}]],
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


class TelegramBot:
    def __init__(self, notifier: TelegramNotifier, state: State, notifiers: list = None):
        self.notifier = notifier
        self.state = state
        # used by /scan to send listing alerts (defaults to telegram only)
        self.notifiers = notifiers if notifiers is not None else ([notifier] if notifier else [])
        self._scanning = False
        self._wizard = None  # guided /add conversation state
        self._pending_term = None  # plain-text term waiting for "continue setup"

    def poll(self):
        """fetch pending updates once, dispatch commands, and persist state."""
        self._register_bot_meta()
        updates = self.notifier.get_updates(self.state.last_update_id + 1)
        for update in updates:
            # persist offset before handling so a long command cannot replay the update
            self.state.last_update_id = update["update_id"]
            self.state.save()
            self._process_update(update)
            self.state.save()  # persist command side-effects (add/stop/…)

    def run_forever(self):
        """persistent long-polling loop for real-time command responses."""
        logging.info("bot service started")
        self._register_bot_meta()
        while True:
            try:
                # reload state from disk so scanner cron changes are always reflected
                self.state = self.state.load(seed_queries=self.state.queries)
                updates = self.notifier.get_updates(self.state.last_update_id + 1, timeout=30)
                for update in updates:
                    # persist offset before handling so /scan (or a crash) cannot replay
                    self.state.last_update_id = update["update_id"]
                    self.state.save()
                    self._process_update(update)
                    self.state.save()  # persist command side-effects (add/stop/…)
            except Exception as e:
                logging.error(f"bot service error: {e}")
                time.sleep(5)

    def _register_bot_meta(self):
        self.notifier.register_commands(_COMMANDS)
        self.notifier.set_descriptions(_BOT_SHORT, _BOT_DESCRIPTION)

    # ── routing ───────────────────────────────────────────────────────────────

    def _process_update(self, update: dict):
        if "callback_query" in update:
            self._process_callback(update["callback_query"])
            return

        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")
        if not text or not chat_id or str(chat_id) != str(self.notifier.chat_id):
            return

        text = text.strip()

        # bottom keyboard buttons send plain labels — map them to commands
        button_cmd = _BUTTON_COMMANDS.get(text)
        if button_cmd:
            if button_cmd != "new" and self._wizard:
                self._wizard_clear()
            self._dispatch(chat_id, button_cmd, [])
            return

        # guided add wizard intercepts plain text (and /cancel)
        if self._wizard:
            low = text.lower()
            if low in ("/cancel", "cancel", "❌ cancel"):
                self._wizard_cancel(chat_id)
                return
            if text.startswith("/"):
                self._wizard_clear()
                # fall through to normal command handling
            else:
                self._wizard_handle_text(chat_id, text)
                return

        if not text.startswith("/"):
            self._cmd_plain_text(chat_id, text)
            return

        parts = text.split()
        command = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1:]
        self._dispatch(chat_id, command, args)

    def _dispatch(self, chat_id, command: str, args: list):
        handlers = {
            "start":     lambda: self._cmd_start(chat_id),
            "help":      lambda: self._cmd_help(chat_id),
            "list":      lambda: self._cmd_list(chat_id),
            "status":    lambda: self._cmd_status(chat_id),
            "scan":      lambda: self._cmd_scan(chat_id),
            "new":       lambda: self._wizard_start(chat_id),
            "add":       lambda: self._cmd_add(chat_id, args, exact=False),
            "exact":     lambda: self._cmd_add(chat_id, args, exact=True),
            "edit":      lambda: self._cmd_edit(chat_id, args),
            "cancel":    lambda: self._wizard_cancel(chat_id),
            "regions":   lambda: self._cmd_regions(chat_id),
            "remove":    lambda: self._cmd_remove(chat_id, args),
            "wipe":      lambda: self._cmd_wipe(chat_id, args),
            "stop":      lambda: self._cmd_stop(chat_id, args),
            "stopall":   lambda: self._cmd_stop_all(chat_id),
            "resume":    lambda: self._cmd_resume(chat_id, args),
            "resumeall": lambda: self._cmd_resume_all(chat_id),
            "startall":  lambda: self._cmd_resume_all(chat_id),
            "pause":     lambda: self._cmd_pause(chat_id),
            "unpause":   lambda: self._cmd_unpause(chat_id),
        }
        handler = handlers.get(command)
        if handler:
            handler()
        else:
            self.notifier.reply(chat_id, (
                "I don't know that command.\n\n"
                "Try /help, tap <b>➕ New</b>, or type a search term "
                "(e.g. <code>macbook pro</code>)."
            ), reply_markup=_MAIN_KEYBOARD)

    def _after_action_kb(self) -> dict:
        """inline shortcuts shown after successful mutations."""
        return _inline(
            [_btn("Searches", "list"), _btn("Scan now", "scan"), _btn("➕ New", "new")],
        )

    def _status_actions_kb(self) -> dict:
        mute = _btn("Unmute alerts", "unpause") if self.state.paused else _btn("Mute alerts", "pause")
        return _inline(
            [_btn("Scan now", "scan"), _btn("Searches", "list"), _btn("➕ New", "new")],
            [mute, _btn("Pause all", "stopall"), _btn("Resume all", "resumeall")],
            [_btn("Delete all…", "wipe")],
        )

    def _empty_searches_text(self) -> str:
        return (
            "<b>No searches yet</b>\n\n"
            "Create one in under a minute — I'll ask for the term, "
            "region, price, and match mode."
        )

    def _empty_searches_kb(self) -> dict:
        return _inline([_btn("➕ New search", "new")])

    def _format_query_meta(self, q: str) -> str:
        bits = []
        region = query_region_name(q) or "All Italy"
        bits.append(region)
        price = format_price_range(*query_price_range(q))
        if price:
            bits.append(price)
        if is_exact_query(q):
            bits.append("exact")
        return " · ".join(bits)

    def _refresh_list_message(self, chat_id, message_id):
        text, markup = self._build_list_message()
        if text is None:
            self.notifier.edit_message(
                chat_id,
                message_id,
                self._empty_searches_text(),
                reply_markup=self._empty_searches_kb(),
            )
        else:
            self.notifier.edit_message(chat_id, message_id, text, reply_markup=markup)

    def _process_callback(self, callback: dict):
        cb_id = callback.get("id")
        data = (callback.get("data") or "").strip()
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        if not chat_id or str(chat_id) != str(self.notifier.chat_id):
            if cb_id:
                self.notifier.answer_callback(cb_id, "unauthorized", show_alert=True)
            return

        if data.startswith("wiz:"):
            self._wizard_handle_callback(chat_id, cb_id, data, message_id)
            return

        if data.startswith("help:"):
            self._help_handle_callback(chat_id, cb_id, data, message_id)
            return

        if data == "new":
            self.notifier.answer_callback(cb_id)
            term = self._pending_term
            self._pending_term = None
            self._wizard_start(chat_id, term=term)
            return

        if data == "new_blank":
            self.notifier.answer_callback(cb_id)
            self._pending_term = None
            self._wizard_start(chat_id)
            return

        if data == "list":
            self.notifier.answer_callback(cb_id)
            self._cmd_list(chat_id)
            return

        if data == "scan":
            self.notifier.answer_callback(cb_id, "Scanning…")
            self._cmd_scan(chat_id)
            return

        if data == "pause":
            self.notifier.answer_callback(cb_id)
            self._cmd_pause(chat_id)
            return

        if data == "unpause":
            self.notifier.answer_callback(cb_id)
            self._cmd_unpause(chat_id)
            return

        if data == "wipe":
            self.notifier.answer_callback(cb_id)
            self._cmd_wipe(chat_id, [])
            return

        if data == "wipe:ok":
            self.notifier.answer_callback(cb_id, "Deleted")
            self._cmd_wipe(chat_id, ["confirm"])
            return

        if data == "wipe:no":
            self.notifier.answer_callback(cb_id, "Kept")
            if message_id:
                self.notifier.edit_message(chat_id, message_id, "Kept your searches.")
            return

        if data == "dismiss":
            self._pending_term = None
            self.notifier.answer_callback(cb_id)
            if message_id:
                self.notifier.edit_message(chat_id, message_id, "Okay.")
            return

        if data == "quick_italy" and self._pending_term:
            term = self._pending_term
            self._pending_term = None
            self.notifier.answer_callback(cb_id, "Added")
            self._cmd_add(chat_id, term.split(), exact=False)
            return

        toast = None
        refresh_list = False
        msg_text = (message.get("text") or "").lstrip()
        from_list = (
            "Searches" in msg_text[:80]
            or msg_text.startswith("Delete search")
            or msg_text.startswith("<b>Delete search")
        )

        if data == "stopall":
            n = self.state.stop_all()
            toast = "Already paused" if n == 0 else f"Paused {n}"
            if from_list:
                refresh_list = True
            else:
                self.notifier.answer_callback(cb_id, toast)
                self._cmd_stop_all(chat_id)
                return
        elif data == "resumeall":
            n = self.state.resume_all()
            toast = "Already active" if n == 0 else f"Resumed {n}"
            if from_list:
                refresh_list = True
            else:
                self.notifier.answer_callback(cb_id, toast)
                self._cmd_resume_all(chat_id)
                return
        elif ":" in data:
            action, _, idx_s = data.partition(":")
            if action == "rmok" and idx_s.isdigit():
                idx = int(idx_s)
                if 0 <= idx < len(self.state.queries):
                    self.state.remove_query(self.state.queries[idx])
                    toast = "Deleted"
                else:
                    toast = "Already gone"
                refresh_list = True
            elif action == "rmno":
                toast = "Kept"
                refresh_list = True
            elif not idx_s.isdigit():
                self.notifier.answer_callback(cb_id, "Invalid button", show_alert=True)
                return
            else:
                idx = int(idx_s)
                if idx < 0 or idx >= len(self.state.queries):
                    self.notifier.answer_callback(cb_id, "Gone — open Searches again", show_alert=True)
                    refresh_list = True
                else:
                    q = self.state.queries[idx]
                    n = idx + 1
                    if action == "stop":
                        if self.state.is_query_disabled(q):
                            toast = f"#{n} already paused"
                        else:
                            self.state.disable_query(q)
                            toast = f"Paused #{n}"
                        refresh_list = True
                    elif action == "resume":
                        if not self.state.is_query_disabled(q):
                            toast = f"#{n} already active"
                        else:
                            self.state.enable_query(q)
                            toast = f"Resumed #{n}"
                        refresh_list = True
                    elif action == "remove":
                        # ask for confirmation before deleting
                        self.notifier.answer_callback(cb_id)
                        title = html.escape(query_title(q))
                        self.notifier.edit_message(
                            chat_id,
                            message_id,
                            f"<b>Delete search #{n}?</b>\n{title}\n\n"
                            "This cannot be undone.",
                            reply_markup=_inline(
                                [_btn("Delete", f"rmok:{idx}"), _btn("Keep", "rmno")],
                            ),
                        )
                        return
                    elif action == "edit":
                        self.notifier.answer_callback(cb_id)
                        self._cmd_edit(chat_id, [str(n)])
                        return
                    else:
                        self.notifier.answer_callback(cb_id, "Unknown action", show_alert=True)
                        return
        else:
            self.notifier.answer_callback(cb_id, "Unknown action", show_alert=True)
            return

        self.notifier.answer_callback(cb_id, toast)
        if refresh_list and message_id:
            self._refresh_list_message(chat_id, message_id)

    # ── command handlers ──────────────────────────────────────────────────────

    def _cmd_start(self, chat_id):
        total = len(self.state.queries)
        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        paused = "\nAlerts are <b>muted</b> — unmute from Status." if self.state.paused else ""

        if total == 0:
            body = (
                "Watch <b>Subito.it</b> and get a ping when something new matches.\n\n"
                "Tap <b>➕ New</b> — I'll guide you through term, region, "
                "price, and match mode."
            )
        else:
            body = (
                f"<b>{active}</b> active · <b>{total}</b> total{paused}\n\n"
                "<b>Shortcuts</b>\n"
                "• <b>➕ New</b> — guided setup\n"
                "• <b>Searches</b> — pause, edit, delete\n"
                "• <b>Scan</b> — check Subito right now\n\n"
                "Tip: type a term like <code>sh 125</code> anytime."
            )

        self.notifier.reply(
            chat_id,
            f"<b>Subito Scanner</b>\n\n{body}",
            reply_markup=_MAIN_KEYBOARD,
        )

    def _help_menu_text(self) -> str:
        return (
            "<b>Help</b>\n\n"
            "Pick a topic — or use the buttons below the chat for everyday actions."
        )

    def _help_menu_kb(self) -> dict:
        return _inline(
            [_btn("➕ Add a search", "help:add"), _btn("✏️ Edit", "help:edit")],
            [_btn("Searches & bulk", "help:bulk"), _btn("Alerts", "help:alerts")],
            [_btn("Regions", "help:regions"), _btn("Examples", "help:examples")],
        )

    def _help_topic(self, topic: str):
        pages = {
            "add": (
                "<b>Add a search</b>\n\n"
                "<b>Guided (easiest)</b>\n"
                "Tap <b>➕ New</b> or send /add — then answer each step.\n\n"
                "<b>One-liner</b>\n"
                "<code>/add sh 125 in toscana min 500 max 2000</code>\n"
                "<code>/exact wd red in toscana</code> — title-only\n\n"
                "Filters can be in any order: <code>in …</code> · <code>min</code>/<code>max</code> · <code>500-2000</code>"
            ),
            "edit": (
                "<b>Edit a search</b>\n\n"
                "Open <b>Searches</b> → tap ✏️, or:\n"
                "<code>/edit 1 in toscana</code>\n"
                "<code>/edit 1 min 500 max 2000</code>\n"
                "<code>/edit 1 exact</code> · <code>/edit 1 broad</code>\n"
                "<code>/edit 1 anywhere</code> — drop region\n"
                "<code>/edit 1 clear price</code>\n\n"
                "Only the parts you write change. History resets after an edit."
            ),
            "bulk": (
                "<b>Searches & bulk</b>\n\n"
                "• <b>Searches</b> — per-item Pause / Edit / Delete\n"
                "• Pause all / Resume all — from Searches or Status\n"
                "• Delete all — Status → Delete all… (asks confirmation)\n"
                "• /stop 1 · /resume 1 · /remove 1 — by number"
            ),
            "alerts": (
                "<b>Alerts</b>\n\n"
                "• /pause — mute notifications (scanning continues)\n"
                "• /unpause — turn alerts back on\n"
                "• <b>Scan</b> — check Subito immediately\n"
                "• Cron still runs on your schedule in the background"
            ),
            "regions": (
                "<b>Regions</b>\n\n"
                "Use <code>in toscana</code> (or any name below) with /add, /exact, /edit.\n"
                "In guided setup, tap a region button.\n\n"
                + "\n".join(
                    f"• <code>{html.escape(REGION_IDS[r].lower().replace(' ', '-').replace(chr(39), ''))}</code>"
                    f" — {html.escape(REGION_IDS[r])}"
                    for r in sorted(REGION_IDS, key=int)
                )
            ),
            "examples": (
                "<b>Examples</b>\n\n"
                "<code>/add macbook pro</code>\n"
                "<code>/add sh 125 in toscana</code>\n"
                "<code>/add sh 125 500-2000</code>\n"
                "<code>/exact wd red in lombardia min 50</code>\n"
                "<code>/edit 1 in toscana min 500</code>\n"
                "<code>/scan</code>"
            ),
        }
        return pages.get(topic, self._help_menu_text())

    def _help_handle_callback(self, chat_id, cb_id, data: str, message_id):
        topic = data.split(":", 1)[1] if ":" in data else "menu"
        self.notifier.answer_callback(cb_id)
        if topic in ("menu", "back"):
            text, kb = self._help_menu_text(), self._help_menu_kb()
        else:
            text = self._help_topic(topic)
            kb = _inline([_btn("← Help topics", "help:back")])
        if message_id:
            self.notifier.edit_message(chat_id, message_id, text, reply_markup=kb)
        else:
            self.notifier.reply(chat_id, text, reply_markup=kb)

    def _cmd_help(self, chat_id):
        self.notifier.reply(
            chat_id,
            self._help_menu_text(),
            reply_markup=self._help_menu_kb(),
        )

    def _build_list_message(self):
        """return (html_text, inline_markup) or (None, None) if empty."""
        if not self.state.queries:
            return None, None

        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        stopped = len(self.state.queries) - active
        header = f"<b>Searches</b>  ·  {active} active"
        if stopped:
            header += f" · {stopped} paused"
        if self.state.paused:
            header += "\nAlerts muted — unmute from Status"
        lines = [header, ""]

        rows = []
        for i, q in enumerate(self.state.queries):
            n = i + 1
            paused = self.state.is_query_disabled(q)
            dot = "○" if paused else "●"
            title = html.escape(query_title(q))
            meta = html.escape(self._format_query_meta(q))
            count = len(self.state.items_by_query.get(q, []))
            state = "paused" if paused else "active"
            lines.append(
                f"{dot} <b>#{n}</b>  {title}\n"
                f"    {meta}\n"
                f"    <i>{state} · {count} seen</i>"
            )
            lines.append("")
            if paused:
                rows.append([
                    _btn(f"Resume #{n}", f"resume:{i}"),
                    _btn(f"Edit #{n}", f"edit:{i}"),
                    _btn(f"Delete #{n}", f"remove:{i}"),
                ])
            else:
                rows.append([
                    _btn(f"Pause #{n}", f"stop:{i}"),
                    _btn(f"Edit #{n}", f"edit:{i}"),
                    _btn(f"Delete #{n}", f"remove:{i}"),
                ])

        rows.append([
            _btn("➕ New", "new"),
            _btn("Pause all", "stopall"),
            _btn("Resume all", "resumeall"),
        ])
        rows.append([_btn("Scan now", "scan")])

        return "\n".join(lines).rstrip(), {"inline_keyboard": rows}

    def _cmd_list(self, chat_id):
        text, markup = self._build_list_message()
        if text is None:
            self.notifier.reply(
                chat_id,
                self._empty_searches_text(),
                reply_markup=self._empty_searches_kb(),
            )
            return
        self.notifier.reply(chat_id, text, reply_markup=markup)

    def _cmd_status(self, chat_id):
        total = len(self.state.queries)
        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        stopped = total - active
        tracked = self.state.total_tracked()

        if self.state.paused:
            run_line = "Alerts <b>muted</b> — scanning continues, no messages"
        else:
            run_line = "Alerts <b>on</b> — new matches notify you"

        lines = [
            "<b>Status</b>",
            "",
            run_line,
            "",
            f"Searches  <b>{active}</b> active"
            + (f" · <b>{stopped}</b> paused" if stopped else "")
            + f" · {total} total",
            f"Seen      <b>{tracked}</b> unique listings",
        ]
        if total == 0:
            lines.append("\nNext: create a search with ➕ New")
        elif active == 0:
            lines.append("\nAll paused — Resume all when ready")

        self.notifier.reply(
            chat_id,
            "\n".join(lines),
            reply_markup=self._status_actions_kb(),
        )

    def _cmd_scan(self, chat_id):
        if self._scanning:
            self.notifier.reply(chat_id, "A scan is already running…")
            return

        active = [q for q in self.state.queries if not self.state.is_query_disabled(q)]
        if not active:
            self.notifier.reply(
                chat_id,
                "<b>Nothing to scan</b>\n\nAdd a search or resume a paused one.",
                reply_markup=_inline(
                    [_btn("➕ New", "new"), _btn("Searches", "list")],
                ),
            )
            return

        paused_note = "\nAlerts are muted — finds are saved silently." if self.state.paused else ""
        self.notifier.reply(chat_id, (
            f"Scanning <b>{len(active)}</b> search"
            f"{'es' if len(active) != 1 else ''}…{paused_note}"
        ))

        self._scanning = True
        try:
            saved_update_id = self.state.last_update_id
            self.state = State.load(seed_queries=self.state.queries)
            self.state.last_update_id = max(self.state.last_update_id, saved_update_id)
            result = SubitoScanner(self.state, self.notifiers).run()
            self.state.last_update_id = max(self.state.last_update_id, saved_update_id)
            self.state.save()
        except Exception as e:
            logging.error(f"manual scan failed: {e}")
            self.notifier.reply(chat_id, f"Scan failed: {html.escape(str(e))}")
            return
        finally:
            self._scanning = False

        new = result["new"]
        if new == 0:
            self.notifier.reply(chat_id, (
                f"<b>Scan complete</b>\n"
                f"No new listings · checked {result['queries']} active"
            ), reply_markup=self._after_action_kb())
        else:
            self.notifier.reply(chat_id, (
                f"<b>Scan complete</b>\n"
                f"<b>{new}</b> new listing{'s' if new != 1 else ''} "
                f"({'muted' if self.state.paused else 'sent above'})"
            ), reply_markup=self._after_action_kb())

    def _cmd_plain_text(self, chat_id, text: str):
        """turn free-form messages into a guided or one-tap add."""
        term = " ".join(text.split())
        if len(term) > 80:
            self.notifier.reply(chat_id, (
                "That looks long for a search term.\n"
                "Try something shorter, or tap ➕ New."
            ), reply_markup=_MAIN_KEYBOARD)
            return

        self._pending_term = term
        safe = html.escape(term)
        self.notifier.reply(chat_id, (
            f"Track <b>{safe}</b>?\n\n"
            "Continue with guided filters, or add it for all Italy right away."
        ), reply_markup=_inline(
            [_btn("Continue setup", "new"), _btn("Add · All Italy", "quick_italy")],
            [_btn("Not now", "dismiss")],
        ))

    def _cmd_regions(self, chat_id):
        lines = [
            "🗺 <b>regions</b> (append <code>in &lt;name&gt;</code> to /add or /exact)\n",
            "<i>example:</i> <code>/add wd red in toscana</code>\n",
        ]
        for rid in sorted(REGION_IDS, key=int):
            name = REGION_IDS[rid]
            slug = name.lower().replace(" ", "-").replace("'", "")
            lines.append(f"• <code>{html.escape(slug)}</code> — {html.escape(name)}")
        lines.append("\nalso accepted: <code>tuscany</code>, <code>lombardy</code>, <code>sicily</code>, …")
        self.notifier.reply(chat_id, "\n".join(lines))

    # ── guided add wizard ─────────────────────────────────────────────────────

    def _wizard_clear(self):
        self._wizard = None

    def _wizard_cancel(self, chat_id):
        was = self._wizard is not None
        self._wizard_clear()
        if was:
            self.notifier.reply(
                chat_id,
                "Cancelled. Tap <b>➕ New</b> whenever you're ready.",
                reply_markup=_MAIN_KEYBOARD,
            )
        else:
            self.notifier.reply(chat_id, "Nothing to cancel.", reply_markup=_MAIN_KEYBOARD)

    def _wizard_header(self, step: int, title: str) -> str:
        return f"<b>New search</b>  ·  {_step_bar(step)}\n<b>{title}</b>"

    def _wizard_draft(self) -> str:
        w = self._wizard or {}
        if not w.get("term"):
            return ""
        where = REGION_IDS.get(w["region"], "All Italy") if w.get("region") else "All Italy"
        price = format_price_range(w.get("min_price"), w.get("max_price")) or "any price"
        mode = "exact" if w.get("exact") else "broad"
        # show mode only once chosen (confirm / after exact step)
        bits = [html.escape(where), html.escape(price)]
        if w.get("step") in ("confirm",) or w.get("exact_set"):
            bits.append(mode)
        return (
            f"\n\n🔍 <b>{html.escape(w['term'])}</b>\n"
            f"<i>{' · '.join(bits)}</i>"
        )

    def _wizard_nav(self, *extra_rows, back: bool = True, cancel: bool = True) -> dict:
        rows = [list(r) for r in extra_rows if r]
        nav = []
        if back:
            nav.append(_btn("← Back", "wiz:back"))
        if cancel:
            nav.append(_btn("Cancel", "wiz:cancel"))
        if nav:
            rows.append(nav)
        return {"inline_keyboard": rows}

    def _wizard_start(self, chat_id, term: str = None):
        self._pending_term = None
        self._wizard = {
            "step": "term",
            "term": None,
            "region": None,
            "min_price": None,
            "max_price": None,
            "exact": False,
            "exact_set": False,
            "history": [],
        }
        if term and len(term.strip()) >= 2:
            self._wizard["term"] = term.strip()
            self._wizard_ask_region(chat_id)
            return

        self.notifier.reply(chat_id, (
            f"{self._wizard_header(1, 'What are you looking for?')}\n\n"
            "Type a search term.\n"
            "<i>Examples:</i> <code>sh 125</code> · <code>macbook pro</code> · <code>wd red</code>"
        ), reply_markup=self._wizard_nav(back=False))

    def _wizard_region_keyboard(self) -> dict:
        rows = []
        row = []
        short_names = {
            "1": "Aosta", "5": "Trentino", "7": "Friuli", "8": "Emilia",
        }
        for rid in sorted(REGION_IDS, key=int):
            label = short_names.get(rid, REGION_IDS[rid])
            row.append(_btn(label, f"wiz:region:{rid}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([_btn("All Italy", "wiz:region:skip")])
        return self._wizard_nav(*rows)

    def _wizard_ask_region(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "region"
        text = (
            f"{self._wizard_header(2, 'Where?')}"
            f"{self._wizard_draft()}\n\n"
            "Pick a region, or type a name (e.g. <code>toscana</code>)."
        )
        markup = self._wizard_region_keyboard()
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_price(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "price"
        text = (
            f"{self._wizard_header(3, 'Price filter?')}"
            f"{self._wizard_draft()}\n\n"
            "Pick a range, type e.g. <code>500-2000</code>, or skip."
        )
        preset_rows = []
        row = []
        for i, (label, _, _) in enumerate(_PRICE_PRESETS):
            row.append(_btn(label, f"wiz:preset:{i}"))
            if len(row) == 2:
                preset_rows.append(row)
                row = []
        if row:
            preset_rows.append(row)
        preset_rows.append([
            _btn("Custom…", "wiz:price:set"),
            _btn("Any price", "wiz:price:skip"),
        ])
        markup = self._wizard_nav(*preset_rows)
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_price_input(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "price_input"
        text = (
            f"{self._wizard_header(3, 'Enter a price')}"
            f"{self._wizard_draft()}\n\n"
            "Send one of:\n"
            "• <code>500-2000</code>\n"
            "• <code>min 500 max 2000</code>\n"
            "• <code>min 500</code> or <code>max 2000</code>"
        )
        markup = self._wizard_nav([_btn("Any price", "wiz:price:skip")])
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_exact(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "exact"
        text = (
            f"{self._wizard_header(4, 'Match mode')}"
            f"{self._wizard_draft()}\n\n"
            "<b>Broad</b> — more results\n"
            "<b>Exact</b> — title-only (Subito “cerca solo nel titolo”)"
        )
        markup = self._wizard_nav([
            _btn("Broad", "wiz:exact:0"),
            _btn("Exact", "wiz:exact:1"),
        ])
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_confirm(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "confirm"
        self._wizard["exact_set"] = True
        text = (
            f"<b>New search</b>  ·  ready"
            f"{self._wizard_draft()}\n\n"
            "Save this search?"
        )
        markup = self._wizard_nav([
            _btn("Save", "wiz:confirm"),
            _btn("Start over", "wiz:restart"),
        ])
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_commit(self, chat_id):
        w = self._wizard
        if not w or not w.get("term"):
            self._wizard_clear()
            self.notifier.reply(
                chat_id,
                "Session expired — tap <b>➕ New</b> to start again.",
                reply_markup=_MAIN_KEYBOARD,
            )
            return

        query = build_query(
            w["term"],
            exact=bool(w.get("exact")),
            region=w.get("region"),
            min_price=w.get("min_price"),
            max_price=w.get("max_price"),
        )
        safe = html.escape(w["term"])
        meta = html.escape(self._format_query_meta(query))
        self._wizard_clear()

        if query in self.state.queries:
            idx = self.state.queries.index(query) + 1
            status = "paused" if self.state.is_query_disabled(query) else "active"
            tip = f" Resume it from Searches." if status == "paused" else ""
            self.notifier.reply(chat_id, (
                f"Already tracking this as <b>#{idx}</b> ({status}).{tip}"
            ), reply_markup=self._after_action_kb())
            return

        self.state.add_query(query)
        n = len(self.state.queries)
        self.notifier.reply(chat_id, (
            f"<b>Saved · #{n}</b>\n"
            f"🔍 <b>{safe}</b>\n"
            f"{meta}\n\n"
            "You'll be notified when new listings appear."
        ), reply_markup=self._after_action_kb())

    def _wizard_go_back(self, chat_id, message_id):
        step = self._wizard.get("step")
        if step in ("region",):
            # back to term
            self._wizard["term"] = None
            self._wizard["step"] = "term"
            self.notifier.edit_message(chat_id, message_id, (
                f"{self._wizard_header(1, 'What are you looking for?')}\n\n"
                "Type a search term."
            ), reply_markup=self._wizard_nav(back=False))
        elif step in ("price", "price_input"):
            self._wizard_ask_region(chat_id, edit_message_id=message_id)
        elif step == "exact":
            self._wizard_ask_price(chat_id, edit_message_id=message_id)
        elif step == "confirm":
            self._wizard["exact_set"] = False
            self._wizard_ask_exact(chat_id, edit_message_id=message_id)
        else:
            self._wizard_cancel(chat_id)

    def _wizard_handle_text(self, chat_id, text: str):
        if not self._wizard:
            return
        step = self._wizard.get("step")

        if step == "term":
            term = text.strip()
            if len(term) < 2:
                self.notifier.reply(chat_id, (
                    "Please send a longer term (2+ characters).\n"
                    "<i>Example:</i> <code>sh 125</code>"
                ), reply_markup=self._wizard_nav(back=False))
                return
            self._wizard["term"] = term
            self._wizard_ask_region(chat_id)
            return

        if step == "region":
            resolved = resolve_region(text)
            if not resolved:
                self.notifier.reply(chat_id, (
                    f"Unknown region <b>{html.escape(text)}</b>.\n"
                    "Tap a button, or try another name."
                ), reply_markup=self._wizard_region_keyboard())
                return
            self._wizard["region"] = resolved[0]
            self._wizard_ask_price(chat_id)
            return

        if step in ("price_input", "price"):
            _, region_id, min_price, max_price, err = parse_search_args(text.split())
            if err:
                self.notifier.reply(chat_id, (
                    f"{html.escape(err)}.\n"
                    "Try <code>500-2000</code> or <code>min 500 max 2000</code>."
                ), reply_markup=self._wizard_nav([_btn("Any price", "wiz:price:skip")]))
                return
            if min_price is None and max_price is None and region_id is None:
                if step == "price":
                    self.notifier.reply(chat_id, (
                        "Pick a preset, tap <b>Any price</b>, or send <code>500-2000</code>."
                    ))
                    return
                self.notifier.reply(chat_id, (
                    "Send a price like <code>500-2000</code>, or tap Any price."
                ), reply_markup=self._wizard_nav([_btn("Any price", "wiz:price:skip")]))
                return
            if region_id is not None:
                self._wizard["region"] = region_id
            self._wizard["min_price"] = min_price
            self._wizard["max_price"] = max_price
            self._wizard_ask_exact(chat_id)
            return

        if step in ("exact", "confirm"):
            self.notifier.reply(chat_id, "Use the buttons below, or /cancel.")
            return

        self.notifier.reply(chat_id, "Use the buttons, or /cancel.")

    def _wizard_handle_callback(self, chat_id, cb_id, data: str, message_id):
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "cancel":
            self.notifier.answer_callback(cb_id, "Cancelled")
            self._wizard_cancel(chat_id)
            return

        if action == "restart":
            self.notifier.answer_callback(cb_id)
            self._wizard_start(chat_id)
            return

        if action == "back":
            if not self._wizard:
                self.notifier.answer_callback(cb_id, "Expired", show_alert=True)
                return
            self.notifier.answer_callback(cb_id)
            self._wizard_go_back(chat_id, message_id)
            return

        if not self._wizard:
            self.notifier.answer_callback(cb_id, "Expired — tap ➕ New", show_alert=True)
            return

        if action == "region":
            val = parts[2] if len(parts) > 2 else "skip"
            if val == "skip":
                self._wizard["region"] = None
                self.notifier.answer_callback(cb_id, "All Italy")
            elif val in REGION_IDS:
                self._wizard["region"] = val
                self.notifier.answer_callback(cb_id, REGION_IDS[val])
            else:
                self.notifier.answer_callback(cb_id, "Unknown region", show_alert=True)
                return
            self._wizard_ask_price(chat_id, edit_message_id=message_id)
            return

        if action == "preset":
            idx_s = parts[2] if len(parts) > 2 else ""
            if not idx_s.isdigit() or int(idx_s) >= len(_PRICE_PRESETS):
                self.notifier.answer_callback(cb_id, "Unknown", show_alert=True)
                return
            _, lo, hi = _PRICE_PRESETS[int(idx_s)]
            self._wizard["min_price"] = lo if lo else None
            # under 100 uses min 0 — treat as no min, max 100
            if lo == 0:
                self._wizard["min_price"] = None
            self._wizard["max_price"] = hi
            self.notifier.answer_callback(cb_id, _PRICE_PRESETS[int(idx_s)][0])
            self._wizard_ask_exact(chat_id, edit_message_id=message_id)
            return

        if action == "price":
            val = parts[2] if len(parts) > 2 else "skip"
            if val == "skip":
                self._wizard["min_price"] = None
                self._wizard["max_price"] = None
                self.notifier.answer_callback(cb_id, "Any price")
                self._wizard_ask_exact(chat_id, edit_message_id=message_id)
            elif val == "set":
                self.notifier.answer_callback(cb_id)
                self._wizard_ask_price_input(chat_id, edit_message_id=message_id)
            else:
                self.notifier.answer_callback(cb_id, "Unknown", show_alert=True)
            return

        if action == "exact":
            val = parts[2] if len(parts) > 2 else "0"
            self._wizard["exact"] = val in ("1", "true", "yes")
            self.notifier.answer_callback(cb_id, "Exact" if self._wizard["exact"] else "Broad")
            self._wizard_ask_confirm(chat_id, edit_message_id=message_id)
            return

        if action == "confirm":
            self.notifier.answer_callback(cb_id, "Saved")
            self._wizard_commit(chat_id)
            return

        self.notifier.answer_callback(cb_id, "Unknown action", show_alert=True)

    def _cmd_add(self, chat_id, args: list, exact: bool):
        # no args → guided wizard (same as ➕ Add)
        if not args:
            self._wizard_start(chat_id)
            return

        cmd = "exact" if exact else "add"

        # allow "/add exact term" as well as dedicated /exact
        if not exact and args[0].lower() in ("exact", "--exact"):
            exact = True
            cmd = "exact"
            args = args[1:]
            if not args:
                self.notifier.reply(chat_id, (
                    "usage: <code>/add exact &lt;term&gt; [filters]</code>\n"
                    "or simply: <code>/exact &lt;term&gt; in toscana min 100</code>"
                ))
                return

        term_args, region_id, min_price, max_price, err = parse_search_args(args)
        if err:
            hint = "\nsend /regions for valid names." if "region" in err else ""
            self.notifier.reply(chat_id, f"❓ {html.escape(err)}.{hint}")
            return

        term = " ".join(term_args).strip()
        if not term:
            self.notifier.reply(chat_id, (
                f"please include a search term.\n"
                f"<code>/{cmd} sh 125 in toscana min 500 max 2000</code>"
            ))
            return

        query = build_query(
            term,
            exact=exact,
            region=region_id,
            min_price=min_price,
            max_price=max_price,
        )
        safe = html.escape(term)
        mode = "exact / title-only" if exact else "broad"
        where = query_region_name(query) or "all Italy"
        price = format_price_range(min_price, max_price) or "any"
        filters = f"{html.escape(where)} · {html.escape(price)}"

        if query in self.state.queries:
            idx = self.state.queries.index(query) + 1
            status = "stopped" if self.state.is_query_disabled(query) else "active"
            tip = f" use /resume {idx} to turn it back on." if status == "stopped" else ""
            self.notifier.reply(chat_id, (
                f"ℹ️ <b>{safe}</b> ({mode}, {filters}) is already #{idx} ({status}).{tip}\n"
                "/list to see all."
            ))
            return

        self.state.add_query(query)
        n = len(self.state.queries)
        self.notifier.reply(chat_id, (
            f"<b>Saved · #{n}</b>\n"
            f"🔍 <b>{safe}</b>\n"
            f"{html.escape(self._format_query_meta(query))}\n\n"
            "You'll be notified when new listings appear."
        ), reply_markup=self._after_action_kb())

    def _edit_usage(self, n: int, query: str) -> str:
        """show current filters + copy-ready /edit examples for one search."""
        title = html.escape(query_title(query))
        meta = html.escape(self._format_query_meta(query))
        return (
            f"<b>Edit #{n}</b>\n"
            f"🔍 {title}\n"
            f"<i>{meta}</i>\n\n"
            "Send a change (only what you write is updated):\n"
            f"• <code>/edit {n} in toscana</code>\n"
            f"• <code>/edit {n} min 500</code> · <code>/edit {n} max 2000</code>\n"
            f"• <code>/edit {n} 500-2000</code>\n"
            f"• <code>/edit {n} exact</code> · <code>/edit {n} broad</code>\n"
            f"• <code>/edit {n} anywhere</code> · <code>/edit {n} clear price</code>\n"
            f"• <code>/edit {n} sh 150</code> — rename\n\n"
            "<i>History resets after an edit.</i>"
        )

    def _cmd_edit(self, chat_id, args: list):
        if not args:
            preview = self._short_list_preview() if self.state.queries else ""
            self.notifier.reply(chat_id, (
                "usage: <code>/edit &lt;n&gt; [filters…]</code>\n\n"
                "<i>examples:</i>\n"
                "• <code>/edit 1 in toscana</code>\n"
                "• <code>/edit 1 min 500 max 2000</code>\n"
                "• <code>/edit 1 exact</code>\n"
                "• <code>/edit 1</code> — show ready-to-copy options for #1\n\n"
                f"{preview}"
            ))
            return

        query = self._resolve_query(chat_id, args[:1], "edit")
        if query is None:
            return

        n = self.state.queries.index(query) + 1
        rest = args[1:]
        if not rest:
            self.notifier.reply(chat_id, self._edit_usage(n, query))
            return

        patch, err = parse_edit_args(rest)
        if err:
            hint = "\nsend /regions for valid names." if "region" in err else ""
            self.notifier.reply(chat_id, f"❓ {html.escape(err)}.{hint}")
            return
        if not patch:
            self.notifier.reply(chat_id, self._edit_usage(n, query))
            return

        try:
            new_query = apply_query_patch(query, patch)
        except ValueError as e:
            self.notifier.reply(chat_id, f"❓ {html.escape(str(e))}.")
            return

        if new_query == query:
            self.notifier.reply(chat_id, (
                f"ℹ️ <b>#{n}</b> already looks like that — nothing changed.\n"
                f"{html.escape(query_label(query))}"
            ))
            return

        if new_query in self.state.queries:
            other = self.state.queries.index(new_query) + 1
            self.notifier.reply(chat_id, (
                f"ℹ️ that would duplicate <b>#{other}</b> "
                f"({html.escape(query_label(new_query))}).\n"
                "change something else, or /remove one of them."
            ))
            return

        self.state.update_query(query, new_query)
        new_label = html.escape(query_label(new_query))
        self.notifier.reply(chat_id, (
            f"<b>Updated #{n}</b>\n"
            f"→ {new_label}\n\n"
            "History cleared — next scan treats current listings as new."
        ), reply_markup=self._after_action_kb())

    def _cmd_remove(self, chat_id, args: list):
        if args and args[0].lower() == "all":
            self._cmd_wipe(chat_id, [])
            return
        query = self._resolve_query(chat_id, args, "remove")
        if query is None:
            return
        label = html.escape(query_label(query))
        self.state.remove_query(query)
        self.notifier.reply(chat_id, (
            f"🗑 deleted <b>{label}</b>\n"
            "its history is gone. /list to see what's left."
        ))

    def _cmd_wipe(self, chat_id, args: list):
        if not self.state.queries:
            self.notifier.reply(
                chat_id,
                "<b>Nothing to delete</b>\nYou're not tracking any searches.",
                reply_markup=_MAIN_KEYBOARD,
            )
            return

        confirmed = bool(args) and args[0].lower() in ("confirm", "yes", "--confirm")
        n = len(self.state.queries)
        if not confirmed:
            self.notifier.reply(chat_id, (
                f"<b>Delete all searches?</b>\n\n"
                f"This permanently removes <b>{n}</b> search"
                f"{'es' if n != 1 else ''} and their history."
            ), reply_markup=_inline(
                [_btn("Delete everything", "wipe:ok"), _btn("Keep them", "wipe:no")],
            ))
            return

        removed = self.state.wipe_all()
        self.notifier.reply(chat_id, (
            f"Deleted <b>{removed}</b> search{'es' if removed != 1 else ''}.\n"
            "Tap <b>➕ New</b> to start fresh."
        ), reply_markup=_inline([_btn("➕ New search", "new")]))

    def _cmd_stop(self, chat_id, args: list):
        if args and args[0].lower() == "all":
            self._cmd_stop_all(chat_id)
            return
        query = self._resolve_query(chat_id, args, "stop")
        if query is None:
            return
        label = html.escape(query_label(query))
        if self.state.is_query_disabled(query):
            idx = self.state.queries.index(query) + 1
            self.notifier.reply(chat_id, (
                f"ℹ️ <b>{label}</b> is already stopped.\n"
                f"/resume {idx} to turn it back on."
            ))
            return
        self.state.disable_query(query)
        idx = self.state.queries.index(query) + 1
        self.notifier.reply(chat_id, (
            f"⏹ stopped <b>{label}</b>\n"
            "history cleared — when you /resume it, you'll get a fresh batch of current listings.\n"
            f"/resume {idx} when you're ready."
        ))

    def _cmd_stop_all(self, chat_id):
        if not self.state.queries:
            self.notifier.reply(chat_id, "📭 no searches to stop.")
            return
        stopped = self.state.stop_all()
        if stopped == 0:
            self.notifier.reply(chat_id, "ℹ️ all searches were already stopped. /resumeall to turn them back on.")
            return
        self.notifier.reply(chat_id, (
            f"⏹ stopped <b>{stopped}</b> search{'es' if stopped != 1 else ''}.\n"
            "history cleared for each — /resumeall when you want them active again."
        ))

    def _cmd_resume(self, chat_id, args: list):
        if args and args[0].lower() == "all":
            self._cmd_resume_all(chat_id)
            return
        query = self._resolve_query(chat_id, args, "resume")
        if query is None:
            return
        label = html.escape(query_label(query))
        if not self.state.is_query_disabled(query):
            self.notifier.reply(chat_id, f"ℹ️ <b>{label}</b> is already active.")
            return
        self.state.enable_query(query)
        self.notifier.reply(chat_id, (
            f"✅ resumed <b>{label}</b>\n"
            "next scan will treat current listings as new (history was cleared when stopped)."
        ))

    def _cmd_resume_all(self, chat_id):
        if not self.state.queries:
            self.notifier.reply(chat_id, "📭 no searches to resume. /add something first.")
            return
        resumed = self.state.resume_all()
        if resumed == 0:
            self.notifier.reply(chat_id, "ℹ️ all searches were already active.")
            return
        self.notifier.reply(chat_id, (
            f"✅ resumed <b>{resumed}</b> search{'es' if resumed != 1 else ''}.\n"
            "next /scan will treat current listings as new for those that were stopped."
        ))

    def _cmd_pause(self, chat_id):
        if self.state.paused:
            self.notifier.reply(chat_id, "ℹ️ alerts are already muted. /unpause to turn them back on.")
            return
        self.state.paused = True
        self.notifier.reply(chat_id, (
            "⏸ <b>all alerts muted</b>\n"
            "scanning still runs in the background, but you won't get messages.\n"
            "/unpause when you want notifications again."
        ))

    def _cmd_unpause(self, chat_id):
        if not self.state.paused:
            self.notifier.reply(chat_id, "ℹ️ alerts are already on.")
            return
        self.state.paused = False
        self.notifier.reply(chat_id, "▶️ <b>alerts unmuted</b> — you'll be notified of new matches again.")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _resolve_query(self, chat_id, args: list, command: str) -> Optional[str]:
        """resolve a 1-based user index to the actual query string."""
        if not self.state.queries:
            self.notifier.reply(chat_id, (
                "📭 no searches yet.\n"
                "add one with <code>/add macbook pro</code> or <code>/exact wd red</code>."
            ))
            return None

        if not args or not args[0].isdigit():
            preview = self._short_list_preview()
            self.notifier.reply(chat_id, (
                f"pick a number from /list:\n"
                f"<code>/{command} 1</code>\n\n"
                f"{preview}"
            ))
            return None

        idx = int(args[0]) - 1
        if idx < 0 or idx >= len(self.state.queries):
            preview = self._short_list_preview()
            self.notifier.reply(chat_id, (
                f"❌ #{args[0]} doesn't exist.\n\n{preview}"
            ))
            return None
        return self.state.queries[idx]

    def _short_list_preview(self) -> str:
        lines = ["<b>your searches:</b>"]
        for i, q in enumerate(self.state.queries):
            icon = "🔴" if self.state.is_query_disabled(q) else "🟢"
            lines.append(f"{icon} <b>#{i + 1}</b> {html.escape(query_label(q))}")
        return "\n".join(lines)
