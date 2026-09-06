# Milestones

Context: no jailbreak. `AUDIT.md` documents two mechanisms that make native
ARM64 execution on this iPhone possible anyway: a real developer signing
identity applied to the whole app bundle — by Xcode at build time for both
milestones below, no other tool required (section 2) — and a declared app
extension standing in for `posix_spawn` (section 3, `process-host/`). Every
milestone below builds on one or both. Native ARM64 throughout — no VM, no
CPU emulator, no instruction translation.

## M1 — Trivial CLI, source-ported (in progress)

**Path A from `AUDIT.md` section 4: you have the source.**

**Goal:** prove the whole pipeline end to end with the smallest possible
payload, before spending any effort on a real tool's dependencies.

- [x] `port/CLICore/clicore.{h,c}` — a tiny, framework-free C function
      (`clicore_run()`) standing in for "the CLI's logic." Validated in
      this sandbox: compiles cleanly to an arm64 object file under
      `-target arm64-apple-ios15.0` with zero warnings (`-Wall -Wextra`),
      and the identical source compiles for `arm64-apple-macos11` too —
      the two object files differ only in the platform load command.
- [x] `port/MaciOSPortApp/` — minimal SwiftUI app (`MaciOSPortApp.swift`,
      `ContentView.swift`, bridging header) whose entire UI is "call
      `clicore_run()`, show the result." This is the smallest shell iOS
      will actually install and launch — there is no smaller unit than
      "one view."
- [ ] **On your Mac**: create the Xcode project, drop these files in,
      build, sign with your Apple ID, run on your iPhone 14 over USB.
      Step-by-step in `docs/xcode-setup.md`. This step needs Xcode and
      your physical device, so it's the one part of M1 you complete
      yourself — I have no Mac or iPhone access from this sandbox.

**Definition of done:** the app launches on your iPhone and displays the
string produced by `clicore_run()`.

## M2 — An already-compiled binary, patched and run via `process-host/`

**Path B from `AUDIT.md` section 4: you don't need the source.**

**Goal:** prove the pipeline that doesn't require recompiling anything —
patch an existing `arm64-apple-macos` binary, convert it to a loadable
dylib, bundle it, and run it as a real separate process, all without
touching its source.

- [x] `tools/macho_patch.py dylibify` — converts `MH_EXECUTE` → `MH_DYLIB`
      in place (flip filetype, neutralize `__PAGEZERO`, repurpose
      `LC_LOAD_DYLINKER`'s space for a new `LC_ID_DYLIB`). Validated
      against a real cross-compiled Mach-O: file size unchanged, the
      converted file's own ad-hoc signature re-verifies correctly (see
      `tests/test_macho_patch.py`).
- [x] `process-host/` — a generic "run this dylib's entry point in a real,
      separate OS process" app extension (`Info.plist`, `main.m`,
      entitlements). See `docs/process-host.md` for the full mechanism
      (the `com.apple.ar.viewer` / `_MultipleInstances` / `_ProcessType`
      trick from LiveContainer) and the payload contract
      (`int name(void)`, invoked via `dlopen`/`dlsym`).
- [ ] **On your Mac**: pick a real small, portable (no AppKit/Cocoa,
      libSystem/Foundation/CoreFoundation-level dependencies only) macOS
      CLI binary — or reuse `fixtures/hello.s`'s pattern, adapted to
      export a named `int name(void)` entry point instead of calling
      `exit()` directly, as the concrete first test subject. Patch it
      (`patch --platform ios`, then `dylibify --no-resign`), add it to the
      Xcode project as a "Copy Files" build phase input targeting
      `Frameworks/` so Xcode signs it along with everything else when you
      build, add `process-host/` as an extension target, run from Xcode,
      and invoke it per `docs/process-host.md`. (No SideStore needed here
      — that only matters if the payload were being fetched/patched after
      the app is already installed, which this milestone doesn't do.)
- [ ] Expect to spend most of the effort on dependency resolution once a
      real tool is the target: any macOS-only framework the binary links
      against needs either a redirect to iOS's real equivalent (if one
      exists, just under a different path — `Foundation`, `CoreFoundation`,
      `CoreGraphics`, `Security` are the ones confirmed to have iOS
      equivalents in the prior art this milestone draws on) or a stub
      implementation (if no iOS equivalent exists at all — confirmed
      necessary for things like `DiskArbitration`, `ServiceManagement` in
      that same prior art). `tools/macho_patch.py` doesn't automate
      dependency redirection yet; that's the next thing to add to it once
      a real target names which dependencies actually matter.

**Definition of done:** `process-host/` reports back a real PID (different
from the host app's own) and the correct `exitCode` from the patched
binary's entry point.

## M3 — A real small CLI tool, either path

Once M1 and M2 are both confirmed working on-device, pick a specific real
tool and use whichever path fits (source available → path A; binary only →
path B). Same "portable" constraint either way: no AppKit/Cocoa, no
`fork`/`exec`/multiprocessing (the sandbox blocks spawning child processes
regardless of which launch mechanism got you running in the first place),
and any hardcoded macOS filesystem paths (`/tmp`, `/usr/...`) redirected to
the app's own sandbox container.

## M4 — GUI application (flagged, likely out of scope as "minimum")

A macOS GUI app's *logic* can be ported the same way as M3, but its *UI
layer* cannot: there is no AppKit on iOS, and reimplementing enough of
AppKit's behavior on top of UIKit to run an unmodified AppKit UI is exactly
the scale of effort Darling put into Cocotron for Linux — a multi-year,
large-surface-area project, not a "minimum milestone." The realistic scope
for a GUI port is: keep the app's non-UI logic as ported in M3, and
**rewrite** its UI layer natively in SwiftUI/UIKit. LiveContainer's
`_UISceneHostingController`-based scene hosting (used for its "nativeWindow"
multitasking) is the closest known prior art for compositing a *separately
processed* GUI surface into a host window, if that's ever worth revisiting —
not planned in the abstract here.

## Explicitly not planned

- Any approach requiring jailbreak (AMFI bypass without a real signing
  identity) — ruled out by the user's constraint, and no longer needed for
  the things jailbreak used to seem necessary for: `AUDIT.md` sections 2–3
  cover the non-jailbreak fixes for both the signing-trust-chain wall and
  the `posix_spawn` wall.
- x86_64 translation/emulation of any kind — out of scope by the user's
  explicit "preserve native ARM64 execution" requirement, and irrelevant
  anyway since every path here only ever produces or loads native arm64
  code.
