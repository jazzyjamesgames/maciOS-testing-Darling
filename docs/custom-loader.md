# A custom Mach-O loader (not dyld): Milestone 1

## Goal

Prove that a minimal, statically-linked macOS arm64 binary can be loaded
by a custom-written loader (not dyld) and have its syscalls statically
located, on paper/in CI, validated as far as possible before any on-device
work. This is a separate track from `port/`/`process-host/` (M1-M3): those
rely on the OS's own normal load path (Xcode/dyld, or the extension-launch
trick) plus a real signing identity; this asks a different question --
can *our own code*, not dyld, be the thing that maps and executes a
Mach-O, as groundwork for eventually intercepting/redirecting its
syscalls at load time.

## The target binary

`fixtures/hello.s` (already used by M1/M2's own tests) is reused rather
than a new near-duplicate fixture: no libSystem, no dyld-resolved external
symbols, direct `svc #0x80` syscalls only --

```asm
__start:
    mov x0, #1          // fd = STDOUT_FILENO
    adrp x1, msg@PAGE
    add x1, x1, msg@PAGEOFF
    mov x2, #31
    mov x16, #4         // SYS_write
    svc #0x80

    mov x0, #0          // exit status
    mov x16, #1         // SYS_exit
    svc #0x80
```

`loader/build_target.sh` builds it (`clang -target arm64-apple-macos11
-nostdlib -Wl,-e,__start ... -lSystem fixtures/hello.s -o loader/target`).
`-lSystem` is required on a real macOS `ld64` (same finding as
`test-payload/build-and-patch.sh`) but adds only one `LC_LOAD_DYLIB`
command -- `hello.s` never references an external symbol, so nothing gets
bound and the hand-written instruction bytes below are unaffected.

## Structure, fully documented (`loader/dump_structure.py`)

Run against a structurally-equivalent build (same source, `-nostdlib`,
`lld`, no `-lSystem` -- the only variant this sandbox can actually produce
without a real macOS SDK; see "What's validated where" below):

```
mach_header_64: cputype=0x100000c cpusubtype=0x0 filetype=2 ncmds=13 sizeofcmds=688 flags=0x200085
LC_SEGMENT_64 __PAGEZERO   vmaddr=0x0 vmsize=0x100000000 fileoff=0x0 filesize=0x0 maxprot=0 initprot=0 nsects=0
LC_SEGMENT_64 __TEXT       vmaddr=0x100000000 vmsize=0x4000 fileoff=0x0 filesize=0x4000 maxprot=5 initprot=5 nsects=2
  section __text       addr=0x1000002f0 size=0x24 offset=0x2f0 align=2
  section __const      addr=0x100000314 size=0x1f offset=0x314 align=0
LC_SEGMENT_64 __LINKEDIT   vmaddr=0x100004000 vmsize=0x1b0 fileoff=0x4000 filesize=0x1b0 maxprot=1 initprot=1 nsects=0
LC_LOAD_DYLINKER: /usr/lib/dyld
LC_MAIN entryoff=0x2f0 stacksize=0x0
entry point vmaddr = __TEXT.vmaddr + entryoff = 0x100000000 + 0x2f0 = 0x1000002f0
svc instructions found: 2
  vmaddr=0x100000304 imm=0x80
  vmaddr=0x100000310 imm=0x80
```

Cross-checked by hand before trusting the tool: `0xd4001001` (the word at
both svc sites) decodes as `svc #0x80` via the ARMv8 encoding
(`bits[31:21]=0b11010100000`, `bits[20:5]=imm16`, `bits[4:0]=0b00001`;
`0x80 << 5 = 0x1000`, and `0xD4000001 | 0x1000 = 0xD4001001`) -- not
assumed from a library, derived and confirmed against real compiled
bytes.

Key facts this milestone depends on:

- **`LC_MAIN`, not `LC_UNIXTHREAD`.** Modern `ld`/`lld` emit the simpler,
  offset-based entry point mechanism by default; `macho_loader.c` only
  handles this form (see "Known limitations").
- **The binary is PIE** (confirmed via `file`: `flags:<...|PIE>`).
  `vmaddr=0x100000000` is Apple's *preferred* load address, not one a
  loader is obligated to honor literally -- see "Design decisions" below.
- **`__PAGEZERO`** (`maxprot=0 initprot=0`, `filesize=0`) is a
  reservation-only hole meant to catch null-pointer accesses, never
  meant to be backed by real memory. Skipped entirely by
  `macho_loader.c` -- correct for this milestone's purposes (proving
  execution works), even though a fuller loader would still reserve
  (not populate) that address range.
