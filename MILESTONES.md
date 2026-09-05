# Milestones

Context: no jailbreak, so every milestone here is a **source-level port**
compiled for `arm64-apple-ios`, hosted in a minimal signed app, installed
via Xcode on a Mac. Native ARM64 throughout — no VM, no CPU emulator, no
instruction translation. See `AUDIT.md` for why this is the smallest
possible unit of "your code, running on this iPhone."

## M1 — Trivial CLI, ported and running on-device (this session's scope)

**Goal:** prove the whole pipeline end to end with the smallest possible
payload, before spending any effort on a real tool's dependencies.

- [x] `port/CLICore/clicore.{h,c}` — a tiny, framework-free C function
      (`clicore_run()`) standing in for "the CLI's logic." Validated in
      this sandbox: compiles cleanly to an arm64 object file under
      `-target arm64-apple-ios15.0` with zero warnings (`-Wall -Wextra`).
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

## M2 — A real small CLI tool

Once M1's pipeline is confirmed working on-device:

- Pick a specific small, portable (POSIX/Foundation-only, no
  AppKit/Cocoa) open-source CLI tool, or your own code. "Portable" here
  means: no `#import <Cocoa/Cocoa.h>` or `#import <AppKit/AppKit.h>`
  anywhere in the paths you need, and no dependency on macOS-only syscalls
  or `sysctl`s the sandbox will block.
- Replace its `main()` entry point with a function callable from Swift
  (same pattern as `clicore_run`, generalized to take argv-style input from
  a text field or bundled test file instead of real command-line args,
  since iOS apps don't get a shell argv).
- Redirect its stdout/stderr to a buffer (`dup2` onto a pipe, or a
  `freopen` onto a file in the app's container) so output can be displayed
  in the UI instead of a terminal.
- Expect to spend most of the effort here on: (a) any macOS-only API calls
  it makes, (b) filesystem paths it assumes exist (`/tmp`, `/usr/...` —
  redirect to the app's sandbox container paths instead), (c) any use of
  `fork`/`exec`/multiprocessing (iOS sandbox forbids spawning arbitrary
  child processes).

## M3 — GUI application (flagged, likely out of scope as "minimum")

A macOS GUI app's *logic* can be ported the same way as M2, but its *UI
layer* cannot: there is no AppKit on iOS, and reimplementing enough of
AppKit's behavior on top of UIKit to run an unmodified AppKit UI is exactly
the scale of effort Darling put into Cocotron for Linux — a multi-year,
large-surface-area project, not a "minimum milestone." The realistic scope
for a GUI port is: keep the app's non-UI logic as ported in M2, and
**rewrite** its UI layer natively in SwiftUI/UIKit. That's a per-app
rewrite effort proportional to how much of the app is UI versus logic, and
should be scoped per-app once you have a specific target in mind — not
planned in the abstract here.

## Explicitly not planned

- Any approach requiring jailbreak (AMFI bypass, arbitrary `exec`,
  ad-hoc-only signing) — ruled out by the user's constraint. `tools/`,
  `tests/`, `fixtures/` are kept as validated reference/audit artifacts
  for *why* this doesn't work, not as a path forward.
- x86_64 translation/emulation of any kind — out of scope by the user's
  explicit "preserve native ARM64 execution" requirement, and irrelevant
  anyway since the port path only ever produces native arm64 code.
