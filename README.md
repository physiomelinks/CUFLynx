<div align="center">

  <!-- Logo Banner or Transparent Icon -->
  <a href="https://github.com/physiomelinks/CUFLynx">
    <img 
        src="apps/web/public/logo_transparent.png" 
        alt="CUFLynx Logo" 
        width="180" 
        style="filter: drop-shadow(0px 0px 8px rgba(255, 255, 255, 0.49));"
    >
    </a>

  <!-- Main Title (Using styled <p> to prevent the bottom border/line) -->
  <p align="center">
    <strong style="font-size: 2.25em;">CUFLynx</strong>
  </p>

  <!-- Badges directly below title without any separating line -->
  <p align="center">
    <a href="https://github.com/physiomelinks/CUFLynx/releases/latest">
      <img src="https://img.shields.io/github/v/release/physiomelinks/CUFLynx?label=download&style=flat-square" alt="Latest Release">
    </a>
    <a href="https://github.com/physiomelinks/CUFLynx/blob/main/LICENSE">
      <img src="https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square" alt="License">
    </a>
  </p>

</div>

# CUFLynx
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

The app is not notarised yet, so macOS blocks it until the quarantine flag is
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

**No setup needed.** The app ships with its own Python and its own copy of
**libCUFLynx** (the circulatory_autogen engine), so simulation, calibration,
sensitivity and UQ all run out of the box.

→ **[Using CUFLynx](tutorials/docs/misc.md)** — solver backends, Myokit `.mmt`
models, and replotting a run outside the app.

## Using your own Python

The app runs everything in its own bundled Python by default, and that is the
recommended way to use it. You only need your own interpreter to use a package
the bundle does not carry — `aadc`, a patched libCUFLynx, a specific numpy — or
to run on a machine where you already maintain the environment.

Pick it under **Settings → Python interpreter**. That one choice governs both
tiers: live simulation (the sliders) and the analysis runs (calibration,
sensitivity, UQ). So whatever you pick has to be able to import the engine —
the bundled copy is inside the executable and is not importable from outside it.

Install into that interpreter:

```bash
pip install "libcuflynx[mpi]"      # the engine, plus multi-rank support
```

That single package brings everything the runners need: numpy, scipy, pandas,
myokit, libcellml, SALib, emcee, nevergrad, statsmodels and the rest. The `[mpi]`
extra adds mpi4py and schwimmbad, needed only if you set **Cores > 1** — it
requires an MPI toolchain (`libopenmpi-dev` or `mpich`) already present, which is
why it is optional. Two more, only if you use those features:

```bash
pip install "libcuflynx[uq]"          # the pyMC sampler (+66 MB)
pip install "libcuflynx[emulation]"   # surrogate models — pulls torch (+750 MB)
```

`pip install -e .` is **not** the equivalent: that installs *this* repo
(`cuflynx-api`, the server), which is not what the runners need and does not pull
libCUFLynx in. Use it only if you are developing CUFLynx itself, in which case see
below. To develop the engine rather than the app, `pip install -e .` in a
`circulatory_autogen` checkout and point **Settings → CA dir** at it.

### On an HPC node, or anywhere `/tmp` is unwritable

libCUFLynx caches flattened CellML in the system temp directory, which is `/tmp`
by default. Where that is unwritable, too small, or purged mid-job, set `TMPDIR`
before launching and everything follows it — no `sudo`, no code change:

```bash
export TMPDIR="$SCRATCH/tmp"   # or any directory you can write to
mkdir -p "$TMPDIR"
```

Prefer node-local scratch over a shared filesystem when you have it: the cache is
rewritten per run, and every rank of an MPI job writes the same file.

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
