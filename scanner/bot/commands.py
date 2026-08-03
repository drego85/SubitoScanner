import html
import logging
from typing import Optional

from ..core import SubitoScanner
from ..query import (
    apply_query_patch,
    build_query,
    format_price_range,
    parse_edit_args,
    parse_search_args,
    query_label,
    query_region_name,
    query_title,
)
from ..regions import REGION_IDS
from ..state import State
from .keyboards import _MAIN_KEYBOARD, _btn, _inline


class CommandsMixin:
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
            f"👋 <b>Subito Scanner</b>\n\n{body}",
            reply_markup=_MAIN_KEYBOARD,
        )

    def _build_list_message(self):
        """return (html_text, inline_markup) or (None, None) if empty."""
        if not self.state.queries:
            return None, None

        active = sum(1 for q in self.state.queries if not self.state.is_query_disabled(q))
        stopped = len(self.state.queries) - active
        header = f"📋 <b>Searches</b>  ·  {active} active"
        if stopped:
            header += f" · {stopped} paused"
        if self.state.paused:
            header += "\nAlerts muted — unmute from Status"
        lines = [header, ""]

        rows = []
        for i, q in enumerate(self.state.queries):
            n = i + 1
            paused = self.state.is_query_disabled(q)
            dot = "🔴" if paused else "🟢"
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
                    _btn(f"▶️ Resume #{n}", f"resume:{i}"),
                    _btn(f"✏️ Edit #{n}", f"edit:{i}"),
                    _btn(f"🗑 Delete #{n}", f"remove:{i}"),
                ])
            else:
                rows.append([
                    _btn(f"⏸ Pause #{n}", f"stop:{i}"),
                    _btn(f"✏️ Edit #{n}", f"edit:{i}"),
                    _btn(f"🗑 Delete #{n}", f"remove:{i}"),
                ])

        rows.append([
            _btn("➕ New", "new"),
            _btn("⏹ Pause all", "stopall"),
            _btn("▶️ Resume all", "resumeall"),
        ])
        rows.append([
            _btn("🔎 Scan now", "scan"),
            _btn("🧹 Clear seen", "flush"),
        ])

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
            "📡 <b>Status</b>",
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
                    [_btn("➕ New", "new"), _btn("📋 Searches", "list")],
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
        bits = [f"checked {result['queries']} active"]
        if result.get("listed"):
            bits.append(f"{result['listed']} on Subito")
        if result.get("empty"):
            bits.append(f"{result['empty']} with 0 hits")
        if result.get("errors"):
            bits.append(f"{result['errors']} failed")
        detail = " · ".join(bits)

        if result.get("errors") and new == 0 and not result.get("listed"):
            self.notifier.reply(chat_id, (
                f"⚠️ <b>Scan failed</b>\n"
                f"Subito API error · {detail}"
            ), reply_markup=self._after_action_kb())
        elif new == 0:
            hint = ""
            if result.get("empty") == result.get("queries") and result.get("queries"):
                hint = (
                    "\n\nAll searches returned 0 ads — try broader match, "
                    "widen price, or drop region (/edit)."
                )
            self.notifier.reply(chat_id, (
                f"✅ <b>Scan complete</b>\n"
                f"No new listings · {detail}{hint}"
            ), reply_markup=self._after_action_kb())
        else:
            self.notifier.reply(chat_id, (
                f"✅ <b>Scan complete</b>\n"
                f"<b>{new}</b> new listing{'s' if new != 1 else ''} "
                f"({'muted' if self.state.paused else 'sent above'}) · {detail}"
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
            f"🔎 Track <b>{safe}</b>?\n\n"
            "Continue with guided filters, or add it for all Italy right away."
        ), reply_markup=_inline(
            [_btn("➕ Continue setup", "new"), _btn("✅ Add · All Italy", "quick_italy")],
            [_btn("✖️ Not now", "dismiss")],
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

        term_args, region_id, min_price, max_price, since, err = parse_search_args(args)
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
            since=since,
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
            f"✅ <b>Saved · #{n}</b>\n"
            f"🔍 <b>{safe}</b>\n"
            f"{html.escape(self._format_query_meta(query))}\n\n"
            "You'll be notified when new listings appear."
        ), reply_markup=self._after_action_kb())

    def _edit_usage(self, n: int, query: str) -> str:
        """show current filters + copy-ready /edit examples for one search."""
        title = html.escape(query_title(query))
        meta = html.escape(self._format_query_meta(query))
        return (
            f"✏️ <b>Edit #{n}</b>\n"
            f"🔍 {title}\n"
            f"<i>{meta}</i>\n\n"
            "Send a change (only what you write is updated):\n"
            f"• <code>/edit {n} in toscana</code>\n"
            f"• <code>/edit {n} min 500</code> · <code>/edit {n} max 2000</code>\n"
            f"• <code>/edit {n} 500-2000</code>\n"
            f"• <code>/edit {n} exact</code> · <code>/edit {n} broad</code>\n"
            f"• <code>/edit {n} anywhere</code> · <code>/edit {n} clear price</code>\n"
            f"• <code>/edit {n} since 01/08/2026</code> · <code>/edit {n} last 7d</code>\n"
            f"• <code>/edit {n} clear since</code> · <code>/edit {n} anydate</code>\n"
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
            f"✅ <b>Updated #{n}</b>\n"
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

    def _cmd_flush(self, chat_id):
        """forget all seen listing ids so the next scan re-notifies current ads."""
        cleared = self.state.clear_history()
        self.state.save()
        if cleared == 0:
            self.notifier.reply(chat_id, (
                "🧹 <b>Already empty</b>\n"
                "No seen listings stored — /scan will notify whatever Subito returns."
            ), reply_markup=self._after_action_kb())
            return
        self.notifier.reply(chat_id, (
            f"🧹 <b>Cleared {cleared}</b> seen listing"
            f"{'s' if cleared != 1 else ''}\n\n"
            "Searches are unchanged. Next /scan will treat current ads as new."
        ), reply_markup=self._after_action_kb())

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
                f"⚠️ <b>Delete all searches?</b>\n\n"
                f"This permanently removes <b>{n}</b> search"
                f"{'es' if n != 1 else ''} and their history."
            ), reply_markup=_inline(
                [_btn("🗑 Delete everything", "wipe:ok"), _btn("✖️ Keep them", "wipe:no")],
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
