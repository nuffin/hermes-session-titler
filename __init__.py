"""session-titler plugin — finalize-aware, topic-aware session titles.

Registers ``on_session_finalize`` so normal EOF/Ctrl-D teardown is covered, with
``pre_command`` retained as a compatibility path for ``/quit`` and ``/exit``.
``/retitle`` runs the same DB-first generation pipeline manually. Title context
combines the existing title, chronological topic summaries, and durable session
history; automatic writes use provenance-aware APIs when the core provides them.
"""

from __future__ import annotations

import datetime
import os
import threading
import traceback
from pathlib import Path
from typing import Any

# ---- dedicated log file ----------------------------------------------------

_HERMES_PERSONAL = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "personal"
_LOG_DIR = _HERMES_PERSONAL / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_info_path = _LOG_DIR / "session-titler.log"
_err_path = _LOG_DIR / "session-titler.err"


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_info_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} [INFO] {msg}\n")
            f.flush()
    except Exception:
        pass


def _log_err(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_err_path, "a", encoding="utf-8") as f:
            f.write(f"{ts} [WARNING] {msg}\n")
            f.flush()
    except Exception:
        pass


_log("plugin loaded")


# ---- per-session baseline: message count at session start ------------------
# Tracked so we can skip title generation on /quit if the user added no
# new messages since the session was resumed / created.

_session_initial_counts: dict[str, int] = {}
_generated_sessions: set[str] = set()
_generation_in_progress: set[str] = set()
_generation_lock = threading.Lock()


def _record_baseline(**kw: Any) -> None:
    """Record the DB message_count as baseline for a session (start or resume)."""
    session_id = kw.get("session_id")
    if not session_id or session_id in _session_initial_counts:
        return
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        session = db.get_session(session_id)
        if session:
            _session_initial_counts[session_id] = session.get("message_count", 0)
            _log(f"baseline: session={session_id} initial_msg_count={_session_initial_counts[session_id]}")
    except Exception as exc:
        _log_err(f"could not get baseline message_count: {exc}")


def _on_session_start(**kw: Any) -> None:
    """on_session_start hook — records the DB message_count as baseline."""
    _record_baseline(**kw)


def _on_session_resume(**kw: Any) -> None:
    """on_session_resume hook — records the DB message_count as baseline."""
    _record_baseline(**kw)


# ---- full-conversation title prompt ----------------------------------------

_TITLE_PROMPT = (
    "You are a session titling assistant. Generate a complete, concise title (3-10 words) "
    "for the whole session. Preserve still-valid key concepts from the existing title. "
    "When the session has multiple topics, represent the most important 2-3 topics and "
    "retain at least one session-level core keyword. Use the chronological topic summaries "
    "and durable transcript together. Return ONLY the title text — no quotes, prefixes, or "
    "ending punctuation."
)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


