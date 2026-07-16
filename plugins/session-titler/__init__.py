"""session-titler plugin — auto-generate or retitle session titles.

Triggers on the ``pre_command`` hook when the user runs ``/quit`` or ``/exit``,
and provides a ``/retitle`` command for mid-session title regeneration.
Generates a short descriptive title from the FULL conversation history using
the agent's LLM, then writes it to the session DB (overwrites any old title).

Skips title generation on /quit if no new messages were added since the session started
(avoids unnecessary LLM calls when resuming an old session and immediately quitting).
"""

from __future__ import annotations

import datetime
import os
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


def _on_session_start(**kw: Any) -> None:
    """on_session_start hook — records the DB message_count as baseline."""
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


# ---- full-conversation title prompt ----------------------------------------

_TITLE_PROMPT = (
    "You are a session titling assistant. Given the following conversation transcript, "
    "generate a short, descriptive title (3-10 words) that captures the MAIN topic or outcome "
    "of the ENTIRE conversation. Return ONLY the title text — no quotes, no prefixes, no "
    "punctuation at the end."
)


def _build_conversation_summary(conv: list) -> str:
    """Concatenate conversation into a readable transcript, truncated to ~4000 chars."""
    parts: list[str] = []
    total = 0
    for msg in conv:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        label = "User" if role == "user" else "Assistant"
        snippet = content[:300]
        line = f"{label}: {snippet}"
        if total + len(line) > 4500:
            parts.append(f"... (conversation truncated, {len(conv)} messages total)")
            break
        parts.append(line)
        total += len(line)
    return "\n\n".join(parts)


# ---- hook handler -----------------------------------------------------------

# ---- slash command handler: /title -----------------------------------------
# Manually trigger title regeneration mid-session (without quitting).

_HANDLED_COMMANDS = frozenset({"quit", "exit", "title"})


def _generate_title(cli: Any, command: str) -> str | None:
    """Core title generation logic. Returns the new title or None on failure."""
    session_db = getattr(cli, "_session_db", None)
    session_id = getattr(cli, "session_id", None)
    conv = getattr(cli, "conversation_history", None)

    if not session_db or not session_id or not conv:
        _log(f"missing data: session_db={bool(session_db)}, session_id={bool(session_id)}, conv={bool(conv)} — skipping")
        return None

    _log(f"title: session={session_id} conv={len(conv)} command={command}")

    # For /quit, skip if no new messages since session start.
    # For /title, always generate regardless.
    if command in ("quit", "exit"):
        initial = _session_initial_counts.get(session_id, len(conv))
        if len(conv) <= initial:
            _log(f"no new messages (conv={len(conv)}, baseline={initial}) — skipping")
            return None

    try:
        transcript = _build_conversation_summary(conv)

        runtime: Any = None
        agent = getattr(cli, "agent", None)
        if agent is not None:
            runtime = getattr(agent, "_runtime", None)

        from agent.auxiliary_client import call_llm

        messages = [
            {"role": "system", "content": _TITLE_PROMPT},
            {"role": "user", "content": f"Conversation transcript:\n\n{transcript}"},
        ]

        response = call_llm(
            task="quit_title_generation",
            messages=messages,
            max_tokens=50,
            temperature=0.3,
            timeout=30.0,
            main_runtime=runtime,
        )

        title = (response.choices[0].message.content or "").strip().strip("\"'")
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        if len(title) > 80:
            title = title[:77] + "..."

        if not title:
            _log("LLM returned empty title — skipping")
            return None

        session_db.set_session_title(session_id, title)
        _log(f"set title='{title}' (session={session_id})")
        return title

    except Exception as exc:
        _log_err(f"title generation failed: {exc}")
        _log_err(traceback.format_exc())
        return None


def _on_pre_command(**kw: Any) -> None:
    """pre_command hook handler — fires before any slash command handler."""
    command = kw.get("command")
    if command not in _HANDLED_COMMANDS:
        return

    cli = kw.get("cli")
    if not cli:
        _log("no cli object — skipping")
        return

    session_db = getattr(cli, "_session_db", None)
    session_id = getattr(cli, "session_id", None)
    conv = getattr(cli, "conversation_history", None)

    if not session_db or not session_id or not conv:
        _log(f"missing data: session_db={bool(session_db)}, session_id={bool(session_id)}, conv={bool(conv)} — skipping")
        return

    _log(f"quit: session={session_id} conv={len(conv)}")

    # Skip if no new messages since session start (avoids unnecessary LLM calls
    # when resuming an old session and immediately quitting).
    initial = _session_initial_counts.get(session_id, len(conv))
    if len(conv) <= initial:
        _log(f"no new messages (conv={len(conv)}, baseline={initial}) — skipping")
        return

    try:
        # Build conversation transcript
        transcript = _build_conversation_summary(conv)

        # Get main runtime for LLM call (same model as the conversation)
        runtime: Any = None
        agent = getattr(cli, "agent", None)
        if agent is not None:
            runtime = getattr(agent, "_runtime", None)

        # Call LLM with full conversation context
        from agent.auxiliary_client import call_llm

        messages = [
            {"role": "system", "content": _TITLE_PROMPT},
            {"role": "user", "content": f"Conversation transcript:\n\n{transcript}"},
        ]

        response = call_llm(
            task="quit_title_generation",
            messages=messages,
            max_tokens=50,
            temperature=0.3,
            timeout=30.0,
            main_runtime=runtime,
        )

        title = (response.choices[0].message.content or "").strip().strip("\"'")
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        if len(title) > 80:
            title = title[:77] + "..."

        if not title:
            _log("LLM returned empty title — skipping")
            return

        session_db.set_session_title(session_id, title)
        _log(f"set title='{title}' (session={session_id})")

    except Exception as exc:
        _log_err(f"title generation failed: {exc}")
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
    """Register the pre_command and on_session_start hooks, plus /title command."""
    _log("registering hooks and commands")

    ctx.register_command(
        name="retitle",
        handler=_handle_retitle_command,
        description="Regenerate the session title immediately from full conversation",
        args_hint="",
    )

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_command", _on_pre_command)
