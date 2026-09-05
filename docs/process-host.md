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

`--no-resign` on the `dylibify` step, if this dylib is going into an app
bundle that SideStore will resign at install time (see `AUDIT.md` section 2)
-- SideStore's own signature replaces whatever's there anyway, so signing
it here would be wasted work. Keep the ad-hoc resign (the default) only for
standalone inspection of the file.

The dylib needs one exported C function matching `int name(void)` -- this
is the CLI's ported entry point (the same shape as `port/CLICore`'s
`clicore_run`, adjusted to return an int status instead of a string).

## Invoking it from the host app

This is ordinary `NSExtension` API, not anything private -- the same shape
`ios18-probe`'s own `Probe/main.m` used for its own extension test:

```objc
NSError *error = nil;
NSExtension *ext = [NSExtension extensionWithIdentifier:@"dev.local.maciOS.PortApp.ProcessHost"
                                                   error:&error];
NSExtensionItem *item = [NSExtensionItem new];
item.userInfo = @{@"dylibPath": dylibPath, @"entryPoint": @"mytool_main"};
[ext beginExtensionRequestWithInputItems:@[item] completion:^(NSUUID *identifier) {
    int pid = [ext pidForRequestIdentifier:identifier];
    // the reply NSExtensionItem (with "exitCode" or "error" in its
    // userInfo) arrives through the extension's own completion/result
    // path once beginRequestWithExtensionContext: calls
    // completeRequestReturningItems:
}];
```

## Known limitations (carried over from ios18-probe's findings)

- **One call per process instance, for now.** Each extension request gets
  a fresh process; there's no persistent "keep it running and call it
  again" path implemented here yet.
- **A crash is unobservable.** If the payload's `entry()` call crashes, the
  process dies and the caller sees the extension request get cancelled --
  there's no signal beyond that from this side. `ios18-probe`'s own probe
  app hit this repeatedly; its workaround (writing progress to disk before
  each risky call) is the pattern to reach for if a payload needs that
  kind of visibility.
- **No argv.** App-extension launches don't carry a shell-style `argv`; a
  payload that needs arguments should read them from a file path or a
  second `userInfo` key, not expect `int main(int argc, char **argv)`
  semantics.
- **Untested on-device.** Like the rest of this repo, this was written and
  structurally reasoned about from a Linux sandbox with no Apple toolchain
  -- see `AUDIT.md` section 3 for exactly what could and couldn't be
  verified here. Wiring `process-host/` into an actual Xcode extension
  target, bundling it, and confirming a payload actually runs is the next
  on-device step, same as `docs/xcode-setup.md`'s M1.
