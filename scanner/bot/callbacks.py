import html

from ..query import query_title
from .keyboards import _btn, _inline


class CallbackMixin:
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
                            f"🗑 <b>Delete search #{n}?</b>\n{title}\n\n"
                            "This cannot be undone.",
                            reply_markup=_inline(
                                [_btn("🗑 Delete", f"rmok:{idx}"), _btn("✖️ Keep", "rmno")],
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
