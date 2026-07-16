# hermes-session-titler

Auto-generate descriptive session titles for Hermes Agent.  Generates a short
title from the full conversation transcript when you `/quit`, and provides
`/retitle` for mid-session regeneration.

> No more "untitled session 47" in your dashboard.

## Install

```bash
pip install hermes-session-titler
```

Then add to `config.yaml`:

```yaml
plugins:
  enabled:
    - session-titler
```

Restart Hermes.

## Usage

- **`/quit`** — title is auto-generated from the full conversation before the session closes
- **`/retitle`** — manually regenerate the title mid-session

The plugin logs to `~/.hermes/personal/logs/session-titler.log`.

### Smart skip

If you resume an old session and immediately `/quit` without adding any new
messages, title generation is skipped — no unnecessary LLM calls.

## How it works

```
/quit or /retitle
  │
  ├─ Read full conversation history from cli.conversation_history
  ├─ Build transcript (~4500 chars, role-labeled)
  ├─ Call auxiliary LLM with titling prompt (max 50 tokens, temp 0.3)
  ├─ Clean the response (strip quotes, "Title:" prefix, cap at 80 chars)
  └─ Write to session DB via SessionDB.set_session_title()
```

Uses the agent's own model through `agent.auxiliary_client.call_llm` — no extra
provider setup.  Titles appear immediately in the TUI session picker and
dashboard.

## Design note

CLI context is resolved from `PluginManager._cli_ref` on every call rather than
stashed in a module-level variable.  This means `/retitle` survives
`importlib.reload` from tools like `hermes-evolve`.

## Config

No configuration required.  The plugin registers the `/retitle` command and
hooks into `pre_command` (for `/quit` titles) and `on_session_start` (for the
message-count baseline).

## Development

```bash
git clone https://github.com/nuffin/hermes-session-titler
cd hermes-session-titler
pip install -e .
```

## License

MIT
