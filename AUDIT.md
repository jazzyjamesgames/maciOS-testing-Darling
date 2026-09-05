# maciOS architecture audit

Target device for this audit: iPhone 14, iOS 26.1, **not jailbroken** (confirmed
with the user — no jailbreak is available or wanted). Everything below is
scoped to that constraint. If that constraint ever changes, most of the
"rejected approach" section becomes viable again.

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
| Process launch surface | Any shell can `exec()` any signed-enough binary | Only SpringBoard/`launchd` can start a process, and only from an **installed app bundle** — there is no general-purpose interactive shell with `exec` rights |
| Trust roots accepted | Apple root, plus locally-generated self-signed/ad-hoc identities | Apple root (App Store/TestFlight/enterprise), or a developer certificate + matching provisioning profile (Xcode/AltStore/SideStore) — **ad-hoc/identity-less signatures are not installable** |
| Sandbox | Opt-in (`sandbox-exec`), most CLI tools run unsandboxed | Mandatory per-app container sandbox, entitlement-gated |
| Frameworks available | Full AppKit/Cocoa/Cocoa Touch split; macOS-only frameworks | iOS's dyld shared cache contains no AppKit; UIKit only |
| W^X / JIT | Default deny; various opt-outs exist | Default deny; JIT needs a specific entitlement or a debugger attach, granted only to processes iOS already trusts |

So the real problem on iOS is not "translate the environment," it's "get
the kernel to agree to launch this code at all" — a trust/authorization
problem, not a compatibility one.

## 2. What was tried and validated in this session (and why it fails on a
non-jailbroken device)

To ground the audit in something concrete rather than pure theory, this
session built and fully tested a Mach-O platform-tag patcher
(`tools/macho_patch.py`, tests in `tests/test_macho_patch.py`, fixture in
`fixtures/`). It:

