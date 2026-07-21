# CUFLynx

[![Latest release](https://img.shields.io/github/v/release/physiomelinks/CUFLynx?label=download)](https://github.com/physiomelinks/CUFLynx/releases/latest)

A GUI for [Circulatory Autogen](https://github.com/physiomelinks/circulatory_autogen):
run sensitivity analysis, calibration and uncertainty quantification, and
manually explore how parameters affect your (CellML) model outputs.

## Download the desktop app

| OS | Download |
|----|----------|
| **Linux** (x86-64, glibc 2.35+ / Ubuntu 22.04+) | [**CUFLynx-linux-x86_64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-linux-x86_64) |
| **macOS** — Apple silicon (any M-series: M1–M5 and later; macOS 11+) | [**CUFLynx-macos-arm64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-macos-arm64) |
| **macOS** — Intel (macOS 11+) | [**CUFLynx-macos-x86_64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-macos-x86_64) |
| **Windows** (x86-64) | [**CUFLynx-windows-x86_64.exe**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-windows-x86_64.exe) |

Not sure which Mac you have? **Apple menu → About This Mac**: anything starting
"Apple M" is Apple silicon; "Intel…" is Intel. Every M-series chip runs the same
`arm64` build.

### Run it

<details open>
<summary><b>macOS</b></summary>

Apple silicon:

```bash
cd ~/Downloads
chmod +x CUFLynx-macos-arm64
xattr -d com.apple.quarantine CUFLynx-macos-arm64
./CUFLynx-macos-arm64
```

Intel:

```bash
cd ~/Downloads
chmod +x CUFLynx-macos-x86_64
xattr -d com.apple.quarantine CUFLynx-macos-x86_64
./CUFLynx-macos-x86_64
```

The app isn't notarized yet, so macOS blocks the download until the quarantine
flag is cleared — the `xattr` line does that, or right-click → **Open** → **Open**.

</details>

<details>
<summary><b>Linux</b></summary>

```bash
cd ~/Downloads
chmod +x CUFLynx-linux-x86_64
./CUFLynx-linux-x86_64
```

</details>

<details>
<summary><b>Windows</b></summary>

Double-click `CUFLynx-windows-x86_64.exe`. If SmartScreen warns, choose
**More info** → **Run anyway**.

Windows Defender (or another antivirus) may flag the file as a threat. This is a
**false positive** — a known quirk of the PyInstaller packaging the app uses, not
actual malware. If it's quarantined, restore it and allow it in your antivirus,
or download again. (The app isn't code-signed yet, which is what would stop this
for good.)

</details>

These links always point at the newest release. All builds are also listed on the
[releases page](https://github.com/physiomelinks/CUFLynx/releases/latest).

The app is self-contained — it bundles Python and everything
`circulatory_autogen` needs, so simulation **and** analysis (sensitivity /
calibration / UQ) run without any Python setup.

On first run, point the app at a `circulatory_autogen` checkout under
**Settings → CA dir** (clone it with
`git clone https://github.com/physiomelinks/circulatory_autogen.git`). That's the
only setup; it's remembered.

**Developing circulatory_autogen?** You can switch analysis to your own Python
under **Settings → Python interpreter** (pick one with your local CA + its deps
installed) instead of the bundled one, so your edits to CA take effect.

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
