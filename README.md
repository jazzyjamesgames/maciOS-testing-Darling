# maciOS-testing-Darling

Investigating what it takes to run macOS-targeted software on an iPhone,
natively (ARM64, no VM, no CPU emulator), taking inspiration from
[Darling](https://github.com/darlinghq/darling)'s approach to macOS
compatibility on Linux.

**Start here:** [`AUDIT.md`](AUDIT.md) — architecture audit, what Darling's
approach does and doesn't carry over to iOS, and why the initial
"patch the Mach-O and run it" idea doesn't work without a jailbreak.
Then [`MILESTONES.md`](MILESTONES.md) for the plan this repo actually
follows given that constraint.

## Current state (target device: iPhone 14, iOS 26.1, not jailbroken)

Direct execution of an unmodified macOS binary is not possible on this
device — iOS enforces code-signature trust chains and app-bundle launch
requirements in-kernel, with no jailbreak available to bypass them. The
viable path is a **source-level port**: recompile the target code for
`arm64-apple-ios` (still native ARM64, nothing emulated), host it in a
minimal signed iOS app, and run it via Xcode.

- [`AUDIT.md`](AUDIT.md) — the full technical audit.
- [`MILESTONES.md`](MILESTONES.md) — the milestone plan.
- [`port/`](port/) — M1: a trivial CLI, ported and ready to build.
- [`docs/xcode-setup.md`](docs/xcode-setup.md) — the steps to build, sign,
  and run M1 on your iPhone (requires a Mac with Xcode).
- [`tools/macho_patch.py`](tools/macho_patch.py), [`tests/`](tests/),
  [`fixtures/`](fixtures/), [`entitlements/`](entitlements/) — a validated
  Mach-O platform-tag patcher built while testing the (rejected)
  direct-execution approach. Kept as reference for why hash-consistent
  ad-hoc patching still isn't enough without a trusted signing chain; see
  `AUDIT.md` section 2.

Run the patcher's tests with:

```sh
python3 -m unittest discover -s tests
```
