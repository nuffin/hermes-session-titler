# Hermes Session Titler

A Hermes Agent plugin that auto-generates descriptive session titles.

## Features

- **Auto-title on `/quit`** — generates a short descriptive title from the full conversation transcript before the session closes
- **`/retitle` command** — manually regenerate the session title mid-session
- **Smart skip** — avoids unnecessary LLM calls when resuming an old session and immediately quitting (no new messages since session start)
- **Full-context generation** — uses the same LLM model as your conversation to generate accurate titles

## Installation

```bash
# Clone the repo
git clone https://github.com/your-username/hermes-session-titler.git ~/studio/hermes/projects/hermes-session-titler

# Symlink into your Hermes profile
mkdir -p ~/.hermes/personal/plugins
ln -sf ~/studio/hermes/projects/hermes-session-titler/plugins/session-titler ~/.hermes/personal/plugins/session-titler

# Or for a specific profile:
mkdir -p ~/.hermes/profiles/<profile>/personal/plugins
ln -sf ~/studio/hermes/projects/hermes-session-titler/plugins/session-titler ~/.hermes/profiles/<profile>/personal/plugins/session-titler

# Symlink into Hermes plugins directory
mkdir -p ~/.hermes/plugins
ln -sf ~/.hermes/personal/plugins/session-titler ~/.hermes/plugins/session-titler
```

Enable in your profile's `config.yaml`:

```yaml
plugins:
  enabled:
    - session-titler
```

## Usage

- **`/quit`** — title is auto-generated before the session closes
- **`/retitle`** — manually regenerate the title mid-session

The plugin logs to `~/.hermes/personal/logs/session-titler.log`.

## Requirements

- Hermes Agent (by Nous Research)
- The plugin uses `agent.auxiliary_client.call_llm` for title generation (same model as your conversation)

## License

MIT
