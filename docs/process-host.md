# process-host: running a payload as a real, separate process

## Why this exists

`AUDIT.md` documents a hard wall found by direct, on-device measurement (in
the course of this research, against real binaries): `posix_spawn()` of
anything inside an iOS app's sandbox returns `EPERM`. There is no way for a
sandboxed app to `fork`/`exec`/`posix_spawn` a new process, period. That
looked like it capped every milestone here at "load foreign code as a
library inside my own process" (`dlopen`), never "run it as its own real
process."

LiveContainer's `LiveProcess` component (github.com/LiveContainer/LiveContainer)
shows the actual way around it: don't spawn a process yourself -- ask iOS's
own app-extension launch machinery to create one for you. `process-host/`
here is a deliberately smaller version of that same trick, scoped to this
project's actual need (run a payload function in a separate process and
get a result back), not LiveContainer's larger need (host a full guest
UIKit app's scene for multitasking).

## The mechanism

`process-host/Info.plist` declares an `NSExtension` against the
`com.apple.ar.viewer` extension point (the real, ordinary AR Quick Look
extension point) with three properties that matter:

- `NSExtensionActivationRule: FALSEPREDICATE` -- the real AR Quick Look
  flow never activates this extension. It only ever runs when this app's
  own code requests it by identifier.
- `XPCService._MultipleInstances: true` -- lets this one declared extension
  be launched as more than one **concurrent** process. An ordinary
  extension point gives you a singleton instance; this is what lets the
  same recipe scale to running several payloads at once later, if that's
  ever needed.
- `XPCService._ProcessType: "App"` -- gives each spawned instance
  RunningBoard's app-class resource limits (memory, CPU, jetsam priority)
  instead of the much tighter limits most extensions get. This matters for
  a payload doing real work, not just a quick round trip.

`process-host/main.m`'s `ProcessHostHandler` is the extension's
`NSExtensionPrincipalClass`. When invoked, it reads a `dylibPath` and
`entryPoint` out of the extension request's input item, `dlopen`s the
dylib, `dlsym`s the named `int name(void)` function, calls it, and replies
with the result -- all inside a genuinely separate OS process from the app
that requested it. See the comments in `main.m` for the exact contract and
for why (unlike LiveContainer) this does **not** shadow `UIApplicationMain`
or hook `dlopen` to intercept UIKit's own load: a ported CLI payload
doesn't need to behave like a full guest app, so that extra machinery is
left out.

## Preparing a payload

A payload is whatever `tools/macho_patch.py` produces for M1/M2: a compiled
arm64 Mach-O, platform-tagged for iOS, converted from `MH_EXECUTE` to
`MH_DYLIB`:

```sh
python3 tools/macho_patch.py patch mytool -o mytool.ios --platform ios
python3 tools/macho_patch.py dylibify mytool.ios -o mytool.dylib \
    --install-name mytool.dylib --no-resign
```

`--no-resign` on the `dylibify` step: whatever signs the final app bundle
-- Xcode at build time (the normal case: add this file to a "Copy Files"
build phase targeting `Frameworks/`, and Xcode's own automatic signing
covers it, no other tool needed) or SideStore at install time (only if
this dylib is being fetched/patched *after* the app is already installed
-- see `AUDIT.md` section 2) -- replaces whatever signature is here
already, so signing it in this step would be wasted work. Keep the ad-hoc
resign (the default) only for standalone inspection of the file.

The dylib needs one exported C function matching `int name(void)` -- this
is the CLI's ported entry point (the same shape as `port/CLICore`'s
`clicore_run`, adjusted to return an int status instead of a string).

**The current on-device test doesn't use this pipeline yet.**
`test-payload/TestPayload.c` is a real Xcode-built framework target
(`project.yml`'s `TestPayload`, embedded in `MaciOSPortApp` alongside
`ProcessHost`) exporting exactly one function, `maciOS_test_entry`,
returning `42`. It exists to test `process-host/`'s mechanism in
isolation -- does the extension trick really produce a separate process,
does `dlopen`/`dlsym` really resolve something at
`Frameworks/TestPayload.framework/TestPayload` -- before adding the
separate variable of a *foreign*, macho_patch.py-patched binary's own
dependencies (AUDIT.md section 5) into the same test.

## Invoking it from the host app

This uses `NSExtension` directly -- a **real but undocumented** Foundation
class (no public header at all), the same one LiveContainer's own
`FoundationPrivate.h` and `ios18-probe`'s `Probe/main.m` forward-declare
rather than import. `port/MaciOSPortApp/ProcessHostTester.h`/`.m` is this
project's version of that forward declaration, wired to a "Test
ProcessHost" button in `ContentView.swift`. Two things worth knowing
before reading it:

- **The identifier in `Info.plist` is not the identifier that's actually
  registered on-device.** Confirmed directly, not theoretical: SideStore's
  install-time resign inserted its own team-ID segment into the middle of
  it (`dev.local.maciOS.PortApp.ProcessHost` in the source became
  `dev.local.maciOS.PortApp.676TQUDKG7.ProcessHost` on install --
  `installd`'s own error message is what surfaced this, while diagnosing
  an unrelated `CFBundleDisplayName` rejection). `ProcessHostTester.m`
  reads the real identifier back from the installed `.appex`'s own
  `Info.plist` under `Bundle.main.builtInPlugInsURL` rather than
  hardcoding the design-time string, for exactly this reason.
- **This tests process-spawn, not the payload's exit code.** `main.m`'s
  `ProcessHostHandler` replies via `completeRequestReturningItems:` with a
  `pid`/`exitCode`/`error` payload, but whether that reply is actually
  observable back on the caller's side through any proven mechanism is
  still an open question -- neither this project's own testing nor the
  prior art it draws on has confirmed it. `ProcessHostTester.m`
  deliberately doesn't rely on it: it reports success only via
  `pidForRequestIdentifier:` (a real, different PID) and failure via
  `setRequestCancellationBlock:`, both confirmed working in `ios18-probe`'s
  own on-device testing ("LAUNCHED as a real separate process... pid=%d").
  That's a smaller, honestly-scoped claim -- "did a real separate process
  come up at all" -- than "did the payload's exit code come back," and
  it's the one to get answered first.

## Known limitations (carried over from ios18-probe's findings, or found here)

- **The exit-code/error reply path is unverified**, per above -- treat
  `main.m`'s reply payload as best-effort until something confirms the
  caller can actually read it back.
- **One call per process instance, for now.** Each extension request gets
  a fresh process; there's no persistent "keep it running and call it
  again" path implemented here yet.
- **A crash is unobservable** beyond `setRequestCancellationBlock:` firing.
  If the payload's `entry()` call crashes, the process dies and that's
  the only signal from this side. `ios18-probe`'s own probe app hit this
  repeatedly; its workaround (writing progress to disk before each risky
  call) is the pattern to reach for if a payload needs more visibility
  than that.
- **No argv.** App-extension launches don't carry a shell-style `argv`; a
  payload that needs arguments should read them from a file path or a
  second `userInfo` key, not expect `int main(int argc, char **argv)`
  semantics.
- **Bundle identifiers get rewritten on install.** See above -- never
  hardcode an extension's design-time `CFBundleIdentifier` when invoking
  it; read it back from the installed bundle.
