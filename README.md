# CUFLynx

[![Latest release](https://img.shields.io/github/v/release/physiomelinks/CUFLynx?label=download)](https://github.com/physiomelinks/CUFLynx/releases/latest)

A GUI for [Circulatory Autogen](https://github.com/physiomelinks/circulatory_autogen):
run sensitivity analysis, calibration and uncertainty quantification, and
manually explore how parameters affect your (CellML) model outputs.

## Download the desktop app

The easiest way to run CUFLynx: download the one file for your OS and double-click
it. No Python or Node.js needed.

| OS | Download |
|----|----------|
| **Linux** | [**CUFLynx-linux-x86_64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-linux-x86_64) — then `chmod +x CUFLynx-linux-x86_64` |
| **macOS** (Apple silicon) | [**CUFLynx-macos-arm64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-macos-arm64) |
| **Windows** | [**CUFLynx-windows-x86_64.exe**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-windows-x86_64.exe) |

These links always point at the newest release. All builds are also listed on the
[releases page](https://github.com/physiomelinks/CUFLynx/releases/latest).

On first run, point the app at your `circulatory_autogen` checkout under
**Settings → CA dir**, and pick a Python interpreter for calibration / sensitivity
/ UQ runs (those run in a separate process and need `circulatory_autogen` and its
dependencies installed). Both choices are remembered.

### Optional: a C compiler (only for the Myokit/CVODE backend)

CUFLynx works out of the box with no compiler. Of the three solver backends, only
one needs a C toolchain:

| Backend (Settings) | Solver | Needs a C compiler? |
|---|---|---|
| `python` | scipy `solve_ivp` | no |
| `casadi_python` | `casadi_integrator` | no |
| `cellml_only` | `CVODE_myokit` | **yes** |

Myokit compiles each CellML model to a native extension when it runs, and that
toolchain can't be shipped inside the app. If it's missing, CUFLynx shows a
warning and you simply pick one of the other two backends. To enable
`CVODE_myokit`:

- **Linux** — `sudo apt install build-essential`
- **macOS** — `xcode-select --install`
- **Windows** — [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) ("Desktop development with C++")

(Sundials/CVODE itself is bundled — you do *not* need to install it separately.)

## Install from source

One-time setup (installs the backend + frontend and builds the UI). Works on
Linux, macOS and Windows — needs **Python** and **Node.js** installed.

```bash
python scripts/install.py
```

## Run

```bash
python scripts/run.py
```

This opens the app at **http://localhost:8000**.

Calibration / sensitivity / UQ runs use the Python interpreter you pick in the
top bar — point it at your `circulatory_autogen` virtual environment.

## Build the desktop app yourself

```bash
python scripts/package.py      # -> dist/CUFLynx (or dist/CUFLynx.exe)
```

Builds the frontend, then freezes the backend + UI + a native window into one
executable. PyInstaller can't cross-compile, so build on the OS you're targeting
(the release workflow does this on Linux, macOS and Windows runners).

Want a native window without packaging? `python apps/desktop/app.py`.

---

Developer setup, dev mode (hot reload), API reference and tests:
see [`apps/README.md`](apps/README.md).