- **`__LINKEDIT`** holds symbol/dysymtab tables a real dynamic linker
  needs to bind imported symbols. `hello.s` imports nothing, so nothing
  needs binding -- `macho_loader.c` doesn't map `__LINKEDIT` at all, and
  execution is unaffected. This is specific to this target being fully
  self-contained; a binary that actually imports symbols would need it.

## The loader (`loader/macho_loader.c`)

A normal macOS command-line tool (built by `loader/build_loader.sh`,
plain `clang`, no special flags -- it isn't the thing being loaded, it's
the loader). What it does, in order:

1. Reads the target file into its own heap and validates the header
   (`MH_MAGIC_64`, `CPU_TYPE_ARM64`).
2. Walks load commands once to find every `LC_SEGMENT_64` (recording the
   `__TEXT` segment's own `vmaddr` specifically, since `LC_MAIN`'s
   `entryoff` is relative to it) and `LC_MAIN`'s `entryoff`.
3. Reserves one anonymous, writable region sized to fit every segment's
   `vmaddr`..`vmaddr+vmsize` range and lets the OS place it -- see "Design
   decisions" for why this, not the file's literal preferred addresses.
4. Copies each segment's file bytes to `reserved_base + (vmaddr -
   lowest_vmaddr)`.
5. **Statically scans the mapped `__TEXT` for `svc` instructions**,
   printing each one's address and immediate -- the same bitmask check
   `loader/dump_structure.py` already validated in Python, reimplemented
   independently in C. This is *groundwork* for a future JIT-based
   patch/redirect once this runs on-device (see
   `docs/syscall-table-diff.md`) -- Milestone 1 only locates them, it does
   not intercept or redirect anything at runtime yet.
6. Calls `sys_icache_invalidate` on any segment with `VM_PROT_EXECUTE`,
   then `mprotect`s every segment to its declared `initprot` (dropping
   write access on `__TEXT` -- proper W^X).
7. Computes the real entry address (`slide + __TEXT.vmaddr + entryoff`)
   and calls it directly as a `void (*)(void)`.

If the parsing and mapping above are correct, step 7 hands control to
`hello.s`'s own `_start`, which makes its own real `write`/`exit`
syscalls, unintercepted -- proof positive if the loader process itself
prints `hello from native arm64 maciOS` on real stdout and exits with
status 0. **That is the correctness check** (task 6), not a separate
harness inspecting internals: `.github/workflows/build.yml` builds and
runs this on the real macOS CI runner and asserts on exactly that output.

## Design decisions

- **Reserve-then-slide, not honor-the-literal-vmaddr.** The target is
  PIE; a real loader (dyld) applies ASLR by choosing its own base and
  sliding every vmaddr by the same amount. This loader does the same
  thing, just computed by us: reserve one region anywhere the OS picks,
  compute `slide = reserved_base - lowest_vmaddr`, and add `slide` to
  every vmaddr uniformly (including the entry point). This is more
  correct/robust than `MAP_FIXED` at the literal `0x100000000` address,
  which risks colliding with something else already in this process's own
  address space.
- **`sys_icache_invalidate` before executing freshly-written code is not
  optional.** ARM64's instruction and data caches are not automatically
  coherent for self-modifying/JIT-written code the way x86's are --
  skipping this risks executing stale cache lines rather than the bytes
  just `memcpy`'d in. This is the same call every JIT compiler on Apple
  platforms makes for the same reason, included here even though this
  milestone's code never actually changes after the copy (worth doing
  correctly now, since a future milestone that patches svc instructions
  in place absolutely will need it).
- **`vm_prot_t`'s bit values equal POSIX `PROT_*` values on Darwin**
  (`VM_PROT_READ=1=PROT_READ`, `VM_PROT_WRITE=2=PROT_WRITE`,
  `VM_PROT_EXECUTE=4=PROT_EXEC`) -- confirmed against the dumped
  `initprot=5` (`__TEXT`, read+execute) above, so `mprotect(...,
  seg->initprot)` is exact, not an approximation needing bit remapping.

## What's validated where

This sandbox has no real Darwin SDK (no `<mach-o/loader.h>`, no
`<libkern/OSCacheControl.h>`) -- `macho_loader.c` itself can only be
compiled and actually run on a real macOS toolchain, same limitation
`test-payload/build-and-patch.sh` already documented for a different
reason. What *was* validated directly, here, before trusting any of the
above:

- The `svc` bit-encoding, derived from first principles and confirmed
  against real compiled bytes (`0xd4001001`, both sites).
- The full segment/section/entry-point layout, via
  `loader/dump_structure.py` (pure Python, reusing `tools/macho_patch.py`,
  fully testable in this sandbox -- see `tests/test_loader_dump_structure.py`).
- That `LC_MAIN` (not `LC_UNIXTHREAD`) is what `lld` actually emits for
  this exact build invocation -- read directly off the real compiled
  bytes, not assumed.

The one thing genuinely *not* validated anywhere yet is
`macho_loader.c`'s own compile and the actual jump-to-entry execution --
that's exactly what `.github/workflows/build.yml`'s new step is for: the
first real signal on whether the C code is correct.

## Known limitations (Milestone 1's own explicit scope)

- **The entry point is called as a plain `void(void)` function, not given
  a real process entry's initial register/stack state** (argc/argv/envp/
  apple-vector in `x0`-`x3`, a fresh stack, the way the kernel sets things
  up for a real `execve()`). This is fine specifically because `hello.s`'s
  `_start` never reads its incoming registers as arguments -- its first
  instruction overwrites `x0` immediately for its own `write()` call. A
  future, more realistic target that actually expects `argc`/`argv` would
  need this loader extended to fake up that initial state first.
- **`LC_UNIXTHREAD`-style entry points are not handled** -- only
  `LC_MAIN`. Fine for this target; would need extending for an older or
  differently-built binary.
- **No dynamic symbol binding at all.** Correct for `hello.s` (imports
  nothing); a binary with real `LC_LOAD_DYLIB`/undefined symbols would
  need `__LINKEDIT` parsed and a real (or stubbed) symbol resolver -- out
  of scope here, and exactly the kind of gap `tools/macho_patch.py`'s
  `check-deps`/`undefined_symbols_by_dependency` (M3 tooling) already
  exists to scope for a future real target.
- **Syscalls are not intercepted, only located.** See
  `docs/syscall-table-diff.md` for the ABI groundwork and why iOS's real
  restrictions (unlike this milestone's own scope) are a *policy*
  question, not an ABI one.
- **Not yet run on iOS at all.** This entire milestone runs as a normal
  macOS command-line tool on the CI runner. iOS's JIT/sandbox
  restrictions on writable+executable memory (`mmap`/`mprotect` with both
  `PROT_WRITE` and `PROT_EXEC`, which this loader's own W^X-respecting
  design already avoids doing *simultaneously*, but iOS's actual
  entitlement requirements for `mprotect(..., PROT_EXEC)` at all inside an
  app sandbox are unconfirmed) are explicitly flagged as on-device
  follow-up, not solved here.
