# Developing CUFLynx

Setup and running from source are in the [README](../../README.md). This covers
what comes after.

## Hot reload

```bash
python scripts/run.py --dev
```

Runs the backend with `uvicorn --reload` **and** the Vite dev server together,
opening **http://localhost:5173**. Vite proxies `/api` to the backend on `:8000`,
so keep the backend on the default port in dev. Ctrl+C stops both.

## Tests

Two suites — keep both green:

```bash
cd apps/api && pytest -m "not integration"   # backend, no Myokit needed
cd apps/web && npm test                      # frontend (vitest)
```

The integration tier needs Myokit and `circulatory_autogen` importable; those
tests skip themselves otherwise, so run plain `pytest` to include them. If you
work in a git worktree, set `CIRCULATORY_AUTOGEN_SRC` — the default sibling-clone
lookup misses from there, and the integration tests skip silently rather than
fail, which looks like everything passing.

## Build the desktop app

```bash
python scripts/package.py      # -> dist/CUFLynx (or dist/CUFLynx.exe)
```

Builds the frontend, then freezes the backend + UI + a native window into one
executable. PyInstaller can't cross-compile, so build on the OS you're targeting
(the release workflow does this on Linux, macOS and Windows runners).

Want a native window without packaging? `python apps/desktop/app.py`.

## Going deeper

- **[`apps/README.md`](../../apps/README.md)** — repository layout, how the single
  server serves both halves, the backend API surface, and the frontend structure.
- **[`packaging/README.md`](../../packaging/README.md)** — what goes into the
  frozen bundle and the hazards of freezing a JIT-compiling simulator.
- **[`CLAUDE.md`](../../CLAUDE.md)** — architecture and the reasoning behind the
  parts that look surprising.
