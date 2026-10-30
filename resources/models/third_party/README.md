# Third-party models

Example models from <https://myokit.org/examples>, kept here as test fixtures for
the `.mmt` import path (issue #27), plus EasyML `.model` files exported from some
of them for the `.model` import path. Downloading them at test time would make
the suite depend on the network, so they are committed instead.

**These files are not covered by CUFLynx's licence.** The `LICENSE` at the root
of this repository applies to CUFLynx's own work. Each `.mmt` here is the work of
its original authors, distributed on its own terms, and is included unmodified
with its original header intact. The `.model` files are **derived** from those
`.mmt` files — see [Derived EasyML exports](#derived-easyml-exports) — so they
are the same authors' work in another notation, carrying the same terms, and are
*not* unmodified copies. Nothing here is compiled into, linked against or
shipped inside CUFLynx — they are inputs to tests, aggregated alongside the
project rather than combined with it.

If you redistribute CUFLynx, these files carry their own obligations with them;
if that is inconvenient, delete this directory. The tests are parametrised over
whatever `.mmt` and `.model` files are present, so removing them makes those
cases disappear rather than fail — with the exception of `resources/br-1977.mmt`
and `resources/hh-1952d.mmt`, which live outside this directory and are relied on
by name. `resources/hodgkin_huxley_1952.model` is **not** from here: it is
CUFLynx's own work under CUFLynx's own licence.

## Provenance

Encodings are by the Myokit authors (Michael Clerx and contributors); Myokit
itself is BSD-3-Clause. The models they encode are published work by the authors
named below.

Several files carry a **GNU GPL** notice inherited from the original Rudy lab
source they were transcribed from. They are marked below and left byte-for-byte
as published.

| File | Model | Notice in file |
|---|---|---|
| `br-1977.mmt`, `br-1977-step-protocol.mmt` | Beeler & Reuter 1977 | none |
| `clancy-1999-stochastic-simulation.mmt` | Clancy & Rudy 1999 | none |
| `decker-2009.mmt`, `decker-2009-simulation-times.mmt` | Decker et al. 2009 (HRd2009) | none — but derived from rudylab.wustl.edu code |
| `dn-1985-if-gna.mmt`, `dn-1985-iplot.mmt` | DiFrancesco & Noble 1985 | none |
| `fink-2009-protocol.mmt` | Fink et al. 2009 | none |
| `heijman-2011.mmt` | Heijman et al. 2011 | **GNU GPL** |
| `hh-1952d.mmt`, `hh-1952d-modern.mmt` | Hodgkin & Huxley 1952 | none |
| `lr-1991*.mmt` (5 files) | Luo & Rudy 1991 | **GNU GPL** (Livshitz & Rudy) |
| `ord-2011.mmt`, `ord-2011-apd.mmt` | O'Hara et al. 2011 | **GNU GPL** |
| `sampson-2010.mmt` | Sampson et al. 2010 | none |
| `stewart-2009*.mmt` (3 files) | Stewart et al. 2009 | none |
| `tran-2009.mmt` | Tran et al. 2009 | none |

"None" means the file states no licence, **not** that it is unencumbered:
absent an explicit grant, ordinary copyright applies. It is recorded here as a
fact about the file, not as a clearance.

## Derived EasyML exports

EasyML is openCARP's ionic-model language. openCARP's own model library cannot be
kept here — it is under the openCARP Academic Public License, which is
non-commercial and not OSI-approved — so the `.model` fixtures are exported from
the `.mmt` files above with Myokit's own `EasyMLExporter`.

A derived file is a stronger claim on the original than an unmodified copy, so
**none is taken from a source with a licence notice.** Every `.mmt` marked
**GNU GPL** in the table above (Heijman 2011, Luo-Rudy 1991, O'Hara 2011) is
deliberately excluded, and a test enforces that rather than trusting this
paragraph.

One file per distinct *model*, not per source: EasyML carries no `[[protocol]]`
or `[[script]]` section, so sources differing only in those export byte for byte
identically.

| File | Exported from | States |
|---|---|---|
| `br-1977.model` | `br-1977.mmt` (identical from `br-1977-step-protocol.mmt`) | 8 |
| `decker-2009.model` | `decker-2009.mmt` (identical from `decker-2009-simulation-times.mmt`) | 46, two Markov models |
| `dn-1985.model` | `dn-1985-iplot.mmt` (identical from `dn-1985-if-gna.mmt`) | 16 |
| `hh-1952d-modern.model` | `hh-1952d-modern.mmt` | 4 |
| `sampson-2010.model` | `sampson-2010.mmt` | 78 |
| `stewart-2009.model` | `stewart-2009.mmt` (identical from the two `stewart-2009-cable*.mmt`) | 20 |
| `tran-2009.model` | `tran-2009.mmt` | 4 |

Not every `.mmt` above has one. `clancy-1999-stochastic-simulation`,
`fink-2009-protocol` and `hh-1952d` cannot be exported at all — Myokit's EasyML
exporter raises on each — which is a fact about that exporter, not about the
import path being tested here.

To regenerate one after a Myokit upgrade:

    python -c "import myokit; from myokit.formats.easyml import EasyMLExporter; \
        EasyMLExporter().model('<name>.model', myokit.load_model('<source>.mmt'))"

`test_easyml_fixtures.py` checks each committed `.model` still matches a fresh
export of its source, so a stale one fails rather than drifting quietly.

Expect the regenerated file to **differ textually from the committed one even
when nothing has changed**: Myokit's exporter is not order-deterministic, because
`guess.membrane_currents()` hands the currents back in varying order. The terms
of `Iion` and the members of the `.trace()` group get shuffled between runs.
Addition is commutative and a group is a set, so those are the same model — which
is why the test compares states, initial values, parameters and methods rather
than bytes.

## Why they are here

They exercise the importer across a range no single model covers: 4 states
(Hodgkin-Huxley) to 145 (Heijman 2011), stimulus-current pacing (Beeler-Reuter)
versus voltage clamp (Hodgkin-Huxley), and models with and without declared time
units.

To refresh or extend the set:

    curl -sfL https://myokit.org/static/download/examples/<name>.mmt \
        -o resources/models/third_party/<name>.mmt

The tests pick up whatever is present; no test needs editing to cover a new one.
