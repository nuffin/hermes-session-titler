from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


PLUGIN_PATH = Path(__file__).parents[1] / "__init__.py"


class FakeResponse:
    def __init__(self, title: str = "Complete Session Title") -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=title))]


class FakeDB:
    TITLE_SOURCE_LLM = "llm"

    def __init__(
        self,
        *,
        messages=None,
        session=None,
        topics=None,
        topic_messages=None,
        support_provenance=True,
    ) -> None:
        self.messages = list(messages or [])
        self.session = session or {
            "id": "session-1",
            "message_count": len(self.messages),
            "title": None,
            "title_source": None,
        }
        self.topics = list(topics or [])
        self.topic_messages = dict(topic_messages or {})
        self.support_provenance = support_provenance
        self.writes = []
        self.load_calls = []
        self.topic_context_calls = 0

    def get_session(self, session_id):
        return dict(self.session) if self.session and session_id == self.session["id"] else None

    def get_messages_as_conversation(self, session_id, **kwargs):
        self.load_calls.append(kwargs)
        return list(self.messages)

    def get_topic_title_context(self, session_id):
        self.topic_context_calls += 1
        return list(self.topics)

    def get_topics(self, session_id):
        raise AssertionError("stable topic-title context API should be preferred")

    def get_topic_messages(self, session_id, topic_id, **kwargs):
        return list(self.topic_messages.get(topic_id, []))

    def set_auto_title(self, session_id, title, *, source):
        if not self.support_provenance:
            raise AttributeError("unsupported")
        self.writes.append(("auto", session_id, title, source))
        return True

    def refresh_auto_title(self, session_id, title, *, source):
        if not self.support_provenance:
            raise AttributeError("unsupported")
        self.writes.append(("refresh", session_id, title, source))
        return True

    def set_auto_title_if_empty(self, session_id, title):
        self.writes.append(("legacy-auto", session_id, title))
        return not self.session.get("title")

    def set_session_title(self, session_id, title):
        self.writes.append(("legacy", session_id, title))
        return True


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    name = f"session_titler_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def install_core(monkeypatch, db, llm_calls):
    state = types.ModuleType("hermes_state")
    state.SessionDB = lambda: db
    monkeypatch.setitem(sys.modules, "hermes_state", state)

    auxiliary = types.ModuleType("agent.auxiliary_client")

    def call_llm(**kwargs):
        llm_calls.append(kwargs)
        return FakeResponse()

    auxiliary.call_llm = call_llm
    agent = types.ModuleType("agent")
    agent.auxiliary_client = auxiliary
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", auxiliary)


def messages(*contents):
    return [
        {"role": "user" if index % 2 == 0 else "assistant", "content": content}
        for index, content in enumerate(contents)
    ]


def test_registers_finalize_hook_and_manifest_declares_it(plugin):
    class Context:
        def __init__(self):
            self.hooks = {}

        def register_command(self, **kwargs):
            pass

        def register_hook(self, name, handler):
            self.hooks[name] = handler

    ctx = Context()
    plugin.register(ctx)

    assert ctx.hooks["on_session_finalize"] is plugin._on_session_finalize
    assert "on_session_finalize" in (PLUGIN_PATH.parent / "plugin.yaml").read_text()
    assert "on_session_resume" not in ctx.hooks
    assert "on_session_resume" not in (PLUGIN_PATH.parent / "plugin.yaml").read_text()


def test_finalize_uses_session_id_and_db_without_cli(plugin, monkeypatch):
    db = FakeDB(messages=messages("EOF alpha", "EOF result"))
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert len(llm_calls) == 1
    assert db.writes == [("auto", "session-1", "Complete Session Title", "llm")]


def test_quit_then_finalize_generates_only_once(plugin, monkeypatch):
    db = FakeDB(messages=messages("quit alpha", "quit result"))
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)
    cli = SimpleNamespace(_session_db=db, session_id="session-1", conversation_history=db.messages, agent=None)

    plugin._on_pre_command(command="quit", cli=cli, session_id="session-1")
    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert len(llm_calls) == 1
    assert len(db.writes) == 1


def test_missing_baseline_does_not_skip_nonempty_session(plugin, monkeypatch):
    db = FakeDB(messages=messages("baseline alpha", "baseline result"))
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert len(llm_calls) == 1


