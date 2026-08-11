<img src="apps/web/public/logo.png" alt="CUFLynx" width="140" align="right" />

# CUFLynx

[![Latest release](https://img.shields.io/github/v/release/physiomelinks/CUFLynx?label=download)](https://github.com/physiomelinks/CUFLynx/releases/latest)

A GUI for [Circulatory Autogen](https://github.com/physiomelinks/circulatory_autogen):
run sensitivity analysis, calibration and uncertainty quantification, and
manually explore how parameters affect your (CellML) model outputs.

## Download the desktop app

| OS | Download |
|----|----------|
| **Linux** (x86-64, glibc 2.35+ / Ubuntu 22.04+) | [**CUFLynx-linux-x86_64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-linux-x86_64) |
| **macOS** — Apple silicon (any M-series; macOS 11+) | [**CUFLynx-macos-arm64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-macos-arm64) |
| **macOS** — Intel (macOS 11+) | [**CUFLynx-macos-x86_64**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-macos-x86_64) |
| **Windows** (x86-64) | [**CUFLynx-windows-x86_64.exe**](https://github.com/physiomelinks/CUFLynx/releases/latest/download/CUFLynx-windows-x86_64.exe) |

Not sure which Mac? **Apple menu → About This Mac**: "Apple M…" is Apple silicon,
"Intel…" is Intel. Every M-series chip runs the same `arm64` build.

The app is self-contained — it bundles Python and everything `circulatory_autogen`
needs, so simulation **and** analysis run without any Python setup.

### Run it

<details open>
<summary><b>macOS</b></summary>

```bash
cd ~/Downloads
chmod +x CUFLynx-macos-arm64          # or CUFLynx-macos-x86_64 on Intel
xattr -d com.apple.quarantine CUFLynx-macos-arm64
./CUFLynx-macos-arm64
```

The app isn't notarized yet, so macOS blocks it until the quarantine flag is
cleared — that's what the `xattr` line does (or right-click → **Open** → **Open**).

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

Antivirus may flag it as a threat. This is a **false positive** — a known quirk of
PyInstaller packaging, not malware. Restore it and allow it, or download again.
(Code signing would stop this for good; it isn't in place yet.)

</details>

**On first run**, point the app at a `circulatory_autogen` checkout under
**Settings → CA dir** (`git clone https://github.com/physiomelinks/circulatory_autogen.git`).
That's the only setup, and it's remembered.

→ **[Using CUFLynx](tutorials/docs/basic.md)** — solver backends, Myokit `.mmt`
models, and replotting a run outside the app.

## For Developers: Install from source

One-time setup (installs the backend + frontend and builds the UI). Works on
Linux, macOS and Windows — needs **Python** and **Node.js**.

```bash
python scripts/install.py
```

## For Developers: Run

```bash
python scripts/run.py
```

This opens the app at **http://localhost:8000**.

Calibration / sensitivity / UQ runs use the Python interpreter you pick in the
top bar — point it at your `circulatory_autogen` virtual environment.

→ **[Developing CUFLynx](tutorials/docs/dev.md)** — hot reload, tests, and
building the desktop executable.
