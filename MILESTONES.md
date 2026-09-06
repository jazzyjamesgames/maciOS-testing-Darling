# Milestones

Context: no jailbreak. `AUDIT.md` documents two mechanisms that make native
ARM64 execution on this iPhone possible anyway: a real developer signing
identity applied to the whole app bundle — by Xcode at build time for both
milestones below, no other tool required (section 2) — and a declared app
extension standing in for `posix_spawn` (section 3, `process-host/`). Every
milestone below builds on one or both. Native ARM64 throughout — no VM, no
CPU emulator, no instruction translation.

## M1 — Trivial CLI, source-ported — ✅ DONE, confirmed on-device

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
- [x] **On-device, confirmed 2026-09-06**: built via CI (`.github/workflows/build.yml`),
      installed via SideStore on the user's iPhone 14 (iOS 26.1, not
      jailbroken). In-app log, verbatim:
      ```
      [19:53:23.366] app launched
      [19:53:23.368] clicore_run() -> hello from native arm64 maciOS (ported)
      ```
      Native ARM64 C code, compiled for `arm64-apple-ios`, executing inside
      a normally-installed, normally-signed iOS app on real hardware — no
      VM, no CPU emulator, no jailbreak. This is the answer the whole audit
      was chasing.

**Definition of done:** ✅ the app launches on the iPhone and displays the
string produced by `clicore_run()`.

## M2 — An already-compiled binary, patched and run via `process-host/` — ✅ DONE, confirmed on-device

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
- [x] `test-payload/TestPayload.c` + `project.yml`'s `TestPayload` target —
      a real, Xcode-built native payload (one function, `maciOS_test_entry`,
      returns `42`), embedded alongside `process-host/` in `MaciOSPortApp`.
      Exists to test the extension mechanism itself first, decoupled from
      whether some specific foreign binary's dependencies resolve on iOS.
- [x] `port/MaciOSPortApp/ProcessHostTester.h`/`.m` + a "Test ProcessHost"
      button in `ContentView.swift` — invokes `process-host/` for real via
      the same undocumented `NSExtension` API `ios18-probe` proved works,
      reading the extension's *installed* (SideStore-rewritten)
      `CFBundleIdentifier` back rather than hardcoding it. Deliberately
      scoped to just the process-spawn question (a real, different PID) —
      see `docs/process-host.md` for why the exit-code reply path is
      treated as unverified rather than assumed.
- [x] **On-device, confirmed 2026-09-06**: tapped "Test ProcessHost" on
      the same iPhone 14 (iOS 26.1, not jailbroken) as M1. In-app log,
      verbatim:
      ```
      [23:37:41.822] app launched
      [23:37:41.824] clicore_run() -> hello from native arm64 maciOS (ported)
      [23:37:44.364] Test ProcessHost tapped: my pid is 1867
      [23:37:44.464] ProcessHost LAUNCHED, reported pid=1869
      ```
      **pid 1867 (host app) → pid 1869 (ProcessHost): a real, different
      process.** The `com.apple.ar.viewer`/`_MultipleInstances`/
      `_ProcessType:"App"` extension trick genuinely produces a separate
      OS process on real, non-jailbroken hardware, without ever calling
      `posix_spawn`. This is the load-bearing claim behind all of M2 and
      every later milestone that needs real process isolation — confirmed,
      not just reasoned about from prior art.
- [x] `fixtures/macos_payload.c` + `test-payload/build-and-patch.sh` +
      `tools/macho_patch.py symbols` — the actual end-to-end pipeline
      (compile for macOS, never iOS → `patch --platform ios` → `dylibify`)
      run against a real binary, not a shortcut. Structurally validated in
      this sandbox: the exported entry point (`maciOS_patched_payload_entry`)
      survives with external linkage intact (`tests/test_macho_patch.py`'s
      `TestFullPipelineAgainstRealPayload`). Also surfaced a real, general
      finding: the `dylibify` install-name slot is always tiny (~7-8 bytes)
      for *any* normally-linked executable, not just this project's
      fixtures — `LC_LOAD_DYLINKER` always names `/usr/lib/dyld`, and
      that's the space being repurposed. `docs/process-host.md`'s example
      is corrected accordingly.
- [x] `project.yml` embeds the result (`test-payload/PatchedMacOSPayload.dylib`)
      via a Copy Files build phase in `MaciOSPortApp` (Xcode's own signing
      covers it, same as `ProcessHost`/`TestPayload` — no SideStore
      needed); `ProcessHostTester` is generalized to take a payload
      path/entry-point pair, and `ContentView.swift` now has a second
      button, "Test ProcessHost (patched macOS binary)", alongside the
      already-confirmed native control.
