import html

from ..regions import REGION_IDS
from .keyboards import _btn, _inline


class HelpMixin:
    def _help_menu_text(self) -> str:
        return (
            "📖 <b>Help</b>\n\n"
            "Pick a topic — or use the buttons below the chat for everyday actions."
        )

    def _help_menu_kb(self) -> dict:
        return _inline(
            [_btn("➕ Add a search", "help:add"), _btn("✏️ Edit", "help:edit")],
            [_btn("📋 Searches & bulk", "help:bulk"), _btn("🔔 Alerts", "help:alerts")],
            [_btn("🗺 Regions", "help:regions"), _btn("💡 Examples", "help:examples")],
        )

    def _help_topic(self, topic: str):
        pages = {
            "add": (
                "<b>Add a search</b>\n\n"
                "<b>Guided (easiest)</b>\n"
                "Tap <b>➕ New</b> or send /add — then answer each step "
                "(region, price, <b>from date</b>, match mode).\n\n"
                "<b>One-liner</b>\n"
                "<code>/add sh 125 in toscana min 500 max 2000</code>\n"
                "<code>/add sh 125 since 01/08/2026</code> — from that day on\n"
                "<code>/add sh 125 last 7d</code> — last 7 days\n"
                "<code>/exact wd red in toscana</code> — title-only\n\n"
                "Filters can be in any order: <code>in …</code> · <code>min</code>/<code>max</code> · "
                "<code>since</code>/<code>last 7d</code> · <code>500-2000</code>"
            ),
            "edit": (
                "<b>Edit a search</b>\n\n"
                "Open <b>Searches</b> → tap ✏️, or:\n"
                "<code>/edit 1 in toscana</code>\n"
                "<code>/edit 1 min 500 max 2000</code>\n"
                "<code>/edit 1 since 01/08/2026</code> · <code>/edit 1 last 7d</code>\n"
                "<code>/edit 1 clear since</code> · <code>/edit 1 anydate</code>\n"
                "<code>/edit 1 exact</code> · <code>/edit 1 broad</code>\n"
                "<code>/edit 1 anywhere</code> — drop region\n"
                "<code>/edit 1 clear price</code>\n\n"
                "Only the parts you write change. History resets after an edit."
            ),
            "bulk": (
                "<b>Searches & bulk</b>\n\n"
                "• <b>Searches</b> — per-item Pause / Edit / Delete\n"
                "• Pause all / Resume all — from Searches or Status\n"
                "• <b>Clear seen</b> — forget seen ads; next scan re-notifies current ones\n"
                "• Delete all — Status → Delete all… (asks confirmation)\n"
                "• /stop 1 · /resume 1 · /remove 1 — by number"
            ),
            "alerts": (
                "<b>Alerts</b>\n\n"
                "• /pause — mute notifications (scanning continues)\n"
                "• /unpause — turn alerts back on\n"
                "• <b>Scan</b> — check Subito immediately\n"
                "• <b>Clear seen</b> / /flush — empty seen history, then scan again\n"
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
