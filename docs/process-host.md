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
python3 tools/macho_patch.py dylibify mytool.ios -o p.dylib \
    --install-name p.dylib --no-resign
python3 tools/macho_patch.py symbols p.dylib --grep mytool_main --defined-only
```

Keep the install name short -- **confirmed directly, not a style
preference**: `LC_LOAD_DYLINKER` always names `/usr/lib/dyld` for any
normally-linked executable, and `dylibify` repurposes that exact load
command's space for the new `LC_ID_DYLIB`, so the name has to fit in
whatever room that leaves (about 7-8 characters for a typical binary --
`tools/macho_patch.py`'s own error tells you the real number if a longer
name doesn't fit). It's an internal identifier, not something callers
look up by, so a short one costs nothing.

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
Run `tools/macho_patch.py symbols` afterward (as above) to confirm it's
still there, externally defined -- don't just assume a byte-level patch
left the symbol table untouched.

**Two payloads are wired into the app now, testing two different things:**

- `test-payload/TestPayload.c` is a real Xcode-built framework target
  (`project.yml`'s `TestPayload`), exporting `maciOS_test_entry` (returns
  `42`). It's a **control**: compiled straight for iOS, never touching
  this pipeline at all, so it isolates "does the extension trick itself
  produce a separate process" from "does a foreign binary's own
  compatibility work out." **Confirmed working on-device 2026-09-06.**
- `test-payload/PatchedMacOSPayload.dylib` is `fixtures/macos_payload.c`
  compiled for `arm64-apple-macos` (never iOS), then run through this
  exact pipeline above by `test-payload/build-and-patch.sh` -- exporting
  `maciOS_patched_payload_entry` (returns `99`). This is the actual M2
  claim ("you don't need the source"), not yet confirmed on-device as of
  this writing. `build-and-patch.sh` must run before `xcodegen generate`
  (`project.yml` embeds its output via a Copy Files build phase), which
  `.github/workflows/build.yml` does automatically.

`ContentView.swift` has one "Test ProcessHost" button per payload.

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
  `ProcessHostHandler` still replies via `completeRequestReturningItems:`
  with a `pid`/`exitCode`/`error` payload, but that specific reply is not
  observable from the host side by any mechanism found so far. A separate,
  independent channel -- Darwin notifications -- is what `ContentView.swift`'s
  two "Test ProcessHost" buttons actually rely on now, per the
  investigation below.

## The reply-channel investigation (two dead ends, then real public API)

`ProcessHostTester.m`'s original forward declaration of `NSExtension` only
had the four methods LiveContainer and `ios18-probe` actually used to
prove a process launches: `extensionWithIdentifier:error:`,
`beginExtensionRequestWithInputItems:completion:`, `pidForRequestIdentifier:`,
`setRequestCancellationBlock:`/`setRequestInterruptionBlock:`. None of
those needed to observe `main.m`'s `completeRequestReturningItems:` reply
payload, because neither project asked "what did the extension hand
back," only "did a separate process come up."

Rather than guess at a wider block signature, `NSExtensionIntrospection.h`/`.m`
asked the real, loaded `NSExtension` and `NSExtensionContext` classes what
their actual method surfaces are, using
`class_copyMethodList`/`method_getTypeEncoding` -- the same "ask the
runtime directly" technique `ios18-probe` used against CoreSimulator (the
"Introspect NSExtension"/"Introspect NSExtensionContext" buttons in
`ContentView.swift` still dump this on demand).

**`NSExtension`'s dump, confirmed on-device 2026-09-06: no, not through
this class.** There is no `resultForRequestIdentifier:`,
`outputItemsForRequestIdentifier:`, or any other accessor exposing
`completeRequestReturningItems:`'s payload -- `pidForRequestIdentifier:`
is the *only* per-request state `NSExtension` exposes after launch.

**The dump did surface a real lead:** two methods take an extra
`NSXPCListenerEndpoint` argument the plain `...completion:` variants
don't -- `beginExtensionRequestWithInputItems:listenerEndpoint:completion:`
and `beginExtensionRequestWithOptions:inputItems:listenerEndpoint:completion:`.

**`NSExtensionContext`'s dump, confirmed on-device 2026-09-06, closed the
loop:** `_auxiliaryListener`/`_setAuxiliaryListener:` and
`_auxiliaryConnection`/`_setAuxiliaryConnection:` are real methods on that
class, and line up with `initWithInputItems:listenerEndpoint:contextUUID:`
-- the constructor `NSExtension` must use internally when a request is
made with a listener endpoint. That's the matching private slot on the
extension side for whatever endpoint the host passed in.

**This was wired in for real and tried on-device -- and ruled out, not
just left unconfirmed.** The build: `ProcessHostTester.m` built an
anonymous `NSXPCListener` and handed its endpoint into
`beginExtensionRequestWithInputItems:listenerEndpoint:completion:`;
`process-host/main.m` would have retrieved it via
`context._auxiliaryListener` and called back through a shared protocol.
None of that ever ran, though: **`beginExtensionRequestWithInputItems:
listenerEndpoint:completion:` itself returned a nil request identifier for
both payloads, confirmed on-device 2026-09-06** -- and fast (~120ms from
tap to failure for both), the signature of a synchronous validation
rejection, not a hung or crashed launch attempt. This extension point
(`com.apple.ar.viewer`, declared with `FALSEPREDICATE`/`_MultipleInstances`/
`_ProcessType:"App"` -- see "The mechanism" above) evidently does not
accept an arbitrary caller-supplied listener endpoint through this call,
whatever internal contract it actually expects there. The code for this
attempt (the `NSXPCListener`/`NSXPCConnection` plumbing on both sides, the
shared protocol header) was reverted rather than kept as dead, known-broken
code -- **this section is the record of what was tried and why it doesn't
work**, so a future session doesn't re-attempt the identical approach.

The two things that got ruled out, plainly:

1. `completeRequestReturningItems:`'s payload -- no accessor exists on the
   host side at all (per `NSExtension`'s dump).
2. A caller-supplied `NSXPCListenerEndpoint` via `beginExtensionRequestWithInputItems:
   listenerEndpoint:completion:` -- rejected outright by this extension
   point before a process is even launched (confirmed on-device).

Still untried, if this one doesn't pan out either:
`beginExtensionRequestWithOptions:inputItems:listenerEndpoint:completion:`
(the sibling overload with an explicit `options:` dictionary -- maybe the
plain 3-arg form is simply incomplete without one, though this is a guess,
not a finding).

## The third attempt: Darwin notifications (real, public, documented API)

Both attempts above are private, undocumented API -- exactly the kind of
thing that can silently stop working across an OS update, or (as it turned
out) simply not behave the way its name and argument shape suggest. Darwin
notifications (`CFNotificationCenterGetDarwinNotifyCenter()`) are the
opposite: real, public, documented Apple API, the same mechanism many
ordinary App Store apps use for app-extension signaling, and specifically
`ios18-probe`'s own fallback once it found App Group files don't survive
SideStore's resign (so a shared-file-based channel was out for the same
reason it would be here).

The catch: Darwin notifications carry no payload of their own -- there is
no `userInfo` dictionary, unlike the *local* notification center. The
mechanism this project uses instead (`process-host/MaciOSProcessHostDarwinReply.h`/`.m`,
shared by both `ProcessHostTester.m` and `main.m` via one implementation,
not two copies that could drift) encodes the exit code/error directly into
the notification's own *name*:

- The host (`ProcessHostTester.m`) generates its own `requestUUID` (not the
  system's own opaque per-launch identifier from
  `beginExtensionRequestWithInputItems:completion:`, since that's only
  known once the process has *launched*, and the request needs a
  correlation ID before that), registers a Darwin notification observer
  for *every* notification system-wide (there's no way to filter
  server-side on a name that encodes an unknown payload), and filters by
  the `requestUUID` prefix itself in the callback.
- `process-host/main.m` posts `dev.local.maciOS.processhost.reply.<uuid>.ok.<exitcode>`
  on success or `...err.<percent-encoded, alphanumeric-only, 200-char-capped>`
  on failure, right before (and independent of) its existing
  `completeRequestReturningItems:` call.
- `ContentView.swift`'s two "Test ProcessHost" buttons log a `REPLY:
  exitCode=... error=...` line if and when this arrives.

**Tested on-device 2026-09-06: no `REPLY:` line, for either payload, even
after an 11-second wait** (ruling out a simple "didn't wait long enough"
explanation). A specific, concrete hypothesis for why:
`CFNotificationCenter.h` documents `NULL` as a wildcard ("if name is
NULL... all notifications will match"), which `ProcessHostTester.m`'s
observer relies on since it can't know its expected name -- the exit code
is embedded in it -- in advance. That documentation isn't scoped to which
center, though, and the Darwin center specifically wraps the low-level
`notify(3)` mechanism, whose real registration primitive
(`notify_register_dispatch`) has historically required an *exact* name
token per registration -- unlike the local/distributed centers' pure
userspace pub/sub, which do support a real wildcard. If that's the actual
behavior here, the observer's callback would never fire for *anything*,
regardless of whether `process-host/main.m` posted its reply correctly --
which matches what was observed.

**`port/MaciOSPortApp/DarwinNotificationSniffer.h`/`.m`** (+ a "Sniff
Darwin Notifications" button) tested this specific hypothesis in
isolation: registers with `NULL` the same way, but logs *any* notification
it receives -- including ones this app had nothing to do with, which the
system posts constantly regardless of what this app does.

**Confirmed on-device 2026-09-06: zero notifications logged over 27
seconds**, spanning two ProcessHost launches (each of which should have
triggered `main.m`'s own post) and the ordinary background rate of
unrelated system Darwin notifications. Not a single one arrived -- the
`NULL`-name wildcard registration does not work on the Darwin center on
this device/OS, full stop. This is a clean result, not an ambiguous one:
zero of *anything* rules out both "the extension didn't post" and "the
name encoding didn't match" as explanations, since either of those would
still have let *unrelated* system notifications through. The registration
mechanism itself is the dead end.

**So this is the third reply-channel mechanism ruled out on-device**,
after `completeRequestReturningItems:` (no host-side accessor at all) and
the `NSXPCListenerEndpoint`/`_auxiliaryListener` approach (rejected
outright by this extension point). All three shared one root cause this
project's whole existing signal (`pidForRequestIdentifier:`) doesn't have:
each needed to observe or register for *something not fully known in
advance* -- a result payload, an endpoint the extension point would
accept, a notification name containing an unpredictable exit code -- and
each channel's real behavior (not its documented contract) turned out not
to support that.

**What would still work, if this is worth a fourth attempt:** Darwin
notifications *do* support exact-name registration reliably (this is how
`main.m`'s own reply attempt would have been received, had the name been
predictable) -- the failure is specifically the wildcard. A genuinely
fixed, fully-enumerable name space sidesteps it: e.g. encode a small
integer exit code as up to 8 individually pre-registered exact
notifications (`...reply.<uuid>.bit0` through `...bit7`, posted only for
set bits), rather than one name containing the value. Inelegant, but
real -- unlike the three attempts above, it wouldn't depend on any
undocumented behavior actually working. Not built, pending a decision on
whether this is worth the added complexity versus deprioritizing the
reply channel (nothing in M1/M2's own claim needs it -- both payloads
already confirm success via `pidForRequestIdentifier:`).

## Known limitations (carried over from ios18-probe's findings, or found here)

- **The exit-code/error reply path has no working mechanism, after three
  attempts.** `completeRequestReturningItems:`, the auxiliary-connection
  listener-endpoint approach, and Darwin notifications (`NULL`-name
  wildcard registration) were all tried and ruled out on-device -- see
  above. Treat `main.m`'s `completeRequestReturningItems:`/
  `CFNotificationCenterPostNotification` calls as write-only until a
  fixed-name-space redesign (above) or something new is tried.
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
