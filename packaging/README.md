# Packaging notes

## Onefile extraction: relocate off the system temp for multi-core analysis (issue #67)

The app ships as a PyInstaller **onefile** executable. A multi-core analysis is
launched as `mpiexec -n N <bundle> --_cuflynx-run-analysis ...`, so each of the N
ranks is a fresh copy of the exe that unpacks its own `_MEIxxxxxx` tree at startup.
By default that lands in the system `$TMPDIR`, so a full/small `$TMPDIR` turns into
an opaque rank crash *before any Python runs*
(`[PYI-...:ERROR] Could not create temporary directory!`).

**Why not `runtime_tmpdir` in the spec?** Two reasons, both verified against
PyInstaller 6.21:

1. On POSIX the bootloader does **not** expand `~` / `$VAR` in `runtime_tmpdir`
   (see its own `--runtime-tmpdir` help). The spec runs on the *build* machine, so
   any absolute path baked there would point at the CI runner's home, not the
   user's.
2. It runs at build time and can't compute a per-user path.

**What we do instead (in `runtime_paths.runner_launch_env()`):** when the frozen
app re-invokes *itself* as the analysis runner (no external interpreter chosen), it
sets **`TMPDIR` / `TMP` / `TEMP` to a roomy per-build user-cache dir** in the
rank environment, relocating every rank's extraction off the volatile system temp
onto a predictable location that won't run out of space.

**Cache dir + version keying.** The dir is
`<user-cache>/CUFLynx/onefile-cache/<key>`, where `<user-cache>` is the OS
convention (`$XDG_CACHE_HOME`/`~/.cache`, `~/Library/Caches`,
`%LOCALAPPDATA%`) and `<key>` is a short hash of the executable's identity (path +
size + mtime). A rebuilt/redownloaded exe gets a **new** key, so different builds
don't pile transient extractions into one directory. Old version dirs are *not*
auto-pruned; they can be cleared by hand or on OS cache cleanup.

**Scope: this relocates, it does not de-duplicate.** Each rank still extracts its
own copy (N x one uncompressed bundle live during a run), just under the cache dir
instead of `$TMPDIR`. An earlier revision also tried to make the ranks *share* one
extraction via PyInstaller's `_MEIPASS2` "already unpacked here" signal — but that
was tested against a real onefile build and **does not work on PyInstaller 6.x**
(the shipped toolchain):

- `strings` on the 6.21 bootloader shows **no `_MEIPASS2`**; the parent→child
  reuse was rewritten as an internal `_PYI_APPLICATION_HOME_DIR` /
  `_PYI_PARENT_PROCESS_LEVEL` protocol.
- Pointing independent `mpiexec` ranks at a parent's live extraction via those
  vars (levels 0/1/2) still produced N distinct `_MEIxxxxxx` dirs — env-triggered
  cross-rank sharing isn't available. Real de-duplication would need a different
  approach (e.g. a **onedir** analysis build, so there's nothing to extract per
  rank), tracked as a possible follow-up on issue #67.

**Validation.** The path/key helpers + env construction are unit-tested in
`apps/api/tests/test_runtime_paths.py`. The frozen-only behaviour was checked with
a minimal onefile probe built by the same PyInstaller 6.21 (the `_MEIPASS2`
findings above). A CI frozen build per OS should still confirm the end-to-end:

- `<CUFLynx> --_cuflynx-run-analysis /nonexistent/runner.py /nonexistent/cfg.json`
  extracts into `<user-cache>/CUFLynx/onefile-cache/<key>`, not `$TMPDIR`.
- `mpiexec -n 2 <CUFLynx> --_cuflynx-run-analysis ...` extracts both ranks under
  that cache dir (off `$TMPDIR`), confirming Hydra forwards `TMPDIR` to the ranks.

## Windows antivirus false positives

The Windows executable is a PyInstaller **onefile** build, and Windows Defender
(and some third-party AV) periodically flag it as a trojan. This is a **false
positive** inherent to PyInstaller, not a real detection. Root causes, in order
of impact:

1. **Unsigned binary.** An executable from an unknown publisher gets no trust
   from Defender/SmartScreen. This is the biggest factor.
2. **Stock PyInstaller bootloader.** The bootloader shipped in the PyInstaller
   wheel is a fixed binary whose bytes are in AV signature databases — malware
   built with PyInstaller shares it, so the signature matches ours too.
3. **Bare metadata.** An exe with no CompanyName/ProductName/version resource
   looks more like a dropper.
4. Onefile self-extraction to a temp dir + spawning is a behavioural heuristic
   trigger.

### What we do about it (free mitigations, in this repo)

- **UPX is disabled** (`upx=False` in `cuflynx.spec`) — packing makes FPs worse.
- **Version resource embedded** (`version_info.txt` → `EXE(version=...)`), so the
  binary carries CompanyName/ProductName/version like real software instead of
  being a bare metadata-less exe. Verified embedded in the built artifact.

These reduce, but do not guarantee elimination of, the detections — especially
across third-party AV vendors and on each new release (a new unsigned binary has
a new hash and no reputation).

### Bootloader-rebuild: attempted, does not work in CI (deferred)

The higher-impact free lever is rebuilding the PyInstaller bootloader from source
(the stock bootloader's bytes are the main signature match). This was tried in
`release.yml` and **removed because it did not fire on the Windows runner**:

- PyInstaller's *sdist* ships a prebuilt Windows bootloader
  (`PyInstaller/bootloader/Windows-64bit-intel/run.exe`), and the build reuses it.
- `hatch_build.py` only compiles when no prebuilt exists *or*
  `PYINSTALLER_COMPILE_BOOTLOADER=1` is set. Setting that env var forced a real
  recompile **locally** (verified: 25 source files compiled), but in CI on Windows
  it still didn't — the wheel build finished in ~0.07 s with zero compiler output,
  for a reason not yet diagnosed (no Windows box to reproduce on).

To make this work, a follow-up should compile the bootloader explicitly and
visibly (clone the source, set up the MSVC env, run `python waf all`, then
install), and validate on a real Windows machine that (a) the bootloader binary
actually changed and (b) Defender's verdict improved. Tracked separately.

### If a release is still flagged

Submit a false-positive report to Microsoft (free, Defender-only, takes hours to
days):

1. Go to <https://www.microsoft.com/en-us/wdsi/filesubmission>.
2. Sign in, choose **Software developer**, submit the flagged
   `CUFLynx-windows-x86_64.exe`, and mark it as an expected false positive.
3. Once Microsoft reclassifies the hash, Defender stops flagging **that build**.
   A new release has a new hash and may need re-submitting until the binary is
   signed.

### The durable fix: code signing

The only reliable, cross-vendor, cross-release fix is **Authenticode signing**.
Best current value is **Azure Trusted Signing** (~$10/month, cloud-based, no
hardware token, CI-friendly, Microsoft-trusted). Requires identity verification.
Once a cert is available, add a `signtool sign` step to the Windows build after
`Build executable`. Not yet set up — tracked separately.

## macOS

Not notarized (`codesign_identity=None`). A downloaded build is Gatekeeper-
quarantined; users right-click → Open or `xattr -d com.apple.quarantine`. See the
main README. Deployment target is pinned via `MACOSX_DEPLOYMENT_TARGET=11.0` in
`release.yml`.
