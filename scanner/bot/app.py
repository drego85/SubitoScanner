import logging
import time

from ..notifiers import TelegramNotifier
from ..state import State
from .callbacks import CallbackMixin
from .commands import CommandsMixin
from .formatting import FormattingMixin
from .help import HelpMixin
from .keyboards import (
    _BOT_DESCRIPTION,
    _BOT_SHORT,
    _BUTTON_COMMANDS,
    _COMMANDS,
    _MAIN_KEYBOARD,
)
from .wizard import WizardMixin


class TelegramBot(HelpMixin, WizardMixin, CallbackMixin, CommandsMixin, FormattingMixin):
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
