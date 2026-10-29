"""A document recording every decision the extraction was made under.

An obs_data.json is a list of numbers. Six months later the questions are: which
recordings went in, which were left out and why, what was measured over what part
of the sweep, what correction was applied to the voltage, and which model
variable each observable was bound to. None of that is recoverable from the
output, so it is written down here.

**LaTeX, and the PDF is optional.** The ``.tex`` is always written; ``pdflatex``
is run only if it is on PATH. A frozen CUFLynx will usually not have a TeX
distribution, and refusing to produce the report in that case would be worse
than producing a source file the user can compile anywhere. A missing toolchain
is an ``[info]`` line, a failing one is a ``[warning]``, and neither is an error.

**Two passes when it does run.** ``longtable`` needs a second pass to settle its
column widths; the CLI this replaces runs one, which is why its first page is
misaligned.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

from .config import timeline_for
from .discovery import split_group_key

#: Enough for a long document; a runaway pdflatex must not hang a job.
COMPILE_TIMEOUT_S = 60

_PREAMBLE = r"""\documentclass{article}
\usepackage[a4paper,margin=2cm]{geometry}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{graphicx}
\usepackage{parskip}
\usepackage[hidelinks]{hyperref}
\begin{document}
\footnotesize
"""


@dataclass
class ReportResult:
    tex_path: str
    pdf_path: str | None = None
    notes: list[str] = field(default_factory=list)


def escape(text) -> str:
    r"""Escape the ten characters TeX treats specially.

    Filenames in this corpus are full of underscores and dots, so getting this
    wrong does not produce a slightly-off document -- it produces one that does
    not compile at all.

    The backslash has to go first, because everything else is escaped *with* a
    backslash. But its replacement (``\textbackslash{}``) contains braces, which
    the brace rule would then escape in turn, giving
    ``\textbackslash\{\}``. So it goes to a placeholder that cannot occur in
    the input, and comes back at the end.
    """
    out = str(text if text is not None else "")
    placeholder = "\x00BACKSLASH\x00"
    out = out.replace("\\", placeholder)
    for char in "&%$#_{}":
        out = out.replace(char, "\\" + char)
    out = out.replace("~", r"\textasciitilde{}")
    out = out.replace("^", r"\textasciicircum{}")
    return out.replace(placeholder, r"\textbackslash{}")


def write_report(
    config: dict,
    outcome,
    docs_dir: str,
    *,
    thumbnails: dict | None = None,
    log=None,
) -> ReportResult:
    """Write ``<docs_dir>/<name>_extraction.tex`` and, if possible, its PDF."""
    log = log or (lambda _m: None)
    os.makedirs(docs_dir, exist_ok=True)
    name = config.get("name") or "extraction"
    tex_path = os.path.join(docs_dir, f"{name}_extraction.tex")

    body = "".join([
        _header(config, outcome),
        _binding_section(config),
        _modifier_section(config),
        _dataset_section(config, "calibration", thumbnails or {}),
        _dataset_section(config, "validation", thumbnails or {}),
        _group_section(config),
        _outcome_section(outcome),
    ])
    with open(tex_path, "w", encoding="utf-8") as fh:
        fh.write(_PREAMBLE + body + "\n\\end{document}\n")

    result = ReportResult(tex_path=tex_path)
    if (config.get("report") or {}).get("compile_pdf", True):
        result.pdf_path, notes = compile_pdf(tex_path, log=log)
        result.notes.extend(notes)
    else:
        result.notes.append("PDF compilation was turned off for this extraction.")
    return result


def compile_pdf(tex_path: str, *, log=None) -> tuple[str | None, list[str]]:
    """Run pdflatex twice, or explain why the ``.tex`` is all there is.

    Never raises. The three outcomes are: no toolchain (an ``[info]``, because
    nothing is wrong -- a frozen app has no TeX), a compile that failed (a
    ``[warning]`` with the tail of the output), and success.
    """
    log = log or (lambda _m: None)
    notes: list[str] = []
    if shutil.which("pdflatex") is None:
        message = (
            "pdflatex is not on PATH, so only the .tex was written. It can be "
            "compiled anywhere, or with a TeX distribution installed.")
        notes.append(message)
        log(f"[info] {message}")
        return None, notes

    out_dir = os.path.dirname(os.path.abspath(tex_path))
    pdf_path = os.path.splitext(tex_path)[0] + ".pdf"
    # Twice: longtable needs a second pass to settle its column widths.
    for attempt in (1, 2):
        try:
            proc = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", out_dir, tex_path],
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S,
                check=False)
        except subprocess.TimeoutExpired:
            message = (f"pdflatex timed out after {COMPILE_TIMEOUT_S}s; the .tex "
                       f"was written and is still usable.")
            notes.append(message)
            log(f"[warning] {message}")
            return None, notes
        except OSError as exc:  # pragma: no cover - which() said it was there
            notes.append(f"could not run pdflatex: {exc}")
            return None, notes
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout or "").strip().splitlines()[-20:])
            message = (f"pdflatex failed on pass {attempt}; the .tex was written "
                       f"and is still usable.")
            notes.append(message + (f"\n{tail}" if tail else ""))
            log(f"[warning] {message}")
            return None, notes

    return (pdf_path if os.path.isfile(pdf_path) else None), notes


# ---------------------------------------------------------------------------
def _header(config: dict, outcome) -> str:
    report = config.get("report") or {}
    title = report.get("title") or f"Observable extraction: \\texttt{{{escape(config.get('name'))}}}"
    author = escape(report.get("author") or "CUFLynx")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    source = config.get("source") or {}
    prep = config.get("preprocess") or {}
    stim = prep.get("stim_detect") or {}
    return "".join([
        f"\\title{{{title}}}\n\\author{{{author}}}\n",
        f"\\date{{Generated {escape(generated)}}}\n\\maketitle\n\n",
        "\\section*{Source}\n\\begin{tabular}{ll}\n\\toprule\n",
        f"Directory & \\texttt{{{escape(source.get('root'))}}} \\\\\n",
        f"CUFLynx & {escape(config.get('cuflynx_version') or 'unknown')} \\\\\n",
        f"Config version & {escape(config.get('obs_extraction_config_version'))} \\\\\n",
        f"Datasets used & {getattr(outcome, 'datasets_used', 0)} \\\\\n",
        f"Sweeps used & {getattr(outcome, 'sweeps_used', 0)} \\\\\n",
        f"Experiments & {getattr(outcome, 'n_experiments', 0)} \\\\\n",
        f"Data items & {getattr(outcome, 'n_data_items', 0)} \\\\\n",
        f"Clamp output rate & {escape(prep.get('clamp_output_hz'))} Hz \\\\\n",
        f"Stimulus threshold & {escape(stim.get('current_threshold'))} (current), "
        f"{escape(stim.get('voltage_threshold'))} (voltage) \\\\\n",
        "\\bottomrule\n\\end{tabular}\n\n",
    ])


def _binding_section(config: dict) -> str:
    """What each observable was actually bound to.

    Load-bearing rather than decorative: the same recording bound to a different
    variable is a different measurement, and this is the only place that choice
    is written down.
    """
    binding = config.get("model_binding") or {}
    mode = binding.get("clamp_mode_param") or {}
    rows = [
        ("Clamp mode switch", (mode.get("qname") if isinstance(mode, dict) else mode)),
        ("Voltage command", binding.get("voltage_command_param")),
        ("Current command", binding.get("current_command_param")),
        ("Measured voltage", binding.get("measured_voltage_variable")),
        ("Measured current", binding.get("measured_current_variable")),
    ]
    out = ["\\section*{Model binding}\n\\begin{tabular}{ll}\n\\toprule\n",
           "Role & Model variable \\\\\n\\midrule\n"]
    for label, qname in rows:
        out.append(f"{escape(label)} & \\texttt{{{escape(qname or '--')}}} \\\\\n")
    out.append("\\midrule\n")
    out.append(f"Current command scale & {escape(binding.get('current_command_scale'))} \\\\\n")
    out.append("\\bottomrule\n\\end{tabular}\n\n")
    return "".join(out)


def _modifier_section(config: dict) -> str:
    modifiers = config.get("data_modifiers") or []
    if not modifiers:
        return ("\\section*{Data modifiers}\nNone: the recorded channels were "
                "used as they were recorded.\n\n")
    out = ["\\section*{Data modifiers}\n",
           "Applied in this order, to the recorded channels, before any "
           "measurement.\n\n\\begin{tabular}{lll}\n\\toprule\n",
           "Name & Target & Expression \\\\\n\\midrule\n"]
    for mod in modifiers:
        out.append(f"{escape(mod.get('name'))} & {escape(mod.get('target'))} & "
                   f"\\texttt{{{escape(mod.get('modifier'))}}} \\\\\n")
    out.append("\\bottomrule\n\\end{tabular}\n\n")
    return "".join(out)


def _dataset_section(config: dict, role: str, thumbnails: dict) -> str:
    """One longtable of the recordings in a study role, with thumbnails."""
    rows = [d for d in (config.get("datasets") or [])
            if d.get("used") and _role_of(config, d) == role]
    heading = f"\\section*{{{role.capitalize()} datasets ({len(rows)})}}\n"
    if not rows:
        return heading + "None.\n\n"
    out = [heading,
           "\\begin{longtable}{p{0.48\\textwidth} p{0.44\\textwidth}}\n"]
    for i, dataset in enumerate(rows):
        group = _group_of(config, dataset)
        text = "\\raggedright " + " \\\\ ".join([
            f"\\textbf{{{escape(dataset.get('case_name'))}}}",
            f"Protocol: {escape(dataset.get('protocol'))} / "
            f"{escape(dataset.get('subprotocol'))}",
            f"Stimulus: {escape((group or {}).get('input') or 'current')}",
            f"Sweeps: {escape(dataset.get('sweep_limit') or (group or {}).get('sweep_limit') or 'all')}",
        ])
        thumb = thumbnails.get(dataset.get("case_name"))
        picture = (f"\\includegraphics[width=\\linewidth,keepaspectratio]"
                   f"{{{_tex_path(thumb)}}}" if thumb else "\\emph{no preview}")
        out.append(f"{text} & {picture} \\\\\n")
        if i < len(rows) - 1:
            out.append("\\midrule\n")
    out.append("\\end{longtable}\n\n")
    return "".join(out)


def _group_section(config: dict) -> str:
    groups = {k: g for k, g in (config.get("subprotocols") or {}).items()
              if g.get("used")}
    out = [f"\\section*{{Feature selection ({len(groups)} group(s))}}\n"]
    if not groups:
        out.append("None.\n\n")
        return "".join(out)
    for key, group in groups.items():
        protocol, subprotocol = split_group_key(key)
        timeline = timeline_for(group)
        window = group.get("plot_time_window") or {}
        out.append(f"\\subsection*{{\\texttt{{{escape(protocol)}}} / "
                   f"\\texttt{{{escape(subprotocol)}}}}}\n")
        out.append("\\begin{tabular}{ll}\n\\toprule\n")
        out.append(f"Study role & {escape(group.get('study_role'))} \\\\\n")
        out.append(f"Stimulus & {escape(group.get('input'))} \\\\\n")
        out.append(f"Sweeps per dataset & {escape(group.get('sweep_limit') or 'all')} \\\\\n")
        out.append(f"Pre-time & {escape(timeline.get('pre_time_s'))} s \\\\\n")
        out.append(f"Settle & {escape(timeline.get('settle_time_s') or 'none')} \\\\\n")
        out.append(f"Stimulus sub-experiment & {escape(timeline.get('stim_subexperiment_index'))} \\\\\n")
        if group.get("modulated_parameter"):
            out.append(f"Modulated parameter & \\texttt{{{escape(group['modulated_parameter'])}}} "
                       f"({escape(group.get('param_pre_value'))} $\\rightarrow$ "
                       f"{escape(group.get('param_stim_value'))}) \\\\\n")
        if window.get("time_start") is not None or window.get("time_end") is not None:
            out.append(f"Plot window & {escape(window.get('time_start'))} -- "
                       f"{escape(window.get('time_end'))} s \\\\\n")
        out.append("\\bottomrule\n\\end{tabular}\n\n")

        features = group.get("features") or []
        if not features:
            out.append("\\emph{No features configured.}\n\n")
            continue
        out.append("\\begin{tabular}{llll}\n\\toprule\n")
        out.append("Operation & Range & Unit & $\\sigma$ \\\\\n\\midrule\n")
        for feature in features:
            out.append(" & ".join([
                f"\\texttt{{{escape(feature.get('operation'))}}}",
                escape(_range_text(feature)),
                escape(feature.get("unit") or "--"),
                escape(_std_text(feature)),
            ]) + " \\\\\n")
        out.append("\\bottomrule\n\\end{tabular}\n\n")
    return "".join(out)


def _outcome_section(outcome) -> str:
    """Everything that did not make it in, and why.

    The section the CLI's report has no equivalent of. A reader asking "why are
    there only 40 items?" has no other way to find out, and the answer is
    usually mundane -- a sweep that never fired, a file that would not open.
    """
    skipped = list(getattr(outcome, "skipped", []) or [])
    warnings = list(getattr(outcome, "warnings", []) or [])
    notes = list(getattr(outcome, "notes", []) or [])
    out = ["\\section*{Extraction outcome}\n"]

    if warnings:
        out.append("\\subsection*{Warnings}\n\\begin{itemize}\n")
        out.extend(f"\\item {escape(w)}\n" for w in warnings)
        out.append("\\end{itemize}\n\n")
    if notes:
        out.append("\\subsection*{Notes}\n\\begin{itemize}\n")
        out.extend(f"\\item {escape(n)}\n" for n in notes)
        out.append("\\end{itemize}\n\n")

    out.append(f"\\subsection*{{Skipped ({len(skipped)})}}\n")
    if not skipped:
        out.append("Nothing was skipped.\n\n")
        return "".join(out)
    out.append("\\begin{longtable}{p{0.42\\textwidth} p{0.50\\textwidth}}\n"
               "\\toprule\nDataset & Reason \\\\\n\\midrule\n\\endhead\n")
    for entry in skipped:
        where = escape(entry.get("case_name"))
        if entry.get("sweep") is not None:
            where += f" (sweep {escape(entry['sweep'])})"
        out.append(f"{where} & {escape(entry.get('reason'))} \\\\\n")
    out.append("\\bottomrule\n\\end{longtable}\n\n")
    return "".join(out)


# ---------------------------------------------------------------------------
def _role_of(config: dict, dataset: dict) -> str:
    role = dataset.get("study_role") or (_group_of(config, dataset) or {}).get("study_role")
    return "validation" if str(role).lower().startswith("v") else "calibration"


def _group_of(config: dict, dataset: dict) -> dict | None:
    key = f"{dataset.get('protocol') or ''}|{dataset.get('subprotocol') or ''}"
    return (config.get("subprotocols") or {}).get(key)


def _range_text(feature: dict) -> str:
    rng = feature.get("range") or {}
    if rng.get("start_s") is not None or rng.get("end_s") is not None:
        return f"{rng.get('start_s')}--{rng.get('end_s')} s"
    kwargs = feature.get("operation_kwargs") or {}
    if "start_frac" in kwargs or "end_frac" in kwargs:
        return f"{kwargs.get('start_frac', 0.0)}--{kwargs.get('end_frac', 1.0)} (fraction)"
    return "whole window"


def _std_text(feature: dict) -> str:
    spec = feature.get("std")
    if spec is None:
        return "10% of value"
    if isinstance(spec, (int, float)):
        return str(spec)
    mode = spec.get("mode")
    if mode == "absolute":
        return f"{spec.get('value')} (absolute)"
    if mode == "fraction":
        return f"{spec.get('value')} x value"
    return str(mode)


def _tex_path(path: str) -> str:
    r"""A path LaTeX can read.

    Backslashes become forward slashes so a Windows path works in
    ``\includegraphics``, which treats a backslash as an escape.
    """
    return str(path).replace("\\", "/")
