#!/bin/sh
# Produces loader/target -- the static arm64 Mach-O executable
# macho_loader.c parses, maps, and jumps into. Reuses fixtures/hello.s (see
# tests/test_macho_patch.py's build_fixture() for the analogous sandbox-side
# invocation): direct `svc #0x80` syscalls only, no dyld-resolved external
# symbols -- exactly the target shape this milestone needs, already proven
# to build cleanly rather than a new near-duplicate fixture.
#
# -lSystem is required on the real macOS CI runner's actual ld64, same
# finding as test-payload/build-and-patch.sh: "dynamic executables or
# dylibs must link with libSystem.dylib" without it. This adds one
# LC_LOAD_DYLIB load command but does not inject any code into __TEXT --
# hello.s never references an external symbol, so there's nothing for the
# linker to bind, and the hand-written instruction bytes this milestone's
# svc-scan/entry-point logic cares about are unaffected either way. This
# script can't be fully exercised in a Linux sandbox for the same reason
# test-payload/build-and-patch.sh can't (no libSystem.tbd stub without the
# real SDK) -- see docs/custom-loader.md for how the structural findings
# there were validated instead (a separate, -nostdlib/no-libSystem/lld
# build, sandbox-only, structurally equivalent in every way this milestone
# depends on).
set -e
cd "$(dirname "$0")"

clang -target arm64-apple-macos11 -nostdlib \
    -Wl,-e,__start -Wl,-platform_version,macos,11.0,11.0 -lSystem \
    ../fixtures/hello.s -o target
echo "built loader/target (from fixtures/hello.s)"
