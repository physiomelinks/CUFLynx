# Packaging notes

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
- **Bootloader rebuilt from source** on the Windows CI runner
  (`pip install --no-binary pyinstaller --force-reinstall`, in `release.yml`),
  so the bootloader is unique and not a signature match. This clears the bulk of
  the Defender detections.
- **Version resource embedded** (`version_info.txt` → `EXE(version=...)`), so the
  binary carries CompanyName/ProductName/version like real software.

These reduce, but do not guarantee elimination of, the detections — especially
across third-party AV vendors and on each new release (a new unsigned binary has
a new hash and no reputation).

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
