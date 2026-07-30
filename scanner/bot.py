import html
import logging
import time
from typing import Optional

from .notifiers import TelegramNotifier
from .state import State
from .core import SubitoScanner
from .utils import (
    REGION_IDS,
    build_query,
    is_exact_query,
    query_label,
    query_region_name,
    query_title,
    resolve_region,
    split_term_and_region,
)

# short descriptions show in telegram's "/" menu
_COMMANDS = [
    {"command": "start",   "description": "Welcome & quick start"},
    {"command": "help",    "description": "All commands with examples"},
    {"command": "list",    "description": "Your tracked searches"},
    {"command": "status",  "description": "Scanner status at a glance"},
    {"command": "scan",    "description": "Scan Subito now (don't wait for cron)"},
    {"command": "add",     "description": "Track a search — /add wd red in toscana"},
    {"command": "exact",   "description": "Title-only — /exact wd red in toscana"},
    {"command": "regions", "description": "List Italian regions you can use"},
    {"command": "stop",    "description": "Pause one search — /stop 1"},
    {"command": "resume",  "description": "Resume one search — /resume 1"},
    {"command": "remove",  "description": "Delete a search — /remove 1"},
    {"command": "pause",   "description": "Mute all notifications"},
    {"command": "unpause", "description": "Unmute all notifications"},
]

_BOT_SHORT = "Alerts you when new Subito.it listings match your searches."
_BOT_DESCRIPTION = (
    "Monitor Subito.it and get Telegram alerts for new listings.\n\n"
    "Quick start:\n"
    "1. /add macbook pro — all Italy\n"
    "2. /add wd red in toscana — only Tuscany\n"
    "3. /exact wd red in toscana — title-only + region\n"
    "4. /scan — check Subito right now\n"
    "5. /regions — see all regions\n\n"
    "Tip: type a search term (no slash) and I'll show you how to track it."
)


