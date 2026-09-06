# maciOS architecture audit

Target device for this audit: iPhone 14, iOS 26.1, **not jailbroken** (confirmed
with the user — no jailbreak is available or wanted, and none was found to
exist for this exact device/iOS combination at the time of writing).
Everything below is scoped to that constraint.

## 1. What "Darling" actually solves, and what iOS already has

Darling ([darlinghq/darling](https://github.com/darlinghq/darling)) exists
because **Linux's kernel has no idea what a Mach-O binary or a Darwin/BSD
syscall is**. Its job is to reimplement, in userspace on top of Linux: Mach
IPC, the POSIX/BSD syscall surface, dyld (the dynamic linker), launchd, and
increasingly complete reimplementations of Apple frameworks (Foundation,
CoreFoundation, AppKit via Cocotron, etc.), all without hardware emulation —
native x86_64/arm64 instructions run directly, only the *environment* around
them is synthesized.

iOS's kernel is XNU — the same kernel family as macOS. **None of that
syscall/Mach-IPC/dyld reimplementation work is needed on iOS**: the kernel
already understands Mach-O, Mach ports, and Darwin syscalls natively. This
is the whole reason "run macOS binaries on iPhone, natively, no emulator"
sounds tractable at all — architecturally it should be a much smaller
problem than what Darling solves on Linux.

What's different on iOS is not the kernel's syscall table. It's everything
Apple bolted on top of XNU specifically to control what code is allowed to
run at all:

| Layer | macOS (typical) | iOS |
|---|---|---|
| Code signing enforcement | Advisory outside the App Store/Gatekeeper path; ad-hoc/unsigned arm64 code can run from a shell | Mandatory, in-kernel (AMFI), for every executable page, no exceptions on stock firmware |
| Process launch surface | Any shell can `exec()` any signed-enough binary | No general-purpose shell with `exec` rights; a new process comes only from an installed app bundle **or** a declared app extension (see section 4) |
| Trust roots accepted | Apple root, plus locally-generated self-signed/ad-hoc identities | Apple root (App Store/TestFlight/enterprise), or a developer certificate + matching provisioning profile — applied by Xcode at build time, or by AltStore/SideStore at install time for content that didn't exist at build time (section 2) — **ad-hoc/identity-less signatures don't satisfy library validation** |
| Sandbox | Opt-in (`sandbox-exec`), most CLI tools run unsandboxed | Mandatory per-app container sandbox, entitlement-gated |
| Frameworks available | Full AppKit/Cocoa/Cocoa Touch split; macOS-only frameworks | iOS's dyld shared cache contains no AppKit; UIKit only |
| W^X / JIT | Default deny; various opt-outs exist | Default deny; JIT needs a specific entitlement or a debugger attach, granted only to processes iOS already trusts |

So the real problem on iOS is not "translate the environment," it's "get
the kernel to agree to launch this code at all" — a trust/authorization
problem, not a compatibility one. Sections 2 and 4 below are two concrete,
validated answers to that problem that don't require jailbreak.

## 2. Byte-patching a Mach-O *is* enough — with a real signing identity

An earlier version of this document claimed byte-level Mach-O patching
"cannot get an unmodified macOS binary running on a non-jailbroken
iPhone," full stop. That was wrong, and the correction matters enough to
walk through in full, because it's the basis for the M2 milestone.

### What's true, and what this repo validated directly

`tools/macho_patch.py` (tests in `tests/test_macho_patch.py`, fixture in
`fixtures/`) cross-compiles a real, thin, arm64, ad-hoc-signed macOS
Mach-O executable from Linux with no Mac involved
(`clang -target arm64-apple-macos11 -fuse-ld=lld -nostdlib …`), then:

- Rewrites `LC_BUILD_VERSION`'s platform field (macOS → iOS) **in place**
  — no load command changes size, no segment moves, file length is
  byte-identical. Verified via manual byte diffing and independent
  SHA-256 recomputation against the real cross-compiled binary.
- Converts `MH_EXECUTE` → `MH_DYLIB` (`dylibify`), repurposing the
  original `LC_LOAD_DYLINKER` command's space for a new `LC_ID_DYLIB` —
  again with no relocation, verified the same way.
- Recomputes the ad-hoc `CodeDirectory`'s per-page SHA-256 hashes
  afterward, so the file's own signature is internally self-consistent.

An **ad-hoc** signature alone (`CS_ADHOC`, no CMS blob, no certificate) is
genuinely not enough on iOS: it satisfies "this file has *a* signature"
but not the separate check, `CS_REQUIRE_LV` (library validation), that a
loaded image's signature chains to the *same team* as the process loading
it (or to Apple). This was confirmed by direct, on-device measurement in
the course of this research (not inferred): an ad-hoc-signed dylib is read
successfully by dyld and then refused with `EPERM`, while a control
dylib — one of the app's own, signed by its real developer-team
certificate, copied into the same directory — loads without complaint.
Identity is the whole difference, not location, not the signature's mere
presence.

### The fix: apply a real signing identity to the whole bundle

Two different tools do this, for two different situations — worth being
precise about which one this project actually needs, since an earlier
version of this document over-pointed at the wrong one:

- **Xcode itself**, if the binary is available *before* you build — patch
  it with `tools/macho_patch.py`, add it to the Xcode project as a "Copy
  Files" build phase targeting `Frameworks/` (or as an embedded app
  extension, `process-host/`'s case). Xcode's own build system re-signs
  every embedded item — frameworks, app extensions, anything copied into
  `Frameworks/` — with the project's own signing identity as a completely
  standard, automatic part of building. This is not a workaround; it's
  what Xcode always does for embedded content, and it's sufficient on its
  own, with nothing else installed, for both M1 and M2 as currently
  scoped in this repo (the payload is patched *before* the build, so it's
  just another file in the project by the time Xcode signs anything).
- **SideStore/AltStore**, if the binary only exists *after* the app is
  already installed — these sideloading tools **re-sign every embedded
  binary in the app bundle with the developer's own team certificate at
  install time**, which is the mechanism to reach for when new content
  can't be embedded at Xcode build time at all. This project's own prior
  art for the whole approach (`ios18-probe`) needed exactly this: it
  fetches a 16GB simulator runtime over the network *after* install and
  patches parts of it then, so there was no "build time" at which Xcode
  could have signed it. Not a requirement for M1/M2 here today, but the
  right tool once a milestone wants runtime-fetched, runtime-patched
  content rather than something prepared before the build.

Either way, the binaries in the bundle end up sharing one real, trusted
signing identity, which is exactly what library validation checks for —
whether that identity got applied by Xcode at build time or by SideStore
at install time doesn't matter to the kernel.

The `ios18-probe` prior art demonstrates the *patching* half of this
concretely: **Apple's own iOS Simulator runtime code**
(`CoreSimulator.framework`, extracted from the DMG Xcode normally ships it
in, byte-patched the same way `tools/macho_patch.py` patches files here)
loaded and genuinely executing — real `SimServiceContext`, `SimDeviceType`,
`SimRuntime`, and `SimDevice` objects instantiated and driven through
their actual Objective-C API — inside a SideStore-signed app on a
physical, non-jailbroken iPhone (SideStore, there, because the runtime is
fetched post-install). That's strong evidence the underlying mechanism
(a shared, real signing identity satisfies library validation for
byte-patched content) works in practice, not just in theory — and it
applies the same way whether the identity comes from Xcode or SideStore.

**What this doesn't unlock:** a real, trusted signing identity is a
requirement most patched Mach-O binaries didn't have a way to satisfy
before; it is not a bypass of code signing itself. Every binary still has
to be a valid, hash-consistent Mach-O; the trust chain still has to be
real (your own developer identity, obtained legitimately); and a binary
whose dependencies don't exist on iOS at all (section 5) still won't run
just because it's signed.

### `tools/macho_patch.py dylibify`

Converts an already-compiled `MH_EXECUTE` into a loadable `MH_DYLIB` for
this pipeline — no source, no recompile needed for a binary whose
dependencies are already iOS-compatible (or stubbed, section 5). See its
module docstring and `tests/test_macho_patch.py` for the validated detail
(file size unchanged, `LC_ID_DYLIB` correctly written, resigned hashes
verify). This is what M2 (`MILESTONES.md`) is built on.

## 3. The `posix_spawn` wall, and the extension-process answer

Even with a trusted signature, a sandboxed iOS process cannot spawn a new
one: `posix_spawn()` against a real on-disk binary, from inside an app's
sandbox, measures as `EPERM` directly (confirmed on-device, not inferred
from documentation). There is no general-purpose `exec` surface on iOS —
this was the original basis for saying byte-patched code could only ever
be `dlopen`'d into an *existing* process, never run as its own.

The way around it, found in LiveContainer's `LiveProcess` component
(github.com/LiveContainer/LiveContainer): don't spawn a process — ask
iOS's own app-extension launch machinery to create one. A real, ordinary
system extension point (`com.apple.ar.viewer`, the AR Quick Look viewer)
can be declared with two properties most extension points don't get:

- `XPCService._MultipleInstances: true` — this one declared extension can
  be launched as more than one **concurrent, independent process**,
  rather than the usual extension singleton.
- `XPCService._ProcessType: "App"` — each instance gets RunningBoard's
  app-class resource limits (memory, CPU, jetsam priority), not the
  much tighter limits an ordinary extension gets.

`NSExtensionActivationRule: FALSEPREDICATE` means the real AR Quick Look
flow never triggers it; it only runs when the app's own code requests it
by identifier via `NSExtension` — a real class, but an undocumented one
(no public header; `extensionWithIdentifier:error:`,
`beginExtensionRequestWithInputItems:` have to be forward-declared, same
as LiveContainer's own `FoundationPrivate.h` does it). Because it's a
declared extension bundled and signed as part of the container app, the
same install-time resign from section 2 covers it — no separate signing
story needed.

**Confirmed on-device, 2026-09-06** (iPhone 14, iOS 26.1, not jailbroken):
the host app (pid 1867) invoked `process-host/` and got back pid 1869 — a
real, different process, produced without ever calling `posix_spawn`. See
`MILESTONES.md`'s M2 for the exact log. This was the single largest
remaining unknown in the whole audit: LiveContainer ships this trick and
`ios18-probe` used it too, but nothing in *this* project had exercised it
until this test, and there was no guarantee it would behave identically
on a different device/iOS version. It does.

This project's own version of that component is `process-host/` — see
`docs/process-host.md` for the full mechanism and the payload contract.
It's deliberately smaller than LiveContainer's: LiveContainer also shadows
`UIApplicationMain` and hooks `dlopen` so its spawned processes can behave
like a full guest iOS app (for hosting that app's UI in "nativeWindow"
multitasking mode). A ported CLI payload doesn't need any of that — just
a real process to run in and a result reported back — so `process-host/`
leaves that machinery out.

## 4. What remains viable: two paths, both native ARM64, neither needing jailbreak

**Path A — source port.** You have the source (yours, or an open-source
tool whose dependencies are POSIX/Foundation-level, not AppKit/Cocoa).
Recompile it for `arm64-apple-ios` (still native ARM64; only the target
triple changes), wrap it in a minimal iOS app shell, sign with your own
Apple ID via Xcode ("personal team," free, 7-day renewal), install over
USB/Wi-Fi. This is `port/` + `docs/xcode-setup.md` (M1).

**Path B — patch an existing compiled binary.** You have a compiled
`arm64-apple-macos` binary but not necessarily its source. Patch its
platform tag, convert it to a dylib (`tools/macho_patch.py`), add it to
the Xcode project as a build-phase input targeting `Frameworks/` so
Xcode's own automatic signing covers it — either `dlopen`'d directly into
the host app's own process, or run as its own process via `process-host/`
(section 3). This needs no recompile, but is bounded by whether the
binary's dependencies resolve on iOS at all (section 5) — this is
`MILESTONES.md`'s M2. (SideStore only enters the picture if the payload
needs to be fetched or patched *after* install, per section 2 — not
needed for M2 as currently scoped.)

### Build requirement this session cannot satisfy directly

Building and signing an actual installable `.app`, and running it on a
physical iPhone, requires **Xcode on a Mac** (for the iOS SDK, code
signing/provisioning, and the USB/Wi-Fi device-install pipeline) — that's
the only tool required for M1 and M2 as currently scoped; a free Apple ID
("personal team" automatic signing) is enough, no paid Developer Program
and no SideStore/AltStore needed. This sandbox is Linux with a stock
`clang`/`lld` and no Apple toolchain beyond that (no iOS SDK, no `xcrun`,
no Swift compiler, no `codesign`). Concretely:

- I *can*, and did, validate that portable C source compiles cleanly to an
  arm64 object file under `-target arm64-apple-ios15.0` here, and that
  `tools/macho_patch.py`'s platform-tag and dylibify patches are
  byte-correct against a real cross-compiled Mach-O (proves the core
  logic is target-portable and the patch tooling is correct at the
  object-code level).
- I *cannot* link a real iOS executable (no libSystem stubs from the SDK),
  compile the Swift/SwiftUI or Objective-C shell, produce a signed `.app`,
  or install/run anything on an iPhone from here. Those steps are in
  `docs/xcode-setup.md` and `docs/process-host.md` for you to run on your
  own Mac and device.

**Update:** `.github/workflows/build.yml` closes part of this gap without
needing a Mac at all — it runs on a `macos-latest` GitHub Actions runner,
generates the Xcode project with XcodeGen (`project.yml`) from the exact
source in `port/` and `process-host/`, and builds it for the iOS Simulator
with code signing disabled. That confirms the Swift/SwiftUI and
Objective-C actually compile against the real SDK (Info.plist schema
included) — something nothing in this repo had been checked against
before. It does **not** confirm anything about on-device behavior,
entitlements, or whether `process-host/`'s extension-launch trick actually
gets a separate PID: a simulator build doesn't exercise any of that, and
still needs your Mac + iPhone (Xcode alone — see above), per the sections
above.

## 5. iOS restrictions inventory (for reference)

- **AMFI / code signing + library validation**: every executable page must
  be covered by a CodeDirectory, and every loaded image's signature must
  chain to the *same team* as the process loading it (or to Apple).
  Section 2 covers the fix (a real signing identity applied to the whole
  bundle at install time) — this is not a bypass, the trust chain still
  has to be real.
- **No general-purpose exec surface**: `posix_spawn` measures as `EPERM`
  inside the app sandbox. Section 3 covers the fix (a declared,
  `_MultipleInstances`/`_ProcessType:"App"` app extension, launched
  through ordinary `NSExtension` API instead of `exec`).
- **Sandbox**: every process still runs in a per-app container regardless
  of how it was launched; filesystem, network, IPC, and device access
  stay entitlement- and profile-gated.
- **Framework availability**: no AppKit/Cocoa in iOS's shared cache; only
  UIKit-family APIs, and a macOS-only framework a patched binary depends
  on has no iOS equivalent to redirect to at all — it needs a stub
  implementation (as CoreSimulator.framework's own macOS-only
  dependencies did in the prior art this audit draws on) or the
  dependency has to genuinely not be exercised at runtime. A GUI port
  needs an actual UI rewrite, not just a recompile, unless the app's UI
  layer is trivial or Foundation-only.
- **JIT**: default-deny; irrelevant to either wall above. JIT entitlements
  (or the debugger-attach trick, itself available without jailbreak via
  tools like StikDebug/SideJITServer) only relax W^X *inside a process iOS
  already agreed to launch* — they don't affect whether that process gets
  launched or can load another signed image, and they're not needed by
  recompiled or dylib-loaded native code, which doesn't self-modify.

## 6. Minimum milestones

See `MILESTONES.md`. **M1 is confirmed working on-device (2026-09-06)**:
built via CI, installed via SideStore on the user's iPhone 14 (iOS 26.1,
not jailbroken), and the in-app log shows `clicore_run()` executing and
returning its string — native ARM64 C code, compiled for `arm64-apple-ios`,
running inside a normally-installed app, no VM, no emulator, no jailbreak.
That confirms the whole chain this audit reasoned about actually holds up
on real hardware, not just in analysis.

**M2's core mechanism is confirmed working on-device too (2026-09-06)**:
the `process-host/` extension trick got a real, separate PID on the same
iPhone, without ever calling `posix_spawn` — see section 3. M2 is path B
(patch an existing compiled binary, run it via `process-host/`) — the more
general and more powerful of the two paths, and now that both paths' core
mechanisms are confirmed on real hardware, the remaining work is scaling
up to an actual real-world CLI tool (dependency resolution, section 5)
rather than validating the underlying approach itself. Later milestones
scale up to a real CLI tool, then (much further out, flagged as a
large/likely-out-of-scope undertaking) a GUI port.
