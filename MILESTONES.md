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
      `docs/process-host.md`'s reply-channel section for the full record.
      **Third attempt, also ruled out on-device (2026-09-06)**: Darwin
      notifications (`CFNotificationCenterGetDarwinNotifyCenter()`) --
      real, public, documented API, unlike the two private-API attempts
      above. The exit code/error was encoded directly into the
      notification's own name (no `userInfo` payload on the Darwin
      center), scoped by a host-chosen `requestUUID`. This depended on
      registering with a `NULL` name ("observe everything," since the
      exact name — containing the exit code — can't be known in advance)
      -- **confirmed not to work**: a dedicated sniffer
      (`port/MaciOSPortApp/DarwinNotificationSniffer.h`/`.m`) logged zero
      notifications over 27 seconds spanning two real ProcessHost
      launches, ruling out both "the extension didn't post" and "a name
      mismatch" (either would still have let unrelated system
      notifications through) — the wildcard registration itself doesn't
      work on the Darwin center on this device. Three mechanisms ruled out
      now; see `docs/process-host.md` for the fixed-name-space redesign
      that would still work, not yet built pending a decision on whether
      it's worth the complexity (nothing in M1/M2 needs it).
- [x] Dependency-resolution tooling built ahead of a specific M3 target
      (see `docs/dependency-resolution.md`): `tools/macho_patch.py deps`
      lists a binary's `LC_LOAD_DYLIB`-family dependencies;
      `check-deps` classifies each as available/unavailable on iOS —
      verified against a real SDK when one is reachable
      (`--auto-detect-sdk`, exercised on every push in
      `.github/workflows/build.yml` against the real macOS CI runner) or a
      best-effort seed table otherwise — and for each unavailable one,
      lists the *exact* undefined symbols this binary needs from it (via
      `LC_SYMTAB`'s two-level-namespace library ordinals, not a guess at
      the whole framework's surface); `redirect-deps` rewrites a
      dependency's path in place (same in-place-size constraint as
      `dylibify`'s install name). Not yet exercised against a real,
      non-trivial target — no specific tool has been picked for M3 yet —
      but the tool itself is tested (22 unit tests, `tests/test_macho_patch.py`).
      **First real CI run (2026-09-06) found a genuine gap and failed the
      build**: `/usr/lib/libSystem.B.dylib` doesn't exist at that literal
      path in a real iPhoneOS26.5 SDK at all. The first fix attempt
      (scanning every `.tbd` for a matching declared install name) turned
      out to be a second wrong guess — a follow-up CI diagnostic step
      showed the umbrella library has **no discoverable `.tbd` anywhere**
      in the SDK, under any name; only its individual sub-libraries do.
      `classify_dependency` now special-cases it as always-available by
      construction instead (`ld64` requires every dynamic binary to link
      it, confirmed independently by this project's own `-lSystem`
      finding) — see `docs/dependency-resolution.md`. **Confirmed
      2026-09-06 (CI run 19): `libSystem.B.dylib` now reports
      `available`** — `--allow-unavailable` removed, the step gates the
      build for real.
- [ ] Once a real (non-trivial) macOS binary is the target, run the above
      against it: `check-deps` will name which frameworks need a redirect
      (an iOS equivalent under a different path — `OpenGL` → `OpenGLES` is
      the known example) versus a hand-written stub (no iOS equivalent at
      all — `DiskArbitration`, `ServiceManagement`, `AppKit` are confirmed
      examples in the prior art this milestone draws on). Stub *generation*
      from a symbol list is still a manual step — see
      `docs/dependency-resolution.md`'s closing section for why automating
      that further isn't worth doing blind, before a real target's own
      list is in hand.

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

**Target picked: SQLite** — literally what macOS itself ships as
`/usr/bin/sqlite3` and `/usr/lib/libsqlite3.dylib`, genuinely non-trivial
(a full SQL engine, not a toy), and a clean fit for path A: its official
amalgamation (`third_party/sqlite/`, public domain, vendored unmodified —
see the README there) needs nothing beyond the C standard library and
pthreads by default, all present on iOS, so this target doesn't exercise
`tools/macho_patch.py`'s dependency-redirection work at all. That's
expected, not a gap in the choice — a future target with real
macOS-framework dependencies is what that tooling is for; this milestone
is about proving a real, substantial piece of macOS software runs, first.

- [x] `port/SQLiteCLI/sqlite_cli.h`/`.c` — drives `third_party/sqlite/sqlite3.c`'s
      public C API directly (not `shell.c`'s interactive REPL, which
      assumes a real terminal that doesn't exist in a sandboxed iOS app —
      see the README): opens an in-memory database (`:memory:`, sidestepping
      sandbox-path questions for this first win), creates a table, inserts
      three rows, runs an aggregate query, and returns a human-readable
      summary string plus a status code — the same `int name(void)`-shaped
      contract `process-host/` already uses for M2 payloads.
- [x] `project.yml`: `third_party/sqlite/sqlite3.c`/`.h` and
      `port/SQLiteCLI/` added directly to `MaciOSPortApp`'s sources (path
      A — compiled straight into the app, same as `port/CLICore`, no
      separate process/framework needed). A "Run SQLite3 (M3)" button in
      `ContentView.swift` logs the result.