def _build_conversation_summary(conv: list, max_chars: int = 18000) -> str:
    """Build a balanced transcript that retains evidence from the whole session."""
    lines = []
    for message in conv:
        content = _message_text(message).strip()
        if not content:
            continue
        role = message.get("role", "")
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    if not lines:
        return ""

    full = "\n\n".join(lines)
    if len(full) <= max_chars:
        return full

    # Sample evenly across the entire durable history instead of consuming only
    # the beginning. Every sampled message receives an equal character budget.
    sample_count = min(len(lines), 24)
    if sample_count == 1:
        indices = [0]
    else:
        indices = sorted({round(index * (len(lines) - 1) / (sample_count - 1)) for index in range(sample_count)})
    budget = max(160, max_chars // max(1, len(indices)) - 32)
    sampled = [lines[index][:budget] for index in indices]
    return "\n\n".join(sampled) + f"\n\n[Balanced sample of {len(lines)} transcript messages]"


def _load_durable_history(session_db: Any, session_id: str) -> list:
    loader = getattr(session_db, "get_messages_as_conversation", None)
    if loader is None:
        return []
    attempts = (
        {"include_ancestors": True, "include_inactive": True, "repair_alternation": True},
        {"include_ancestors": True, "repair_alternation": True},
        {"include_ancestors": True},
        {},
    )
    for kwargs in attempts:
        try:
            loaded = loader(session_id, **kwargs)
            return list(loaded or [])
        except TypeError:
            continue
        except Exception as exc:
            _log_err(f"could not load messages from DB: {exc}")
            return []
    return []


def _load_topics(session_db: Any, session_id: str) -> list[dict[str, Any]]:
    getter = getattr(session_db, "get_topic_title_context", None)
    if getter is None:
        getter = getattr(session_db, "get_topics", None)
    if getter is None:
        return []
    try:
        topics = list(getter(session_id) or [])
    except Exception as exc:
        _log_err(f"could not load session topics: {exc}")
        return []
    return sorted(topics, key=lambda topic: (topic.get("created_at") is None, topic.get("created_at", 0), topic.get("id", 0)))


def _topic_detail(session_db: Any, session_id: str, topic: dict[str, Any]) -> str:
    summary = topic.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    getter = getattr(session_db, "get_topic_messages", None)
    if getter is None or topic.get("id") is None:
        return "(no summary available)"
    for kwargs in ({"include_inactive": True}, {}):
        try:
            topic_messages = getter(session_id, topic["id"], **kwargs)
            detail = _build_conversation_summary(list(topic_messages or []), max_chars=2400)
            return detail or "(no summary available)"
        except TypeError:
            continue
        except Exception as exc:
            _log_err(f"could not load messages for topic {topic.get('id')}: {exc}")
            break
    return "(no summary available)"


def _build_title_context(session_db: Any, session_id: str, session: dict[str, Any], fallback_conv: list | None) -> tuple[str, list]:
    durable = _load_durable_history(session_db, session_id)
    conv = durable or list(fallback_conv or [])
    parts = [
        "Existing session title and provenance:",
        f"title={session.get('title') or '(none)'}",
        f"source={session.get('title_source') or '(unknown)'}",
    ]
    topics = _load_topics(session_db, session_id)
    if topics:
        parts.append("\nTopics in chronological order:")
        for index, topic in enumerate(topics, 1):
            metadata = (
                f"state={topic.get('state') or 'unknown'}, "
                f"messages={topic.get('message_count', 0)}"
            )
            parts.append(
                f"{index}. {topic.get('title') or '(untitled)'} [{metadata}]\n"
                f"   {_topic_detail(session_db, session_id, topic)}"
            )
    transcript = _build_conversation_summary(conv)
    if transcript:
        parts.extend(("\nDurable session transcript (balanced across the whole session):", transcript))
    return "\n".join(parts), conv


def _write_title(session_db: Any, session_id: str, title: str) -> bool:
    auto_writer = getattr(session_db, "set_auto_title", None)
    if auto_writer is not None:
        source = getattr(session_db, "TITLE_SOURCE_LLM", "llm")
        try:
            return bool(auto_writer(session_id, title, source=source))
        except AttributeError:
            pass
    legacy_auto_writer = getattr(session_db, "set_auto_title_if_empty", None)
    if legacy_auto_writer is not None:
        return bool(legacy_auto_writer(session_id, title))
    # Oldest cores have no provenance-safe automatic write. Never replace a
    # pre-existing title through that compatibility path.
    session = session_db.get_session(session_id) or {}
    if session.get("title"):
        return False
    return bool(session_db.set_session_title(session_id, title))


def _clean_title(response: Any) -> str:
    title = (response.choices[0].message.content or "").strip().strip("\"'")
    if title.lower().startswith("title:"):
        title = title[6:].strip()
    if len(title) > 80:
        title = title[:77] + "..."
    return title


# ---- hook and command handlers ---------------------------------------------

_HANDLED_COMMANDS = frozenset({"quit", "exit"})


def _generate_title_for_session(
    session_db: Any,
    session_id: str,
    *,
    command: str,
    fallback_conv: list | None = None,
    runtime: Any = None,
) -> str | None:
    """Run the unified DB-first title pipeline for one session."""
    session = session_db.get_session(session_id) if hasattr(session_db, "get_session") else None
    if not session:
        _log(f"session {session_id} not found — skipping title generation")
        return None
    if session.get("title") and session.get("title_source") in ("user", None):
        _log(f"human or legacy title already holds session={session_id} — preserving")
        return None

    context, conv = _build_title_context(session_db, session_id, session, fallback_conv)
    message_count = session.get("message_count")
    if message_count == 0 and not conv:
        _log(f"no messages in session {session_id} — skipping title generation")
        return None
    if not conv and not _load_topics(session_db, session_id):
        _log(f"no trustworthy title context for session {session_id} — preserving existing title")
        return None

    baseline = _session_initial_counts.get(session_id)
    if command != "retitle" and baseline is not None and message_count is not None and message_count <= baseline:
        _log(f"no new messages (db={message_count}, baseline={baseline}) — skipping")
        return None

    with _generation_lock:
        if session_id in _generated_sessions or session_id in _generation_in_progress:
            _log(f"title already generated or in progress for session={session_id} — skipping duplicate")
            return None
        _generation_in_progress.add(session_id)

    try:
        _log(f"starting title rebuild: session={session_id} command={command}")
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="quit_title_generation",
            messages=[
                {"role": "system", "content": _TITLE_PROMPT},
                {"role": "user", "content": context},
            ],
            max_tokens=50,
            temperature=0.3,
            timeout=30.0,
            main_runtime=runtime,
        )
        title = _clean_title(response)
        if not title:
            _log("LLM returned empty title — preserving existing title")
            return None
        if not _write_title(session_db, session_id, title):
            _log(f"title write declined by provenance policy for session={session_id}")
            return None
        with _generation_lock:
            _generated_sessions.add(session_id)
        _log(f"set title='{title}' (session={session_id})")
        return title
    except Exception as exc:
        _log_err(f"title generation failed: {exc}")
        _log_err(traceback.format_exc())
        return None
    finally:
        with _generation_lock:
            _generation_in_progress.discard(session_id)


