import html

from ..query import (
    build_query,
    format_price_range,
    format_since,
    parse_search_args,
    parse_since_value,
)
from ..regions import REGION_IDS, resolve_region
from .keyboards import (
    _MAIN_KEYBOARD,
    _PRICE_PRESETS,
    _btn,
    _inline,
    _step_bar,
)


class WizardMixin:
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
        return f"➕ <b>New search</b>  ·  {_step_bar(step)}\n<b>{title}</b>"

    def _wizard_draft(self) -> str:
        w = self._wizard or {}
        if not w.get("term"):
            return ""
        where = REGION_IDS.get(w["region"], "All Italy") if w.get("region") else "All Italy"
        price = format_price_range(w.get("min_price"), w.get("max_price")) or "any price"
        since = format_since(w.get("since")) or "any date"
        mode = "exact" if w.get("exact") else "broad"
        bits = [html.escape(where), html.escape(price), html.escape(since)]
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
            nav.append(_btn("❌ Cancel", "wiz:cancel"))
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
            "since": None,
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
        rows.append([_btn("🇮🇹 All Italy", "wiz:region:skip")])
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
            _btn("💶 Custom…", "wiz:price:set"),
            _btn("⏭ Any price", "wiz:price:skip"),
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
        markup = self._wizard_nav([_btn("⏭ Any price", "wiz:price:skip")])
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_since(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "since"
        text = (
            f"{self._wizard_header(4, 'Posted from when?')}"
            f"{self._wizard_draft()}\n\n"
            "Optional — only keep ads posted on/after a date.\n"
            "Or type e.g. <code>01/08/2026</code> / <code>2026-08-01</code>."
        )
        markup = self._wizard_nav(
            [_btn("📅 Today", "wiz:since:today"), _btn("🗓 Last 7 days", "wiz:since:7d")],
            [_btn("📆 Last 30 days", "wiz:since:30d"), _btn("⏭ Any date", "wiz:since:skip")],
        )
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_exact(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "exact"
        text = (
            f"{self._wizard_header(5, 'Match mode')}"
            f"{self._wizard_draft()}\n\n"
            "<b>Broad</b> — more results\n"
            "<b>Exact</b> — title-only (Subito “cerca solo nel titolo”)"
        )
        markup = self._wizard_nav([
            _btn("🔎 Broad", "wiz:exact:0"),
            _btn("🎯 Exact", "wiz:exact:1"),
        ])
        if edit_message_id:
            self.notifier.edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            self.notifier.reply(chat_id, text, reply_markup=markup)

    def _wizard_ask_confirm(self, chat_id, edit_message_id=None):
        self._wizard["step"] = "confirm"
        self._wizard["exact_set"] = True
        text = (
            f"➕ <b>New search</b>  ·  ready"
            f"{self._wizard_draft()}\n\n"
            "Save this search?"
        )
        markup = self._wizard_nav([
            _btn("✅ Save", "wiz:confirm"),
            _btn("🔁 Start over", "wiz:restart"),
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
            since=w.get("since"),
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
            f"✅ <b>Saved · #{n}</b>\n"
            f"🔍 <b>{safe}</b>\n"
            f"{meta}\n\n"
            "You'll be notified when new listings appear."
        ), reply_markup=self._after_action_kb())

    def _wizard_go_back(self, chat_id, message_id):
        step = self._wizard.get("step")
        if step in ("region",):
            self._wizard["term"] = None
            self._wizard["step"] = "term"
            self.notifier.edit_message(chat_id, message_id, (
                f"{self._wizard_header(1, 'What are you looking for?')}\n\n"
                "Type a search term."
            ), reply_markup=self._wizard_nav(back=False))
        elif step in ("price", "price_input"):
            self._wizard_ask_region(chat_id, edit_message_id=message_id)
        elif step == "since":
            self._wizard_ask_price(chat_id, edit_message_id=message_id)
        elif step == "exact":
            self._wizard_ask_since(chat_id, edit_message_id=message_id)
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
            _, region_id, min_price, max_price, _, err = parse_search_args(text.split())
            if err:
                self.notifier.reply(chat_id, (
                    f"{html.escape(err)}.\n"
                    "Try <code>500-2000</code> or <code>min 500 max 2000</code>."
                ), reply_markup=self._wizard_nav([_btn("⏭ Any price", "wiz:price:skip")]))
                return
            if min_price is None and max_price is None and region_id is None:
                if step == "price":
                    self.notifier.reply(chat_id, (
                        "Pick a preset, tap <b>Any price</b>, or send <code>500-2000</code>."
                    ))
                    return
                self.notifier.reply(chat_id, (
                    "Send a price like <code>500-2000</code>, or tap Any price."
                ), reply_markup=self._wizard_nav([_btn("⏭ Any price", "wiz:price:skip")]))
                return
            if region_id is not None:
                self._wizard["region"] = region_id
            self._wizard["min_price"] = min_price
            self._wizard["max_price"] = max_price
            self._wizard_ask_since(chat_id)
            return

        if step == "since":
            parsed = parse_since_value(text.strip())
            if not parsed:
                self.notifier.reply(chat_id, (
                    "Invalid date. Use <code>01/08/2026</code>, <code>2026-08-01</code>, "
                    "or tap a button."
                ), reply_markup=self._wizard_nav(
                    [_btn("📅 Today", "wiz:since:today"), _btn("⏭ Any date", "wiz:since:skip")],
                ))
                return
            self._wizard["since"] = parsed
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
            self._wizard_ask_since(chat_id, edit_message_id=message_id)
            return

        if action == "price":
            val = parts[2] if len(parts) > 2 else "skip"
            if val == "skip":
                self._wizard["min_price"] = None
                self._wizard["max_price"] = None
                self.notifier.answer_callback(cb_id, "Any price")
                self._wizard_ask_since(chat_id, edit_message_id=message_id)
            elif val == "set":
                self.notifier.answer_callback(cb_id)
                self._wizard_ask_price_input(chat_id, edit_message_id=message_id)
            else:
                self.notifier.answer_callback(cb_id, "Unknown", show_alert=True)
            return

        if action == "since":
            val = parts[2] if len(parts) > 2 else "skip"
            if val == "skip":
                self._wizard["since"] = None
                self.notifier.answer_callback(cb_id, "Any date")
            elif val == "today":
                self._wizard["since"] = parse_since_value("today")
                self.notifier.answer_callback(cb_id, "Today")
            elif val in ("7d", "30d"):
                self._wizard["since"] = parse_since_value(val)
                self.notifier.answer_callback(cb_id, f"Last {val[:-1]} days")
            else:
                self.notifier.answer_callback(cb_id, "Unknown", show_alert=True)
                return
            self._wizard_ask_exact(chat_id, edit_message_id=message_id)
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
