# hermes-session-titler

Auto-generate descriptive session titles for Hermes Agent. On every normal
session finalization (including EOF/Ctrl-D and `/quit`), it builds a complete
title from the existing title, chronological topic summaries, and durable DB
history. `/retitle` runs the same pipeline manually.

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

- **EOF/Ctrl-D or `/quit`** — one automatic, topic-aware title attempt runs at finalization
- **`/retitle`** — manually run the same DB-first title pipeline

Automatic finalization and `/retitle` refresh an existing `llm` title, while preserving titles whose provenance is `user`. They prefer the future public
`SessionDB.refresh_auto_title` API and, on current provenance-capable Hermes,
uses only SessionDB's transaction/sanitization primitives as a constrained
compatibility bridge. Legacy (`NULL`) provenance and user titles are never
replaced. On older cores that cannot make this distinction, existing titles are
retained and `/retitle` reports that safe refresh is unsupported.

## License

MIT


## Repositories

| Role | Repo | PyPI |
|------|------|------|
| Plugin code (this repo) | [hermes-session-titler](https://github.com/nuffin/hermes-session-titler) | — |
| Pip wrapper | [hermes-session-titler-pip](https://github.com/nuffin/hermes-session-titler-pip) | [hermes-session-titler](https://pypi.org/project/hermes-session-titler/) |
