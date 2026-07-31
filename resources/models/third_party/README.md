# Third-party Myokit models

Example models from <https://myokit.org/examples>, kept here as test fixtures for
the `.mmt` import path (issue #27). Downloading them at test time would make the
suite depend on the network, so they are committed instead.

**These files are not covered by CUFLynx's licence.** The `LICENSE` at the root
of this repository applies to CUFLynx's own work. Each file here is the work of
its original authors, distributed on its own terms, and is included unmodified
with its original header intact. Nothing here is compiled into, linked against
or shipped inside CUFLynx — they are inputs to tests, aggregated alongside the
project rather than combined with it.

If you redistribute CUFLynx, these files carry their own obligations with them;
if that is inconvenient, delete this directory. The tests are parametrised over
whatever `.mmt` files are present, so removing them makes those cases disappear
rather than fail — with the exception of `resources/br-1977.mmt` and
`resources/hh-1952d.mmt`, which live outside this directory and are relied on by
name.

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

## Why they are here

They exercise the importer across a range no single model covers: 4 states
(Hodgkin-Huxley) to 145 (Heijman 2011), stimulus-current pacing (Beeler-Reuter)
versus voltage clamp (Hodgkin-Huxley), and models with and without declared time
units.

To refresh or extend the set:

    curl -sfL https://myokit.org/static/download/examples/<name>.mmt \
        -o resources/models/third_party/<name>.mmt

The tests pick up whatever is present; no test needs editing to cover a new one.
