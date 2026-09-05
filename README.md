# maciOS-testing-Darling

Investigating what it takes to run macOS-targeted software on an iPhone,
natively (ARM64, no VM, no CPU emulator), taking inspiration from
[Darling](https://github.com/darlinghq/darling)'s approach to macOS
compatibility on Linux.

**Start here:** [`AUDIT.md`](AUDIT.md) — architecture audit: what Darling's
approach does and doesn't carry over to iOS, and two mechanisms that make
native execution possible on this device without jailbreak. Then
[`MILESTONES.md`](MILESTONES.md) for the plan this repo follows.

## Current state (target device: iPhone 14, iOS 26.1, not jailbroken)

Two walls, two non-jailbreak fixes, both documented in `AUDIT.md` with the
prior art they're drawn from:

1. **Code signing / library validation** — a byte-patched Mach-O needs a
   real signing identity applied to the *whole* app bundle at install
   time (SideStore/AltStore do this), not just a hash-consistent ad-hoc
   signature. `tools/macho_patch.py` produces the patched binary; the
   bundle + install step is where the identity gets applied.
2. **No `posix_spawn`** — a sandboxed app can't spawn a new process, but
   it can ask iOS's own app-extension launch machinery to create one
   (LiveContainer's trick, adapted in `process-host/`).

That opens two paths, both 100% native ARM64:

- [`port/`](port/) — **M1**, path A: you have the source. Recompile for
  `arm64-apple-ios`, host in a minimal signed app, install via Xcode.
  See [`docs/xcode-setup.md`](docs/xcode-setup.md).
- [`process-host/`](process-host/) — **M2**, path B: you have a compiled
  binary, not necessarily its source. Patch it
  (`tools/macho_patch.py patch` + `dylibify`), bundle it, run it as a real
  separate process via this extension. See
  [`docs/process-host.md`](docs/process-host.md).

- [`AUDIT.md`](AUDIT.md) — the full technical audit.
- [`MILESTONES.md`](MILESTONES.md) — the milestone plan.
- [`tools/macho_patch.py`](tools/macho_patch.py), [`tests/`](tests/),
  [`fixtures/`](fixtures/), [`entitlements/`](entitlements/) — the Mach-O
  patcher both paths above are built on: platform-tag rewriting and
  `MH_EXECUTE` → `MH_DYLIB` conversion, both validated against a real
  cross-compiled binary (see `AUDIT.md` section 2 and
  `tests/test_macho_patch.py`).

Run the patcher's tests with:

```sh
python3 -m unittest discover -s tests
```

[`.github/workflows/build.yml`](.github/workflows/build.yml) builds
`port/` + `process-host/` on a `macos-latest` runner (project generated
from [`project.yml`](project.yml) via
[XcodeGen](https://github.com/yonaskolb/XcodeGen)) on every push: a fast
iOS Simulator compile check, then a real-device build producing an
**unsigned `.ipa`** uploaded as a workflow artifact — the correct input
for SideStore/AltStore to sign at install (they resign an unsigned IPA
themselves; see `AUDIT.md` section 2). Neither build confirms on-device
behavior; see `AUDIT.md`'s note at the end of section 4.

**[`docs/getting-logs.md`](docs/getting-logs.md)** — every channel that
actually exists for getting a real log back into this chat (CI, Xcode's
console, the app's own in-app log/Copy-Log button, Console.app/sysdiagnose
for a hard crash). Read this before running anything on-device.