- [ ] **Not yet confirmed on-device.** Couldn't be locally smoke-tested in
      this sandbox at all (unlike `port/CLICore`'s trivial stub): `clang
      -target arm64-apple-ios15.0 -c` on `sqlite3.c` hits a glibc-internal
      header conflict (`bits/libc-header-start.h`) with no real Darwin SDK
      present, since SQLite genuinely needs the C standard library, unlike
      a `-nostdlib` fixture. The real macOS CI runner (with the actual
      iOS SDK) is the first real compile check this gets.

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

## Custom loader track — Milestone 1 (separate from M1-M4 above)

A different question from M1-M3's own path A/B: not "can the OS's normal
load path (Xcode/dyld, or the extension-launch trick) run this," but "can
*our own code*, not dyld, be the thing that maps and executes a Mach-O at
all" — groundwork for eventually intercepting/redirecting syscalls at
load time, distinct from `process-host/`'s approach (which runs unmodified
code as-is via the OS's own extension-launch mechanism, no custom loading
involved). See `docs/custom-loader.md` for the full report and
`docs/syscall-table-diff.md` for the syscall ABI groundwork.

- [x] Target binary: reused `fixtures/hello.s` (already proven by M1/M2's
      own tests) rather than a new near-duplicate — no libSystem, direct
      `svc #0x80` syscalls only. `loader/build_target.sh` builds it (needs
      `-lSystem` on real `ld64`, same finding as
      `test-payload/build-and-patch.sh`; doesn't affect the hand-written
      instruction bytes since nothing in `hello.s` references an external
      symbol).
- [x] Structure fully documented: `loader/dump_structure.py` (otool
      `-l`/`-h` equivalent, reusing `tools/macho_patch.py`) dumps every
      load command, segment/section, the `LC_MAIN` entry point, and a
      static `svc`-instruction scan. Confirmed the `svc #0x80` byte
      encoding (`0xd4001001`) by hand before trusting the tool. Tested in
      this sandbox against a real compiled binary
      (`tests/test_loader_dump_structure.py`, 3 tests).
- [x] `loader/macho_loader.c` — a from-scratch Mach-O parser/loader that
      reads the file, maps `__TEXT` into freshly-reserved memory (handling
      the target's PIE-ness via its own reserve-then-slide, not `MAP_FIXED`
      at the literal preferred address), resolves the `LC_MAIN` entry
      point, statically scans for `svc` instructions before ever jumping,
      calls `sys_icache_invalidate` (required for ARM64 self-written-code
      correctness, not optional), and then actually jumps to the entry
      point — without touching dyld or macOS's normal loader path at all.
- [x] `docs/syscall-table-diff.md`: for `exit`/`write`, there is **no ABI
      difference** between macOS arm64 and iOS arm64 at all — same trap
      immediate, same syscall number, same registers — because both share
      the same kernel (XNU) and syscall table source. This is also, this
      doc argues, *why* M1/M2 succeed with just a platform-tag patch: the
      real per-platform differences (e.g. `posix_spawn` → `EPERM`) are
      enforced by sandbox/MAC-framework *policy* layered on top of an
      identical syscall table, not by the syscall table itself differing.
- [x] CI-testable (`.github/workflows/build.yml`): builds the target and
      the loader on the real macOS runner, runs
      `./loader/macho_loader loader/target`, and asserts the target's own
      `write` syscall's output actually appears — proof the parsing +
      mapping + entry-point resolution is correct, not just that
      everything compiled. **Not yet confirmed** (this session's first
      push of this track); see the next CI run.
- [ ] **Explicitly not yet done, flagged as on-device follow-up**: this
      entire milestone runs as a normal macOS command-line tool on the CI
      runner. Nothing here has been tried on iOS, where `mmap`/`mprotect`
      with `PROT_EXEC` inside an app sandbox may face restrictions this
      milestone hasn't measured (this loader's own W^X design never holds
      `PROT_WRITE` and `PROT_EXEC` on the same page simultaneously, which
      may or may not be sufficient — unconfirmed). Also not done: actually
      intercepting/redirecting the located `svc` sites at runtime (this
      milestone only locates them statically before jumping) — a future
      milestone's concern, once the on-device mmap/mprotect question is
      answered.

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