def _generate_title(cli: Any, command: str) -> str | None:
    """Generate from a live CLI while keeping durable DB history authoritative."""
    session_db = getattr(cli, "_session_db", None)
    session_id = getattr(cli, "session_id", None)
    if not session_db or not session_id:
        _log(f"missing data: session_db={bool(session_db)}, session_id={bool(session_id)} — skipping")
        return None
    runtime = None
    agent = getattr(cli, "agent", None)
    if agent is not None:
        runtime = getattr(agent, "_runtime", None)
    return _generate_title_for_session(
        session_db,
        session_id,
        command=command,
        fallback_conv=getattr(cli, "conversation_history", None),
        runtime=runtime,
    )


def _on_pre_command(**kw: Any) -> None:
    """Compatibility path for /quit and /exit before CLI teardown."""
    if kw.get("command") not in _HANDLED_COMMANDS:
        return
    cli = kw.get("cli")
    if cli is not None:
        _generate_title(cli, kw.get("command"))


def _on_session_finalize(**kw: Any) -> None:
    """Finalize handler that requires only the durable session ID and SessionDB."""
    session_id = kw.get("session_id")
    if not session_id:
        return
    try:
        from hermes_state import SessionDB
        _generate_title_for_session(SessionDB(), session_id, command="finalize")
    except Exception as exc:
        _log_err(f"finalize title generation failed: {exc}")
        _log_err(traceback.format_exc())


def _handle_retitle_command(args: str) -> str:
    """Slash command handler for /retitle — regenerate session title immediately."""
    _log("/retitle command invoked")

    from hermes_cli.plugins import get_plugin_manager
    cli = getattr(get_plugin_manager(), "_cli_ref", None)
    if cli is None:
        return "No CLI context available — use /quit to generate title instead."

    title = _generate_title(cli, "retitle")
    if title:
        return f"Session title updated: {title}"
    return "Title generation failed — check logs."


# ---- plugin entry point -----------------------------------------------------


def register(ctx: Any) -> None:
    """Register lifecycle hooks and the manual /retitle command."""
    _log("registering hooks and commands")

    ctx.register_command(
        name="retitle",
        handler=_handle_retitle_command,
        description="Regenerate the session title immediately from full conversation",
        args_hint="",
    )

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_resume", _on_session_resume)
    ctx.register_hook("pre_command", _on_pre_command)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
