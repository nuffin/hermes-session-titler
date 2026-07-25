# hermes-session-titler

Auto-generate descriptive session titles for Hermes Agent. Generates a short
title from the full conversation transcript when you `/quit`, and provides
`/retitle` for mid-session regeneration.

## Install

Symlink or clone into `~/.hermes/plugins/`:

```bash
ln -sf /path/to/hermes-session-titler ~/.hermes/plugins/session-titler
```

Or install via pip:

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

## License

MIT


## Repositories

| Role | Repo | PyPI |
|------|------|------|
| Plugin code (this repo) | [hermes-session-titler](https://github.com/nuffin/hermes-session-titler) | — |
| Pip wrapper | [hermes-session-titler-pip](https://github.com/nuffin/hermes-session-titler-pip) | [hermes-session-titler](https://pypi.org/project/hermes-session-titler/) |