class TelegramBot:
    def __init__(self, notifier: TelegramNotifier, state: State, notifiers: list = None):
        self.notifier = notifier
        self.state = state
        # used by /scan to send listing alerts (defaults to telegram only)
        self.notifiers = notifiers if notifiers is not None else ([notifier] if notifier else [])
        self._scanning = False

    def poll(self):
        """fetch pending updates once, dispatch commands, and persist state."""
        self._register_bot_meta()
        updates = self.notifier.get_updates(self.state.last_update_id + 1)
        for update in updates:
            self.state.last_update_id = update["update_id"]
            self._process_update(update)
        if updates:
            self.state.save()

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
                    self.state.last_update_id = update["update_id"]
                    self._process_update(update)
                if updates:
                    self.state.save()
            except Exception as e:
                logging.error(f"bot service error: {e}")
                time.sleep(5)

    def _register_bot_meta(self):
        self.notifier.register_commands(_COMMANDS)
        self.notifier.set_descriptions(_BOT_SHORT, _BOT_DESCRIPTION)

    # ── routing ───────────────────────────────────────────────────────────────

    def _process_update(self, update: dict):
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")
        if not text or not chat_id or str(chat_id) != str(self.notifier.chat_id):
            return

        text = text.strip()
        if not text.startswith("/"):
            self._cmd_plain_text(chat_id, text)
            return

        parts = text.split()
        command = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1:]

        handlers = {
            "start":   lambda: self._cmd_start(chat_id),
            "help":    lambda: self._cmd_help(chat_id),
            "list":    lambda: self._cmd_list(chat_id),
            "status":  lambda: self._cmd_status(chat_id),
            "scan":    lambda: self._cmd_scan(chat_id),
            "add":     lambda: self._cmd_add(chat_id, args, exact=False),
            "exact":   lambda: self._cmd_add(chat_id, args, exact=True),
            "regions": lambda: self._cmd_regions(chat_id),
            "remove":  lambda: self._cmd_remove(chat_id, args),
            "stop":    lambda: self._cmd_stop(chat_id, args),
            "resume":  lambda: self._cmd_resume(chat_id, args),
            "pause":   lambda: self._cmd_pause(chat_id),
            "unpause": lambda: self._cmd_unpause(chat_id),
        }
        handler = handlers.get(command)
        if handler:
            handler()
        else:
            self.notifier.reply(chat_id, (
                "❓ i don't know that command.\n\n"
                "try /help for the full list, or just type what you want to search "
                "(e.g. <code>macbook pro</code>) and i'll show you how to track it."
            ))

    # ── command handlers ──────────────────────────────────────────────────────

    def _cmd_start(self, chat_id):
        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        paused_note = "\n⏸ notifications are currently <b>paused</b> — /unpause to resume.\n" if self.state.paused else ""
        self.notifier.reply(chat_id, (
            "👋 <b>welcome to subito scanner</b>\n\n"
            "i watch <b>subito.it</b> for you and send a message when a new listing matches.\n"
            f"{paused_note}\n"
            "<b>quick start</b>\n"
            "• /add macbook pro — all Italy\n"
            "• /add wd red in toscana — only Tuscany\n"
            "• /exact wd red in toscana — title-only + region\n"
            "• /scan — check Subito now (don't wait for cron)\n"
            "• /regions — list regions · /list — your searches\n\n"
            f"you have <b>{active}</b> active search"
            f"{'es' if active != 1 else ''}"
            f" out of <b>{len(self.state.queries)}</b> total.\n\n"
            "💡 tip: type a search term without a command and i'll offer the right /add for you.\n"
            "full guide → /help"
        ))

    def _cmd_help(self, chat_id):
        self.notifier.reply(chat_id, (
            "📖 <b>commands</b>\n\n"
            "<b>searches</b>\n"
            "/add &lt;term&gt; — track a broad search (all Italy)\n"
            "    <i>ex:</i> <code>/add macbook pro</code>\n"
            "/add &lt;term&gt; in &lt;region&gt; — limit where to look\n"
            "    <i>ex:</i> <code>/add wd red in toscana</code>\n"
            "/exact &lt;term&gt; [in &lt;region&gt;] — title-only / exact keywords\n"
            "    <i>ex:</i> <code>/exact wd red in toscana</code>\n"
            "/regions — show all Italian regions you can use\n"
            "/scan — run a Subito check now (don't wait for cron)\n"
            "/list — numbered list of your searches\n"
            "/stop &lt;n&gt; — pause search #n (clears its history)\n"
            "/resume &lt;n&gt; — turn search #n back on\n"
            "/remove &lt;n&gt; — delete search #n forever\n\n"
            "<b>notifications</b>\n"
            "/pause — mute all alerts (scanning continues)\n"
            "/unpause — unmute alerts\n"
            "/status — running / paused + counts\n\n"
            "<b>tips</b>\n"
            "• /exact = subito <i>cerca solo nel titolo</i>\n"
            "• add <code>in toscana</code> (or any region) to filter by place\n"
            "• send a plain term like <code>iphone 15</code> for ready-to-tap commands"
        ))

    def _cmd_list(self, chat_id):
        if not self.state.queries:
            self.notifier.reply(chat_id, (
                "📭 you're not tracking anything yet.\n\n"
                "try:\n"
                "• <code>/add macbook pro</code>\n"
                "• <code>/add wd red in toscana</code>\n"
                "• <code>/exact wd red in toscana</code>"
            ))
            return

        lines = ["📋 <b>your searches</b>\n"]
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
            badge_txt = f" · {' · '.join(badges)}" if badges else ""
            count = len(self.state.items_by_query.get(q, []))
            title = html.escape(query_title(q))
            lines.append(
                f"<b>#{n}</b> {status}{badge_txt}\n"
                f"   {title}\n"
                f"   📦 {count} item{'s' if count != 1 else ''} seen"
            )

        lines.append(
            "\n<i>actions:</i> /stop 1 · /resume 1 · /remove 1\n"
            "<i>add more:</i> /add … in toscana · /exact … · /regions"
        )
        if self.state.paused:
            lines.append("\n⏸ alerts are muted globally — /unpause")
        self.notifier.reply(chat_id, "\n".join(lines))

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
        ))

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
            # reload from disk in case cron updated state since last poll
            self.state = State.load(seed_queries=self.state.queries)
            result = SubitoScanner(self.state, self.notifiers).run()
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
        self.notifier.reply(chat_id, (
            f"🔎 track <b>{safe}</b>?\n\n"
            f"copy &amp; send one of these:\n"
            f"• <code>{add_cmd}</code> — all Italy\n"
            f"• <code>{region_cmd}</code> — only Tuscany\n"
            f"• <code>{exact_cmd}</code> — title-only / exact\n\n"
            "/regions for other places · /list for what you already track."
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

    def _cmd_add(self, chat_id, args: list, exact: bool):
        cmd = "exact" if exact else "add"
        if not args:
            if exact:
                self.notifier.reply(chat_id, (
                    "usage: <code>/exact &lt;term&gt; [in &lt;region&gt;]</code>\n\n"
                    "title-only / exact keywords — fewer false matches.\n"
                    "<i>examples:</i>\n"
                    "• <code>/exact wd red</code>\n"
                    "• <code>/exact wd red in toscana</code>\n\n"
                    "/regions to see places · /add for a broader search."
                ))
            else:
                self.notifier.reply(chat_id, (
                    "usage: <code>/add &lt;term&gt; [in &lt;region&gt;]</code>\n\n"
                    "<i>examples:</i>\n"
                    "• <code>/add macbook pro</code> — all Italy\n"
                    "• <code>/add wd red in toscana</code> — only Tuscany\n"
                    "• <code>/exact wd red in toscana</code> — title-only + region\n\n"
                    "/regions for the full list."
                ))
            return

        # allow "/add exact term" as well as dedicated /exact
        if not exact and args[0].lower() in ("exact", "--exact"):
            exact = True
            cmd = "exact"
            args = args[1:]
            if not args:
                self.notifier.reply(chat_id, (
                    "usage: <code>/add exact &lt;term&gt; [in &lt;region&gt;]</code>\n"
                    "or simply: <code>/exact &lt;term&gt; in toscana</code>"
                ))
                return

        # detect unknown trailing "in something"
        if len(args) >= 3 and args[-2].lower() == "in" and resolve_region(args[-1]) is None:
            self.notifier.reply(chat_id, (
                f"❓ unknown region <b>{html.escape(args[-1])}</b>.\n"
                "send /regions to see valid names "
                "(e.g. <code>toscana</code>, <code>lombardia</code>)."
            ))
            return

        term_args, region_id = split_term_and_region(args)
        term = " ".join(term_args).strip()
        if not term:
            self.notifier.reply(chat_id, (
                f"please include a search term.\n"
                f"<code>/{cmd} wd red in toscana</code>"
            ))
            return

        query = build_query(term, exact=exact, region=region_id)
        safe = html.escape(term)
        mode = "exact / title-only" if exact else "broad"
        where = query_region_name(query) or "all Italy"

        if query in self.state.queries:
            idx = self.state.queries.index(query) + 1
            status = "stopped" if self.state.is_query_disabled(query) else "active"
            tip = f" use /resume {idx} to turn it back on." if status == "stopped" else ""
            self.notifier.reply(chat_id, (
                f"ℹ️ <b>{safe}</b> ({mode}, {html.escape(where)}) is already #{idx} ({status}).{tip}\n"
                "/list to see all."
            ))
            return

        self.state.add_query(query)
        n = len(self.state.queries)
        self.notifier.reply(chat_id, (
            f"✅ now tracking <b>{safe}</b>\n"
            f"mode: {mode} · where: <b>{html.escape(where)}</b> · slot <b>#{n}</b>\n"
            f"you'll get alerts on the next scan when new listings appear.\n\n"
            "/list · /status · /regions"
        ))

    def _cmd_remove(self, chat_id, args: list):
        query = self._resolve_query(chat_id, args, "remove")
        if query is None:
            return
        label = html.escape(query_label(query))
        self.state.remove_query(query)
        self.notifier.reply(chat_id, (
            f"🗑 deleted <b>{label}</b>\n"
            "its history is gone. /list to see what's left."
        ))

    def _cmd_stop(self, chat_id, args: list):
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

    def _cmd_resume(self, chat_id, args: list):
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