- [x] **On-device, confirmed 2026-09-06** (same session as M1, same iPhone
      14, iOS 26.1, not jailbroken). In-app log, verbatim:
      ```
      [09:39:37.969] app launched
      [09:39:37.971] clicore_run() -> hello from native arm64 maciOS (ported)
      [09:39:40.497] Test ProcessHost (patched) tapped: my pid is 2547
      [09:39:40.635] ProcessHost (patched) LAUNCHED, reported pid=2548
      [09:39:43.129] Test ProcessHost (native) tapped: my pid is 2547
      [09:39:43.191] ProcessHost (native) LAUNCHED, reported pid=2549
      ```
      **pid 2547 (host app) → pid 2548: a real, separate process, running
      a binary that was compiled for `arm64-apple-macos` and never
      recompiled for iOS at any point** — only platform-tag-patched and
      dylibified by `tools/macho_patch.py`, exactly as `fixtures/macos_payload.c`
      → `test-payload/build-and-patch.sh` → `process-host/` describes.
      This is the actual, un-shortcut "maciOS" claim, not just the
      mechanism in isolation: native ARM64 code that never targeted iOS,
      running on a real non-jailbroken iPhone, no VM, no emulator. Both
      process-host payloads (native control and patched macOS binary) got
      distinct real PIDs in the same run, confirming the mechanism is
      consistent across different loaded content, not a one-off.
- [ ] Follow-up, not blocking: confirm the exit-code/error reply path
      (`process-host/main.m`'s `completeRequestReturningItems:`) is
      actually observable by the caller — still unverified per
      `docs/process-host.md`. Both payloads return a distinctive value
      (`42` native, `99` patched) specifically so this is easy to check
      once it's worth wiring in. If the reply channel doesn't pan out,
      whatever `ios18-probe` fell back to (Darwin notifications, since
      App Group files didn't survive SideStore's resign) is next to try.
      **Tried and ruled out, 2026-09-06** — introspecting `NSExtension`
      and `NSExtensionContext` (`port/MaciOSPortApp/NSExtensionIntrospection.h`/`.m`)
      found a real lead beyond the dead-end `completeRequestReturningItems:`
      path (`NSExtension`'s dump showed no accessor exposes that payload
      at all, but surfaced `beginExtensionRequestWithInputItems:
      listenerEndpoint:completion:`; `NSExtensionContext`'s dump showed the
      matching private slot, `_auxiliaryListener`/`_auxiliaryConnection`).
      Wired in for real (an `NSXPCListener` passed as that endpoint,
      retrieved via `_auxiliaryListener` on the extension side) and tested
      on-device — **the listener-endpoint call itself was rejected
      outright for both payloads** (a fast, clean nil request identifier,
      not a hang or crash): this `com.apple.ar.viewer`-based extension
      point doesn't accept a caller-supplied listener endpoint this way.
      Reverted rather than kept as known-broken code; see
      `docs/process-host.md`'s reply-channel section for the full record
      and what's untried (an `options:`-dictionary variant of the same
      call; Darwin notifications, per `ios18-probe`'s own fallback).
- [ ] Once a real (non-trivial) macOS binary is the target, expect to
      spend most of the effort on dependency resolution: any macOS-only
      framework the binary links
      against needs either a redirect to iOS's real equivalent (if one
      exists, just under a different path — `Foundation`, `CoreFoundation`,
      `CoreGraphics`, `Security` are the ones confirmed to have iOS
      equivalents in the prior art this milestone draws on) or a stub
      implementation (if no iOS equivalent exists at all — confirmed
      necessary for things like `DiskArbitration`, `ServiceManagement` in
      that same prior art). `tools/macho_patch.py` doesn't automate
      dependency redirection yet; that's the next thing to add to it once
      a real target names which dependencies actually matter.

**Definition of done:** ✅ `process-host/` reports back a real PID (different
from the host app's own), for a binary that was patched, not recompiled.
The exit-code/error reply path is a follow-up, not required for this
milestone's core claim (process isolation without `posix_spawn`, for
genuinely foreign macOS-compiled code) to be considered proven.

## M3 — A real small CLI tool, either path

Both M1 and M2 are confirmed working on-device — pick a specific real
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
