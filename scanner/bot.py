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
    {"command": "start",     "description": "Welcome & quick start"},
    {"command": "help",      "description": "All commands with examples"},
    {"command": "list",      "description": "Your tracked searches"},
    {"command": "status",    "description": "Scanner status at a glance"},
    {"command": "scan",      "description": "Scan Subito now (don't wait for cron)"},
    {"command": "add",       "description": "Track a search — /add alone for guided setup"},
    {"command": "exact",     "description": "Title-only — /exact wd red in toscana"},
    {"command": "edit",      "description": "Change a search — /edit 1 in toscana min 500"},
    {"command": "cancel",    "description": "Cancel the guided add wizard"},
    {"command": "regions",   "description": "List Italian regions you can use"},
    {"command": "stop",      "description": "Pause one search — /stop 1"},
    {"command": "stopall",   "description": "Pause ALL searches"},
    {"command": "resume",    "description": "Resume one search — /resume 1"},
    {"command": "resumeall", "description": "Resume ALL stopped searches"},
    {"command": "remove",    "description": "Delete a search — /remove 1"},
    {"command": "wipe",      "description": "Delete ALL searches (needs /wipe confirm)"},
    {"command": "pause",     "description": "Mute all notifications"},
    {"command": "unpause",   "description": "Unmute all notifications"},
]

_BOT_SHORT = "Alerts you when new Subito.it listings match your searches."
_BOT_DESCRIPTION = (
    "Monitor Subito.it and get Telegram alerts for new listings.\n\n"
    "Quick start:\n"
    "1. Tap ➕ Add (guided) or /add macbook pro\n"
    "2. /add wd red in toscana — only Tuscany\n"
    "3. /exact wd red in toscana — title-only + region\n"
    "4. /edit 1 in toscana min 500 — tweak an existing search\n"
    "5. /scan — check Subito right now\n"
    "6. /stopall · /resumeall · /wipe confirm — bulk controls\n\n"
    "Tip: use the bottom buttons, or tap actions under /list."
)

# persistent bottom keyboard (reply keyboard)
_BTN_ADD = "➕ Add"
_BTN_LIST = "📋 List"
_BTN_SCAN = "🔎 Scan"
_BTN_STATUS = "📡 Status"
_BTN_RESUME_ALL = "▶️ Resume all"
_BTN_STOP_ALL = "⏹ Stop all"
_BTN_WIPE_ALL = "🗑 Wipe all"
_BTN_HELP = "📖 Help"

_MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": _BTN_ADD}, {"text": _BTN_LIST}, {"text": _BTN_SCAN}],
        [{"text": _BTN_STATUS}, {"text": _BTN_RESUME_ALL}, {"text": _BTN_STOP_ALL}],
        [{"text": _BTN_WIPE_ALL}, {"text": _BTN_HELP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

# map reply-keyboard labels → command name (no leading slash)
_BUTTON_COMMANDS = {
    _BTN_ADD: "new",
    _BTN_LIST: "list",
    _BTN_SCAN: "scan",
    _BTN_STATUS: "status",
    _BTN_RESUME_ALL: "resumeall",
    _BTN_STOP_ALL: "stopall",
    _BTN_WIPE_ALL: "wipe",
    _BTN_HELP: "help",
}

_WIZ_CANCEL_KB = {
    "inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "wiz:cancel"}]],
}


class TelegramBot:
    def __init__(self, notifier: TelegramNotifier, state: State, notifiers: list = None):
        self.notifier = notifier
        self.state = state
        # used by /scan to send listing alerts (defaults to telegram only)
        self.notifiers = notifiers if notifiers is not None else ([notifier] if notifier else [])
        self._scanning = False
        self._wizard = None  # guided /add conversation state

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
                "❓ i don't know that command.\n\n"
                "try /help for the full list, or just type what you want to search "
                "(e.g. <code>macbook pro</code>) and i'll show you how to track it."
            ), reply_markup=_MAIN_KEYBOARD)

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

        # guided add wizard buttons
        if data.startswith("wiz:"):
            self._wizard_handle_callback(chat_id, cb_id, data, message_id)
            return

        if data == "new":
            self.notifier.answer_callback(cb_id, "add search")
            self._wizard_start(chat_id)
            return

        toast = None
        refresh_list = False

        if data == "scan":
            self.notifier.answer_callback(cb_id, "scanning…")
            self._cmd_scan(chat_id)
            return

        if data == "stopall":
            n = self.state.stop_all()
            toast = "already all stopped" if n == 0 else f"stopped {n}"
            refresh_list = True
        elif data == "resumeall":
            n = self.state.resume_all()
            toast = "already all active" if n == 0 else f"resumed {n}"
            refresh_list = True
        elif ":" in data:
            action, _, idx_s = data.partition(":")
            if not idx_s.isdigit():
                self.notifier.answer_callback(cb_id, "invalid button", show_alert=True)
                return
            idx = int(idx_s)
            if idx < 0 or idx >= len(self.state.queries):
                self.notifier.answer_callback(cb_id, "search gone — open /list again", show_alert=True)
                refresh_list = True
            else:
                q = self.state.queries[idx]
                n = idx + 1
                if action == "stop":
                    if self.state.is_query_disabled(q):
                        toast = f"#{n} already stopped"
                    else:
                        self.state.disable_query(q)
                        toast = f"stopped #{n}"
                    refresh_list = True
                elif action == "resume":
                    if not self.state.is_query_disabled(q):
                        toast = f"#{n} already active"
                    else:
                        self.state.enable_query(q)
                        toast = f"resumed #{n}"
                    refresh_list = True
                elif action == "remove":
                    self.state.remove_query(q)
                    toast = f"removed #{n}"
                    refresh_list = True
                elif action == "edit":
                    self.notifier.answer_callback(cb_id, f"edit #{n}")
                    self._cmd_edit(chat_id, [str(n)])
                    return
                else:
                    self.notifier.answer_callback(cb_id, "unknown action", show_alert=True)
                    return
        else:
            self.notifier.answer_callback(cb_id, "unknown action", show_alert=True)
            return

        self.notifier.answer_callback(cb_id, toast)
        if refresh_list and message_id:
            text, markup = self._build_list_message()
            if text is None:
                self.notifier.edit_message(
                    chat_id,
                    message_id,
                    "📭 you're not tracking anything yet.\n\ntry <b>➕ Add</b> or <code>/add macbook pro</code>",
                    reply_markup={"inline_keyboard": [[{"text": "➕ Add search", "callback_data": "new"}]]},
                )
            else:
                self.notifier.edit_message(chat_id, message_id, text, reply_markup=markup)

    # ── command handlers ──────────────────────────────────────────────────────

    def _cmd_start(self, chat_id):
        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        paused_note = "\n⏸ notifications are currently <b>paused</b> — /unpause to resume.\n" if self.state.paused else ""
        self.notifier.reply(chat_id, (
            "👋 <b>welcome to subito scanner</b>\n\n"
            "i watch <b>subito.it</b> for you and send a message when a new listing matches.\n"
            f"{paused_note}\n"
            "<b>quick start</b>\n"
            "• use the <b>➕ Add</b> button for a guided setup\n"
            "• or /add sh 125 in toscana min 500 max 2000 — all at once\n"
            "• open /list then tap ⏹ ▶️ ✏️ 🗑 under each search\n\n"
            f"you have <b>{active}</b> active search"
            f"{'es' if active != 1 else ''}"
            f" out of <b>{len(self.state.queries)}</b> total.\n\n"
            "💡 tip: type a search term without a command and i'll offer the right /add for you.\n"
            "full guide → /help"
        ), reply_markup=_MAIN_KEYBOARD)

    def _cmd_help(self, chat_id):
        self.notifier.reply(chat_id, (
            "📖 <b>commands</b>\n\n"
            "<b>searches</b>\n"
            "/add — <b>guided setup</b> (same as ➕ Add button)\n"
            "/add &lt;term&gt; [filters] — quick add in one message\n"
            "    <i>ex:</i> <code>/add macbook pro</code>\n"
            "    <i>ex:</i> <code>/add sh 125 in toscana min 500 max 2000</code>\n"
            "/exact &lt;term&gt; [filters…] — title-only / exact keywords\n"
            "    <i>ex:</i> <code>/exact wd red in toscana</code>\n"
            "/edit &lt;n&gt; [filters…] — change an existing search\n"
            "    <i>ex:</i> <code>/edit 1 in toscana</code>\n"
            "    <i>ex:</i> <code>/edit 1 min 500 max 2000</code>\n"
            "    <i>ex:</i> <code>/edit 1 exact</code> · <code>/edit 1 anywhere</code>\n"
            "/cancel — abort the guided add wizard\n"
            "/regions — show all Italian regions you can use\n"
            "/scan — run a Subito check now (don't wait for cron)\n"
            "/list — numbered list of your searches (+ action buttons)\n"
            "/stop &lt;n&gt; — pause search #n (clears its history)\n"
            "/stopall — pause ALL searches\n"
            "/resume &lt;n&gt; — turn search #n back on\n"
            "/resumeall — resume ALL stopped searches\n"
            "/remove &lt;n&gt; — delete search #n forever\n"
            "/wipe confirm — delete ALL searches + history\n\n"
            "<b>notifications</b>\n"
            "/pause — mute all alerts (scanning continues)\n"
            "/unpause — unmute alerts\n"
            "/status — running / paused + counts\n\n"
            "<b>buttons</b>\n"
            "• bottom keyboard: Add · List · Scan · Status · Stop/Resume/Wipe · Help\n"
            "• under /list: ⏹ stop · ▶️ resume · ✏️ edit · 🗑 remove\n\n"
            "<b>tips</b>\n"
            "• ➕ Add asks step by step: term → region → price → exact/broad\n"
            "• /exact = subito <i>cerca solo nel titolo</i>\n"
            "• /edit keeps the term and only changes what you specify\n"
            "• combine filters freely: <code>in toscana min 100 max 500</code>\n"
            "• send a plain term like <code>iphone 15</code> for ready-to-tap commands"
        ), reply_markup=_MAIN_KEYBOARD)

    def _build_list_message(self):
        """return (html_text, inline_markup) or (None, None) if empty."""
        if not self.state.queries:
            return None, None

        lines = ["📋 <b>your searches</b>\n"]
        rows = []
        for i, q in enumerate(self.state.queries):
            n = i + 1
            stopped = self.state.is_query_disabled(q)
            status = "🔴 stopped" if stopped else "🟢 active"
            badges = []
            if is_exact_query(q):
                badges.append("exact")
            region = query_region_name(q)
            if region:
                badges.append(region)
            price = format_price_range(*query_price_range(q))
            if price:
                badges.append(price)
            badge_txt = f" · {' · '.join(badges)}" if badges else ""
            count = len(self.state.items_by_query.get(q, []))
            title = html.escape(query_title(q))
            lines.append(
                f"<b>#{n}</b> {status}{badge_txt}\n"
                f"   {title}\n"
                f"   📦 {count} item{'s' if count != 1 else ''} seen"
            )
            if stopped:
                rows.append([
                    {"text": f"▶️ #{n}", "callback_data": f"resume:{i}"},
                    {"text": f"✏️ #{n}", "callback_data": f"edit:{i}"},
                    {"text": f"🗑 #{n}", "callback_data": f"remove:{i}"},
                ])
            else:
                rows.append([
                    {"text": f"⏹ #{n}", "callback_data": f"stop:{i}"},
                    {"text": f"✏️ #{n}", "callback_data": f"edit:{i}"},
                    {"text": f"🗑 #{n}", "callback_data": f"remove:{i}"},
                ])

        rows.append([
            {"text": "➕ Add", "callback_data": "new"},
            {"text": "⏹ Stop all", "callback_data": "stopall"},
            {"text": "▶️ Resume all", "callback_data": "resumeall"},
        ])
        rows.append([{"text": "🔎 Scan now", "callback_data": "scan"}])

        lines.append("\n<i>tap the buttons below to manage searches.</i>")
        if self.state.paused:
            lines.append("\n⏸ alerts are muted globally — /unpause")

        return "\n".join(lines), {"inline_keyboard": rows}

    def _cmd_list(self, chat_id):
        text, markup = self._build_list_message()
        if text is None:
            self.notifier.reply(chat_id, (
                "📭 you're not tracking anything yet.\n\n"
                "tap <b>➕ Add</b> below for a guided setup, or send:\n"
                "• <code>/add macbook pro</code>\n"
                "• <code>/add wd red in toscana</code>"
            ), reply_markup=_MAIN_KEYBOARD)
            return
        self.notifier.reply(chat_id, text, reply_markup=markup)

    def _cmd_status(self, chat_id):
        total = len(self.state.queries)
        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        stopped = total - active
        tracked = self.state.total_tracked()

        if self.state.paused:
            run_line = "⏸ <b>paused</b> — scanning runs, but no alerts are sent\n   → /unpause to unmute"
        else:
            run_line = "▶️ <b>running</b> — new matches will notify you\n   → /pause to mute"

        if total == 0:
            next_step = "\n\nnext: <code>/add something you want</code>"
        elif active == 0:
            next_step = "\n\nall searches are stopped — /list then /resume &lt;n&gt;"
        else:
            next_step = "\n\n/scan to check now · /list to manage searches"

        self.notifier.reply(chat_id, (
            "📡 <b>scanner status</b>\n\n"
            f"{run_line}\n\n"
            f"🔍 searches: <b>{active}</b> active"
            + (f", <b>{stopped}</b> stopped" if stopped else "")
            + f" (of {total})\n"
            f"📦 unique items seen: <b>{tracked}</b>"
            f"{next_step}"
        ), reply_markup=_MAIN_KEYBOARD)

    def _cmd_scan(self, chat_id):
        if self._scanning:
            self.notifier.reply(chat_id, "⏳ a scan is already running — hang tight.")
            return

        active = [q for q in self.state.queries if not self.state.is_query_disabled(q)]
        if not active:
            self.notifier.reply(chat_id, (
                "📭 nothing to scan.\n"
                "add a search with <code>/add …</code> or /resume a stopped one."
            ))
            return

        paused_note = "\n⏸ alerts are muted — new items will be recorded but not notified." if self.state.paused else ""
        self.notifier.reply(chat_id, (
            f"🔎 scanning <b>{len(active)}</b> search"
            f"{'es' if len(active) != 1 else ''} now…"
            f"{paused_note}"
        ))

        self._scanning = True
        try:
            # reload queries/items from disk, but keep the telegram offset we just saved
            saved_update_id = self.state.last_update_id
            self.state = State.load(seed_queries=self.state.queries)
            self.state.last_update_id = max(self.state.last_update_id, saved_update_id)
            result = SubitoScanner(self.state, self.notifiers).run()
            self.state.last_update_id = max(self.state.last_update_id, saved_update_id)
            self.state.save()
        except Exception as e:
            logging.error(f"manual scan failed: {e}")
            self.notifier.reply(chat_id, f"❌ scan failed: {html.escape(str(e))}")
            return
        finally:
            self._scanning = False

        new = result["new"]
        if new == 0:
            self.notifier.reply(chat_id, (
                f"✅ scan done — no new listings.\n"
                f"checked {result['queries']} active search"
                f"{'es' if result['queries'] != 1 else ''}."
            ))
        else:
            self.notifier.reply(chat_id, (
                f"✅ scan done — <b>{new}</b> new listing"
                f"{'s' if new != 1 else ''} "
                f"(alerts {'muted' if self.state.paused else 'sent above'})."
            ))

    def _cmd_plain_text(self, chat_id, text: str):
        """turn free-form messages into ready-to-use add commands."""
        term = " ".join(text.split())
        if len(term) > 80:
            self.notifier.reply(chat_id, (
                "that message looks a bit long for a search.\n"
                "try a short term, or use /help."
            ))
            return

        safe = html.escape(term)
        add_cmd = html.escape(f"/add {term}")
        exact_cmd = html.escape(f"/exact {term}")
        region_cmd = html.escape(f"/add {term} in toscana")
        price_cmd = html.escape(f"/add {term} min 100 max 500")
        self.notifier.reply(chat_id, (
            f"🔎 track <b>{safe}</b>?\n\n"
            f"easiest: tap <b>➕ Add</b> for a guided setup.\n\n"
            f"or copy &amp; send one of these:\n"
            f"• <code>{add_cmd}</code> — all Italy\n"
            f"• <code>{region_cmd}</code> — only Tuscany\n"
            f"• <code>{price_cmd}</code> — price range\n"
            f"• <code>{exact_cmd}</code> — title-only / exact\n\n"
            "/regions for other places · /list for what you already track."
        ), reply_markup=_MAIN_KEYBOARD)

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
                "❎ add cancelled. tap <b>➕ Add</b> whenever you're ready.",
                reply_markup=_MAIN_KEYBOARD,
            )
        else:
            self.notifier.reply(chat_id, "nothing to cancel.", reply_markup=_MAIN_KEYBOARD)

    def _wizard_start(self, chat_id):
        self._wizard = {
            "step": "term",
            "term": None,
            "region": None,
            "min_price": None,
            "max_price": None,
            "exact": False,
        }
        self.notifier.reply(chat_id, (
            "➕ <b>add a search</b> — step 1/4\n\n"
            "what should i look for?\n"
            "<i>example:</i> <code>sh 125</code> or <code>macbook pro</code>\n\n"
            "send /cancel anytime to abort."
        ), reply_markup=_WIZ_CANCEL_KB)

    def _wizard_summary_bits(self) -> str:
        w = self._wizard or {}
        term = html.escape(w.get("term") or "…")
        where = REGION_IDS.get(w["region"], "all Italy") if w.get("region") else "all Italy"
        price = format_price_range(w.get("min_price"), w.get("max_price")) or "any"
        mode = "exact" if w.get("exact") else "broad"
        return (
            f"🔍 <b>{term}</b>\n"
            f"📍 {html.escape(where)} · 💶 {html.escape(price)} · {mode}"
        )

    def _wizard_region_keyboard(self) -> dict:
        rows = []
        row = []
        short_names = {
            "1": "Aosta", "5": "Trentino", "7": "Friuli", "8": "Emilia",
        }
        for rid in sorted(REGION_IDS, key=int):
            label = short_names.get(rid, REGION_IDS[rid])
            row.append({"text": label, "callback_data": f"wiz:region:{rid}"})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "🇮🇹 All Italy (skip)", "callback_data": "wiz:region:skip"}])
        rows.append([{"text": "❌ Cancel", "callback_data": "wiz:cancel"}])
        return {"inline_keyboard": rows}

    def _wizard_ask_region(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "region"
        text = (
            "➕ <b>add a search</b> — step 2/4\n\n"
            f"{self._wizard_summary_bits()}\n\n"
            "limit to a region?\n"
            "tap one below, or type a name (e.g. <code>toscana</code>)."
        )
        markup = self._wizard_region_keyboard()
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_price(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "price"
        text = (
            "➕ <b>add a search</b> — step 3/4\n\n"
            f"{self._wizard_summary_bits()}\n\n"
            "add a price filter?"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "⏭ Skip (any price)", "callback_data": "wiz:price:skip"}],
                [{"text": "💶 Set price…", "callback_data": "wiz:price:set"}],
                [{"text": "❌ Cancel", "callback_data": "wiz:cancel"}],
            ]
        }
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_price_input(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "price_input"
        text = (
            "➕ <b>price filter</b>\n\n"
            f"{self._wizard_summary_bits()}\n\n"
            "send prices like:\n"
            "• <code>500-2000</code>\n"
            "• <code>min 500 max 2000</code>\n"
            "• <code>min 500</code> or <code>max 2000</code>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "⏭ Skip", "callback_data": "wiz:price:skip"}],
                [{"text": "❌ Cancel", "callback_data": "wiz:cancel"}],
            ]
        }
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_exact(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "exact"
        text = (
            "➕ <b>add a search</b> — step 4/4\n\n"
            f"{self._wizard_summary_bits()}\n\n"
            "search mode?"
        )
        markup = {
            "inline_keyboard": [
                [
                    {"text": "🔎 Broad (default)", "callback_data": "wiz:exact:0"},
                    {"text": "🎯 Exact (title-only)", "callback_data": "wiz:exact:1"},
                ],
                [{"text": "❌ Cancel", "callback_data": "wiz:cancel"}],
            ]
        }
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_confirm(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "confirm"
        text = (
            "➕ <b>ready to track?</b>\n\n"
            f"{self._wizard_summary_bits()}\n\n"
            "confirm to save this search."
        )
        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm", "callback_data": "wiz:confirm"},
                    {"text": "🔁 Start over", "callback_data": "wiz:restart"},
                ],
                [{"text": "❌ Cancel", "callback_data": "wiz:cancel"}],
            ]
        }
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_commit(self, chat_id):
        w = self._wizard
        if not w or not w.get("term"):
            self._wizard_clear()
            self.notifier.reply(chat_id, "❓ wizard expired — tap ➕ Add to start again.", reply_markup=_MAIN_KEYBOARD)
            return

        query = build_query(
            w["term"],
            exact=bool(w.get("exact")),
            region=w.get("region"),
            min_price=w.get("min_price"),
            max_price=w.get("max_price"),
        )
        safe = html.escape(w["term"])
        mode = "exact / title-only" if w.get("exact") else "broad"
        where = REGION_IDS.get(w["region"], "all Italy") if w.get("region") else "all Italy"
        price = format_price_range(w.get("min_price"), w.get("max_price")) or "any"
        self._wizard_clear()

        if query in self.state.queries:
            idx = self.state.queries.index(query) + 1
            status = "stopped" if self.state.is_query_disabled(query) else "active"
            tip = f" use /resume {idx} to turn it back on." if status == "stopped" else ""
            self.notifier.reply(chat_id, (
                f"ℹ️ <b>{safe}</b> is already #{idx} ({status}).{tip}\n"
                "/list to see all."
            ), reply_markup=_MAIN_KEYBOARD)
            return

        self.state.add_query(query)
        n = len(self.state.queries)
        self.notifier.reply(chat_id, (
            f"✅ now tracking <b>{safe}</b>\n"
            f"mode: {mode} · where: <b>{html.escape(where)}</b> · "
            f"price: <b>{html.escape(price)}</b> · slot <b>#{n}</b>\n"
            f"you'll get alerts on the next scan when new listings appear.\n\n"
            "/list · /status · /scan"
        ), reply_markup=_MAIN_KEYBOARD)

    def _wizard_handle_text(self, chat_id, text: str):
        if not self._wizard:
            return
        step = self._wizard.get("step")

        if step == "term":
            term = text.strip()
            if len(term) < 2:
                self.notifier.reply(chat_id, (
                    "please send a longer search term (at least 2 characters).\n"
                    "<i>example:</i> <code>sh 125</code>"
                ), reply_markup=_WIZ_CANCEL_KB)
                return
            self._wizard["term"] = term
            self._wizard_ask_region(chat_id)
            return

        if step == "region":
            resolved = resolve_region(text)
            if not resolved:
                self.notifier.reply(chat_id, (
                    f"❓ unknown region <b>{html.escape(text)}</b>.\n"
                    "tap a button below, or try another name (/regions for the list)."
                ), reply_markup=self._wizard_region_keyboard())
                return
            self._wizard["region"] = resolved[0]
            self._wizard_ask_price(chat_id)
            return

        if step == "price_input":
            _, region_id, min_price, max_price, err = parse_search_args(text.split())
            if err:
                self.notifier.reply(chat_id, (
                    f"❓ {html.escape(err)}.\n"
                    "try <code>500-2000</code> or <code>min 500 max 2000</code>."
                ), reply_markup=_WIZ_CANCEL_KB)
                return
            if min_price is None and max_price is None and region_id is None:
                self.notifier.reply(chat_id, (
                    "i need a price — e.g. <code>500-2000</code> — or tap Skip."
                ), reply_markup={
                    "inline_keyboard": [
                        [{"text": "⏭ Skip", "callback_data": "wiz:price:skip"}],
                        [{"text": "❌ Cancel", "callback_data": "wiz:cancel"}],
                    ]
                })
                return
            if region_id is not None:
                self._wizard["region"] = region_id
            self._wizard["min_price"] = min_price
            self._wizard["max_price"] = max_price
            self._wizard_ask_exact(chat_id)
            return

        if step == "price":
            # allow typing a price directly instead of tapping "set"
            _, region_id, min_price, max_price, err = parse_search_args(text.split())
            if not err and (min_price is not None or max_price is not None):
                if region_id is not None:
                    self._wizard["region"] = region_id
                self._wizard["min_price"] = min_price
                self._wizard["max_price"] = max_price
                self._wizard_ask_exact(chat_id)
                return
            self.notifier.reply(chat_id, (
                "tap <b>Skip</b> or <b>Set price</b> below, or send e.g. <code>500-2000</code>."
            ))
            return

        if step in ("exact", "confirm"):
            self.notifier.reply(chat_id, (
                "use the buttons below for this step, or /cancel to abort."
            ))
            return

        self.notifier.reply(chat_id, "use the buttons, or /cancel.")

    def _wizard_handle_callback(self, chat_id, cb_id, data: str, message_id):
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "cancel":
            self.notifier.answer_callback(cb_id, "cancelled")
            self._wizard_cancel(chat_id)
            return

        if action == "restart":
            self.notifier.answer_callback(cb_id, "restart")
            self._wizard_start(chat_id)
            return

        if not self._wizard:
            self.notifier.answer_callback(cb_id, "wizard expired — tap ➕ Add", show_alert=True)
            return

        if action == "region":
            val = parts[2] if len(parts) > 2 else "skip"
            if val == "skip":
                self._wizard["region"] = None
                self.notifier.answer_callback(cb_id, "all Italy")
            elif val in REGION_IDS:
                self._wizard["region"] = val
                self.notifier.answer_callback(cb_id, REGION_IDS[val])
            else:
                self.notifier.answer_callback(cb_id, "unknown region", show_alert=True)
                return
            self._wizard_ask_price(chat_id, edit_message_id=message_id)
            return

        if action == "price":
            val = parts[2] if len(parts) > 2 else "skip"
            if val == "skip":
                self._wizard["min_price"] = None
                self._wizard["max_price"] = None
                self.notifier.answer_callback(cb_id, "any price")
                self._wizard_ask_exact(chat_id, edit_message_id=message_id)
            elif val == "set":
                self.notifier.answer_callback(cb_id, "type a price")
                self._wizard_ask_price_input(chat_id, edit_message_id=message_id)
            else:
                self.notifier.answer_callback(cb_id, "unknown", show_alert=True)
            return

        if action == "exact":
            val = parts[2] if len(parts) > 2 else "0"
            self._wizard["exact"] = val in ("1", "true", "yes")
            self.notifier.answer_callback(cb_id, "exact" if self._wizard["exact"] else "broad")
            self._wizard_ask_confirm(chat_id, edit_message_id=message_id)
            return

        if action == "confirm":
            self.notifier.answer_callback(cb_id, "saved")
            self._wizard_commit(chat_id)
            return

        self.notifier.answer_callback(cb_id, "unknown action", show_alert=True)

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
            f"✅ now tracking <b>{safe}</b>\n"
            f"mode: {mode} · where: <b>{html.escape(where)}</b> · price: <b>{html.escape(price)}</b> · slot <b>#{n}</b>\n"
            f"you'll get alerts on the next scan when new listings appear.\n\n"
            "/list · /status · /scan"
        ))

    def _edit_usage(self, n: int, query: str) -> str:
        """show current filters + copy-ready /edit examples for one search."""
        label = html.escape(query_label(query))
        mode = "exact" if is_exact_query(query) else "broad"
        where = query_region_name(query) or "all Italy"
        price = format_price_range(*query_price_range(query)) or "any"
        return (
            f"✏️ <b>edit search #{n}</b> — {label}\n"
            f"now: {mode} · {html.escape(where)} · {html.escape(price)}\n\n"
            "send one of these (only changes what you write):\n"
            f"• <code>/edit {n} in toscana</code> — set region\n"
            f"• <code>/edit {n} min 500</code> — set min price\n"
            f"• <code>/edit {n} max 2000</code> — set max price\n"
            f"• <code>/edit {n} 500-2000</code> — set both prices\n"
            f"• <code>/edit {n} in toscana min 500 max 2000</code> — several at once\n"
            f"• <code>/edit {n} exact</code> / <code>/edit {n} broad</code> — title-only on/off\n"
            f"• <code>/edit {n} anywhere</code> — remove region\n"
            f"• <code>/edit {n} clear price</code> — remove price filter\n"
            f"• <code>/edit {n} sh 150</code> — rename the term\n\n"
            "history is cleared after an edit so the next scan starts fresh."
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

        old_label = html.escape(query_label(query))
        self.state.update_query(query, new_query)
        new_label = html.escape(query_label(new_query))
        self.notifier.reply(chat_id, (
            f"✅ updated <b>#{n}</b>\n"
            f"<s>{old_label}</s>\n"
            f"→ <b>{new_label}</b>\n\n"
            "history cleared — next /scan treats current listings as new."
        ))

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
                "📭 nothing to wipe — you're not tracking any searches.",
                reply_markup=_MAIN_KEYBOARD,
            )
            return

        confirmed = bool(args) and args[0].lower() in ("confirm", "yes", "--confirm")
        n = len(self.state.queries)
        if not confirmed:
            self.notifier.reply(chat_id, (
                f"⚠️ this will permanently delete <b>{n}</b> search"
                f"{'es' if n != 1 else ''} and all their history.\n\n"
                "to confirm, send:\n"
                "<code>/wipe confirm</code>"
            ), reply_markup=_MAIN_KEYBOARD)
            return

        removed = self.state.wipe_all()
        self.notifier.reply(chat_id, (
            f"🧹 wiped <b>{removed}</b> search{'es' if removed != 1 else ''}.\n"
            "start fresh with <code>/add …</code>"
        ), reply_markup=_MAIN_KEYBOARD)

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
