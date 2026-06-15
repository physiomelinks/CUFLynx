# CUFLynx

A GUI for [Circulatory Autogen](https://github.com/physiomelinks/circulatory_autogen):
run sensitivity analysis, calibration and uncertainty quantification, and
manually explore how parameters affect your (CellML) model outputs.

## Install

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

---

Developer setup, dev mode (hot reload), API reference and tests:
see [`apps/README.md`](apps/README.md).