- Cross-compiles a real, thin, arm64, ad-hoc-signed macOS Mach-O executable
  from Linux with no Mac involved (`clang -target arm64-apple-macos11
  -fuse-ld=lld -nostdlib …` — validated working in this repo's sandbox).
- Parses `LC_BUILD_VERSION` (or legacy `LC_VERSION_MIN_MACOSX`) and rewrites
  the platform field from macOS (1) to iOS (2) **in place** — no load
  command changes size, no segment moves, file length is byte-identical.
  Verified against the real cross-compiled binary via manual byte diffing
  and independent SHA-256 recomputation.
- Recomputes the ad-hoc `CodeDirectory`'s per-page SHA-256 hashes in place
  afterward, so the file's own signature is internally self-consistent
  again (verified by independently re-hashing every 4 KiB page against the
  stored digest — see `tests/test_macho_patch.py`).

This is a genuinely correct, minimal, surgical patch — and it is **not
sufficient** to run the result on a non-jailbroken iPhone, for a reason that
has nothing to do with the patch's correctness:

- iOS's installer (`installd`/`MobileInstallationd`) and AMFI don't check
  "does the CodeDirectory hash match the file contents" as the *only*
  gate — they check that the CodeDirectory (and the entitlements blob
  alongside it) is covered by a **CMS signature chaining to a trust root
  iOS recognizes**. An ad-hoc signature (no CMS blob, `CS_ADHOC` flag only)
  has no such chain. It's accepted by the *kernel* for a process already
  running under permissive conditions (this is how macOS runs unsigned
  arm64 binaries from a shell — Gatekeeper is a separate, bypassable
  userspace gate there), but iOS's installer refuses to even **install**
  such a binary as an app, and there is no shell to `exec()` it manually
  outside of an app process. Jailbreak's role is precisely to patch AMFI
  and/or the installer to drop this chain-of-trust requirement — which is
  the piece that is unavailable here.
- Separately, even with a trusted signature, iOS has no concept of running
  a bare Unix executable as a launchable unit — SpringBoard launches **app
  bundles** (`.app` with `Info.plist`, `CFBundleExecutable`, icons, etc.),
  installed through `installd`. A raw Mach-O, however perfectly signed,
  isn't itself an installable/launchable artifact on iOS.

Conclusion: **byte-level Mach-O patching cannot get an unmodified macOS
binary running on a non-jailbroken iPhone.** This isn't a matter of a
smarter patch — it's a trust-chain and packaging requirement enforced
in-kernel and in the installer, with no user-facing override. The tooling
built for this is kept in the repo (`tools/`, `tests/`, `fixtures/`) because
it's correct, tested, and directly demonstrates *why* the naive approach
fails — useful both as a reference and in case jailbreak ever becomes
available for this device.

## 3. What remains viable: source-level porting, still 100% native ARM64

Given the constraints above, the only path to running your own code on this
iPhone without a VM/emulator and without jailbreak is:

1. **You need the source** of the CLI logic (yours, or an open-source tool
   whose dependencies are POSIX/Foundation-level, not AppKit/Cocoa-level).
2. **Recompile it for `arm64-apple-ios`** instead of `arm64-apple-macos`.
   This is still native ARM64 machine code — nothing is emulated or
   translated; only the target triple (and therefore which syscalls/ABI
   version/available frameworks apply) changes.
3. **Wrap it in a minimal iOS app bundle** (a thin SwiftUI/UIKit shell whose
   only job is to call into the ported code and show its output) — this is
   the smallest unit iOS will actually install and launch.
4. **Sign it with your own Apple ID** through Xcode ("personal team," free,
   renews every 7 days, no paid Developer Program required for local device
   testing) and install directly over USB/Wi-Fi via Xcode's Run button.

This is a **port**, not a **binary-compatibility layer**: the tool's logic
runs unchanged, but it must be *rebuilt* against the iOS target, and it must
be *hosted* inside an app process rather than run as a freestanding
executable. That's the minimum true cost of "no jailbreak."

### Build requirement this session cannot satisfy directly

Building and signing an actual installable `.app` and running it on a
physical iPhone requires **Xcode on a Mac** (for the iOS SDK, the code
signing/provisioning machinery, and the USB/Wi-Fi device-install pipeline).
This sandbox is Linux with no Apple toolchain beyond a stock `clang`/`lld`
(no iOS SDK, no `xcrun`, no Swift compiler, no `codesign`). Concretely, that
means:

- I *can* and did validate that portable C source compiles cleanly to an
  arm64 object file under `-target arm64-apple-ios15.0` here (proves the
  core logic is target-portable at the object-code level).
- I *cannot* link a real iOS executable (no libSystem stubs from the SDK),
  compile the Swift/SwiftUI shell, produce a signed `.app`, or install/run
  anything on your iPhone from here. Those steps are described precisely
  in `docs/xcode-setup.md` for you to run on your own Mac.

## 4. JIT, revisited

JIT entitlements (`com.apple.security.cs.allow-jit`, or the debugger-attach
trick some emulator/browser apps use) only relax W^X *inside a process iOS
has already agreed to launch* — they let that process later mmap
writable+executable pages. They do nothing for the launch-authorization
problem above; a binary iOS refuses to install/launch doesn't get any
closer to running by also asking for JIT rights. JIT only becomes relevant
once you have a properly signed, running iOS app whose *own logic* needs to
generate and execute code at runtime (a script interpreter, a bytecode VM,
an emulator core) — out of scope for "run a small ARM64 CLI," and not
needed for the porting path in section 3 either, since recompiled native
code doesn't self-modify.

## 5. iOS restrictions inventory (for reference)

- **AMFI / code signing**: every executable page must be covered by a
  CodeDirectory whose CMS signature chains to a trust root iOS accepts.
  No user override without jailbreak.
- **No general-purpose exec surface**: no shell with rights to launch
  arbitrary installed-or-not binaries; only SpringBoard → `installd` →
  app-bundle launch.
- **Sandbox**: every app runs in a per-app container; filesystem, network,
  IPC, and device access are entitlement- and profile-gated regardless of
  what the recompiled code tries to do.
- **Framework availability**: no AppKit/Cocoa in iOS's shared cache; only
  UIKit-family APIs. A GUI port needs an actual UI rewrite, not just a
  recompile, unless the app's UI layer is trivial or Foundation-only.
- **JIT**: default-deny; irrelevant to the launch problem, only relevant to
  a properly-launched app that itself wants to generate code at runtime.

## 6. Revised minimum milestone

See `MILESTONES.md`. Summary: M1 ports a trivial "hello" CLI's logic
(`port/CLICore/`) into a minimal SwiftUI shell (`port/MaciOSPortApp/`),
buildable and installable only via Xcode on your Mac — that's the smallest
possible "your native ARM64 code, running on this specific iPhone, no VM,
no emulator, no jailbreak" milestone. Later milestones scale up to a real
CLI tool of your choosing, then (much further out, flagged as a
large/likely-out-of-scope undertaking) a GUI port.
