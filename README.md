# CUFLynx

A GUI for [Circulatory Autogen](https://github.com/physiomelinks/circulatory_autogen):
run sensitivity analysis, calibration and uncertainty quantification, and
manually explore how parameters affect your (CellML) model outputs.

## Download the desktop app

The easiest way to run CUFLynx: grab the file for your OS from the
[latest release](https://github.com/physiomelinks/CUFLynx/releases/latest) and
double-click it. No Python or Node.js needed.

| OS | File |
|----|------|
| Linux | `CUFLynx-linux-x86_64` (`chmod +x` it first) |
| macOS | `CUFLynx-macos-arm64` |
| Windows | `CUFLynx-windows-x86_64.exe` |

On first run, point the app at your `circulatory_autogen` checkout under
**Settings → CA dir**, and pick a Python interpreter for calibration / sensitivity
/ UQ runs (those run in a separate process and need `circulatory_autogen` and its
dependencies installed). Both choices are remembered.

**One prerequisite: a C compiler.** Myokit compiles each CellML model to a native
extension when it runs, so it needs a C toolchain — this can't be shipped inside
the app. If one is missing, CUFLynx says so on startup and tells you how to
install it:

- **Linux** — `sudo apt install build-essential`
- **macOS** — `xcode-select --install`
- **Windows** — [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) ("Desktop development with C++")

(Sundials/CVODE is bundled — you do *not* need to install it separately.)

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
