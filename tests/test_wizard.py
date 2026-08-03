from scanner.bot import TelegramBot
from scanner.query import query_label
from scanner.state import State


class _FakeNotifier:
    chat_id = "1"

    def __init__(self):
        self.msgs = []

    def reply(self, *a, **k):
        self.msgs.append(("reply", a, k))

    def edit_message(self, *a, **k):
        self.msgs.append(("edit", a, k))

    def answer_callback(self, *a, **k):
        pass


def test_wizard_commit_builds_query():
    bot = TelegramBot(_FakeNotifier(), State(False, [], [], 0, {}))
    bot._wizard_start("1", term="sh 125")
    assert bot._wizard["step"] == "region"
    bot._wizard_handle_callback("1", "cb", "wiz:region:9", None)
    bot._wizard_handle_callback("1", "cb", "wiz:preset:1", None)
    assert bot._wizard["step"] == "since"
    bot._wizard_handle_callback("1", "cb", "wiz:since:skip", None)
    bot._wizard_handle_callback("1", "cb", "wiz:exact:1", None)
    assert bot._wizard["step"] == "confirm"
    bot._wizard_commit("1")
    assert bot._wizard is None
    assert len(bot.state.queries) == 1
    label = query_label(bot.state.queries[0])
    assert "sh 125" in label
    assert "Toscana" in label
    assert "[exact]" in label
