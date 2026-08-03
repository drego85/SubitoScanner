import html

from ..query import (
    format_price_range,
    is_exact_query,
    query_label,
    query_price_range,
    query_region_name,
    query_title,
)
from .keyboards import _btn, _inline


class FormattingMixin:
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

    def _empty_searches_text(self) -> str:
        return (
            "📭 <b>No searches yet</b>\n\n"
            "Create one in under a minute — I'll ask for the term, "
            "region, price, and match mode."
        )

    def _empty_searches_kb(self) -> dict:
        return _inline([_btn("➕ New search", "new")])

    def _after_action_kb(self) -> dict:
        """inline shortcuts shown after successful mutations."""
        return _inline(
            [_btn("📋 Searches", "list"), _btn("🔎 Scan now", "scan"), _btn("➕ New", "new")],
            [_btn("🧹 Clear seen", "flush")],
        )

    def _status_actions_kb(self) -> dict:
        mute = _btn("🔔 Unmute alerts", "unpause") if self.state.paused else _btn("⏸ Mute alerts", "pause")
        return _inline(
            [_btn("🔎 Scan now", "scan"), _btn("📋 Searches", "list"), _btn("➕ New", "new")],
            [mute, _btn("⏹ Pause all", "stopall"), _btn("▶️ Resume all", "resumeall")],
            [_btn("🧹 Clear seen", "flush"), _btn("🗑 Delete all…", "wipe")],
        )

    def _short_list_preview(self) -> str:
        lines = ["<b>your searches:</b>"]
        for i, q in enumerate(self.state.queries):
            icon = "🔴" if self.state.is_query_disabled(q) else "🟢"
            lines.append(f"{icon} <b>#{i + 1}</b> {html.escape(query_label(q))}")
        return "\n".join(lines)