def test_empty_session_skips_llm(plugin, monkeypatch):
    db = FakeDB(messages=[], session={"id": "session-1", "message_count": 0, "title": None, "title_source": None})
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert llm_calls == []
    assert db.writes == []


def test_title_input_contains_existing_title_and_all_topics_in_chronological_order(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("durable history", "durable result"),
        session={"id": "session-1", "message_count": 2, "title": "Core Alpha", "title_source": "llm"},
        topics=[
            {"id": 3, "title": "Late Gamma", "summary": "gamma summary", "state": "active", "message_count": 5, "created_at": 30},
            {"id": 1, "title": "Early Alpha", "summary": "alpha summary", "state": "cold", "message_count": 7, "created_at": 10},
            {"id": 2, "title": "Middle Beta", "summary": "beta summary", "state": "warm", "message_count": 4, "created_at": 20},
        ],
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._generate_title_for_session(db, "session-1", command="finalize")

    prompt = llm_calls[0]["messages"][1]["content"]
    assert "Core Alpha" in prompt
    assert db.topic_context_calls >= 1
    assert prompt.index("Early Alpha") < prompt.index("Middle Beta") < prompt.index("Late Gamma")
    assert "alpha summary" in prompt and "beta summary" in prompt and "gamma summary" in prompt
    assert "state=cold" in prompt and "messages=7" in prompt
    assert db.writes == [("refresh", "session-1", "Complete Session Title", "llm")]


def test_long_history_preserves_early_middle_and_late_keywords(plugin, monkeypatch):
    long_messages = messages(
        "EARLY_KEYWORD " + "a" * 5000,
        "early response",
        "MIDDLE_KEYWORD " + "b" * 5000,
        "middle response",
        "LATE_KEYWORD " + "c" * 5000,
        "late response",
    )
    db = FakeDB(messages=long_messages)
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._generate_title_for_session(db, "session-1", command="finalize")

    prompt = llm_calls[0]["messages"][1]["content"]
    assert "EARLY_KEYWORD" in prompt
    assert "MIDDLE_KEYWORD" in prompt
    assert "LATE_KEYWORD" in prompt


def test_missing_topic_summary_includes_topic_messages(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("history alpha", "history result"),
        topics=[{"id": 9, "title": "Unsummarized", "summary": None, "state": "active", "message_count": 2, "created_at": 1}],
        topic_messages={9: messages("TOPIC_DETAIL_KEYWORD", "topic result")},
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._generate_title_for_session(db, "session-1", command="finalize")

    assert "TOPIC_DETAIL_KEYWORD" in llm_calls[0]["messages"][1]["content"]


def test_durable_db_history_wins_over_partial_live_history(plugin, monkeypatch):
    db = FakeDB(messages=messages("DURABLE_EARLY", "DURABLE_LATE"))
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)
    cli = SimpleNamespace(
        _session_db=db,
        session_id="session-1",
        conversation_history=messages("PARTIAL_ONLY"),
        agent=None,
    )

    plugin._generate_title(cli, "retitle")

    prompt = llm_calls[0]["messages"][1]["content"]
    assert "DURABLE_EARLY" in prompt and "DURABLE_LATE" in prompt
    assert db.load_calls[0]["include_ancestors"] is True


def test_automatic_finalize_protects_human_title_without_llm_call(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("new alpha", "new result"),
        session={"id": "session-1", "message_count": 2, "title": "Human Chosen Title", "title_source": "user"},
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert llm_calls == []
    assert db.writes == []


def test_automatic_finalize_refreshes_existing_llm_title(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("new alpha", "new result"),
        session={"id": "session-1", "message_count": 2, "title": "Earlier Generated Title", "title_source": "llm"},
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert len(llm_calls) == 1
    assert db.writes == [("refresh", "session-1", "Complete Session Title", "llm")]


def test_automatic_finalize_protects_legacy_title_without_llm_call(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("new alpha", "new result"),
        session={"id": "session-1", "message_count": 2, "title": "Legacy Title", "title_source": None},
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert llm_calls == []
    assert db.writes == []


def test_automatic_finalize_on_old_core_does_not_replace_existing_title(plugin, monkeypatch):
    class OldDB:
        def __init__(self):
            self.writes = []

        def get_session(self, session_id):
            return {"id": session_id, "message_count": 2, "title": "Earlier Generated Title", "title_source": "llm"}

        def get_messages_as_conversation(self, session_id, **kwargs):
            return messages("new alpha", "new result")

        def set_session_title(self, session_id, title):
            self.writes.append((session_id, title))
            return True

    db = OldDB()
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert len(llm_calls) == 1
    assert db.writes == []


def test_manual_retitle_also_preserves_human_title(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("new alpha", "new result"),
        session={"id": "session-1", "message_count": 2, "title": "Human Chosen Title", "title_source": "user"},
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)
    cli = SimpleNamespace(_session_db=db, session_id="session-1", conversation_history=db.messages, agent=None)

    result = plugin._generate_title(cli, "retitle")
    assert result.outcome == plugin._PROTECTED
    assert llm_calls == []
    assert db.writes == []


def test_manual_retitle_refreshes_an_existing_llm_title(plugin, monkeypatch):
    db = FakeDB(
        messages=messages("new alpha", "new result"),
        session={"id": "session-1", "message_count": 2, "title": "Earlier Generated Title", "title_source": "llm"},
    )
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)
    cli = SimpleNamespace(_session_db=db, session_id="session-1", conversation_history=db.messages, agent=None)

    result = plugin._generate_title(cli, "retitle")
    assert (result.outcome, result.title) == (plugin._UPDATED, "Complete Session Title")
    assert db.writes == [("refresh", "session-1", "Complete Session Title", "llm")]


def test_older_core_without_topics_or_provenance_uses_compatible_fallback(plugin, monkeypatch):
    class LegacyDB:
        def __init__(self):
            self.writes = []

        def get_session(self, session_id):
            return {"id": session_id, "message_count": 2, "title": None}

        def get_messages_as_conversation(self, session_id, include_ancestors=True, repair_alternation=True):
            return messages("legacy alpha", "legacy result")

        def set_auto_title_if_empty(self, session_id, title):
            self.writes.append((session_id, title))
            return True

    db = LegacyDB()
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)

    plugin._on_session_finalize(session_id="session-1", platform="cli")

    assert len(llm_calls) == 1
    assert db.writes == [("session-1", "Complete Session Title")]


def test_current_core_fallback_refresh_uses_sessiondb_transaction(plugin):
    class CurrentCoreDB:
        TITLE_SOURCE_LLM = "llm"

        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT UNIQUE, title_source TEXT)")
            self.conn.execute("INSERT INTO sessions VALUES ('session-1', 'Old LLM Title', 'llm')")

        def get_session(self, session_id):
            row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return dict(row) if row else None

        @staticmethod
        def sanitize_title(title):
            return title.strip()

        def set_auto_title(self, session_id, title, *, source):
            raise AssertionError("refresh must use the compatibility transaction, not set_auto_title")

        @staticmethod
        def _is_compression_ancestor(conn, *, ancestor_id, descendant_id):
            return False

        def _execute_write(self, operation):
            result = operation(self.conn)
            self.conn.commit()
            return result

    db = CurrentCoreDB()

    assert plugin._write_title(db, "session-1", "Refreshed LLM Title", refresh=True) == plugin._UPDATED
    assert db.get_session("session-1") == {
        "id": "session-1", "title": "Refreshed LLM Title", "title_source": "llm"
    }


def test_manual_retitle_after_automatic_rebuild_is_not_deduped(plugin, monkeypatch):
    db = FakeDB(messages=messages("alpha", "result"))
    llm_calls = []
    install_core(monkeypatch, db, llm_calls)
    cli = SimpleNamespace(_session_db=db, session_id="session-1", conversation_history=db.messages, agent=None)

    assert plugin._generate_title(cli, "finalize").outcome == plugin._UPDATED
    assert plugin._generate_title(cli, "retitle").outcome == plugin._UPDATED
    assert len(llm_calls) == 2


def test_manual_retitle_on_old_core_reports_unsupported_compatibility(plugin):
    class OldDB:
        def get_session(self, session_id):
            return {"id": session_id, "title": "Unattributed Old Title", "message_count": 2}

    assert plugin._write_title(OldDB(), "session-1", "New Title", refresh=True) == plugin._UNSUPPORTED
