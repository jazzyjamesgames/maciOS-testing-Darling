# Syscall table diff: macOS arm64 vs iOS arm64, for `exit`/`write`

## The calling convention (both platforms, identical)

ARM64 Darwin (both macOS and iOS) uses:

- `svc #0x80` to trap into the kernel for BSD/"Unix" syscalls (as opposed
  to Mach traps, which use negative numbers and a different immediate
  convention). Confirmed directly against real compiled bytes -- see
  `docs/custom-loader.md`.
- `x16` holds the syscall number (positive for BSD syscalls; Mach traps
  use `x16` = the trap number's negation instead).
- Arguments in `x0`-`x7`, standard AAPCS64 register convention.
- Return value in `x0`; the carry flag (`PSTATE.C`) is set on error, with
  `x0` holding `errno` in that case (the convention `libSystem`'s C
  wrappers check to decide whether to return `-1` and set `errno`, or
  return `x0` directly on success).

## The two syscalls this milestone's target binary uses

| Syscall | Number (`x16`) | Args | macOS arm64 | iOS arm64 |
|---|---|---|---|---|
| `exit`  | 1 | `x0`=status | same | same |
| `write` | 4 | `x0`=fd, `x1`=buf, `x2`=len | same | same |

**There is no ABI difference for either syscall.** Same trap immediate,
same syscall number, same register convention, same argument order, same
return convention. This isn't a coincidence or an oversight worth
patching around -- it's a direct consequence of macOS and iOS sharing the
same kernel lineage (XNU) and the same syscall table source
(`bsd/kern/syscalls.master` in Apple's own open-source XNU tree), compiled
for the same CPU architecture. Darling has to translate syscalls at all
because it's bridging XNU semantics onto a genuinely different kernel
(Linux); nothing here is bridging between different kernels, so nothing
needs a syscall-number/ABI translation layer for calls like these.

This is also, concretely, *why* `AUDIT.md`'s M1/M2 milestones succeed with
nothing more than a platform-tag patch and a real signing identity: the
kernel underneath is already speaking the exact same syscall dialect the
binary was compiled for. A byte-patched macOS binary's raw instructions
--- its `svc #0x80` calls included --- are already correct for iOS,
unmodified, with zero translation.

## Where the real platform difference actually lives

Not in the syscall table -- in **sandbox/MAC-framework policy**, enforced
*after* a syscall number is dispatched, by a layer that can and does
behave differently per platform even though the underlying syscall ABI is
identical. The concrete, already-confirmed example from this project:
`posix_spawn()` measures as `EPERM` inside an iOS app sandbox (`AUDIT.md`),
despite `posix_spawn`'s syscall number/ABI being exactly the same on
macOS. The kernel accepts the same trap, dispatches to the same syscall
number, and then the Sandbox/Seatbelt policy module attached to the
calling process's entitlements refuses it -- a decision made by policy
code layered on top of an identical mechanism, not by the mechanism
itself differing.

This matters directly for this milestone's own stated next step
("groundwork for a future JIT-based patch/redirect once on-device"):
**patching/redirecting `write`/`exit` themselves has no ABI gap to
bridge** -- there's nothing to translate, because both platforms already
agree on what these calls mean. The open, on-device question for a
future milestone isn't "does the target syscall number differ" (it
doesn't, for anything in `exit`/`write`'s general class of ordinary BSD
syscalls) -- it's whether the SPECIFIC syscall a redirect target cares
about is one of the ones sandboxed *third-party* iOS processes are
restricted from at the policy layer (`posix_spawn`/`fork`/`ptrace`/
`task_for_pid` and similar are confirmed or strongly expected to be;
plain `write`/`read`/`open`-on-the-app's-own-sandbox-container are not).
That's a per-syscall policy question to check empirically on-device, not
a per-syscall ABI-translation question to solve in a loader.
