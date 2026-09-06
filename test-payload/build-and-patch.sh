#!/bin/sh
# Produces test-payload/PatchedMacOSPayload.dylib -- the real end-to-end
# M2 test artifact. Unlike TestPayload.c (compiled straight for iOS by
# Xcode as a control), this script:
#
#   1. Compiles fixtures/macos_payload.c for arm64-apple-macos -- a real
#      macOS Mach-O, never targeting iOS at any point.
#   2. Runs it through tools/macho_patch.py's actual pipeline: patch the
#      platform tag to iOS, then dylibify (MH_EXECUTE -> MH_DYLIB).
#   3. Confirms the entry point process-host/ needs survived, using the
#      same check tools/macho_patch.py symbols exposes for exactly this
#      purpose -- fails loudly here rather than shipping a payload with a
#      missing symbol and finding out only after an install round trip.
#
# Not gitignored input, gitignored output: the .dylib this produces
# (test-payload/PatchedMacOSPayload.dylib) is a build artifact, regenerated
# by this script -- same pattern as fixtures/hello_macos_arm64 and
# fixtures/macos_payload_macos_arm64. project.yml's MaciOSPortApp target
# embeds it via a Copy Files build phase, so this script has to run BEFORE
# `xcodegen generate` (see .github/workflows/build.yml).
#
# -lSystem is required, not optional: confirmed directly against a real
# macOS CI runner's actual ld64, which refuses with "dynamic executables
# or dylibs must link with libSystem.dylib" without it -- a policy check
# Apple's real linker enforces that lld (used for this project's Linux-
# sandbox validation, which has no libSystem to link against at all) does
# not. Nothing in fixtures/macos_payload.c actually calls into libSystem;
# this is here purely to satisfy that static check, and -nostdlib still
# suppresses the default CRT startup files, so the custom _start entry
# point below remains the real one.
#
# EXTRA_CLANG_FLAGS exists for other overrides if ever needed, but note
# this script cannot be fully exercised end to end in a Linux sandbox any
# more regardless of linker choice, now that it links a real system
# library: there's no libSystem.tbd stub to link against without the
# actual macOS SDK. tests/test_macho_patch.py's own fixture-building
# (lld, -nostdlib, no -lSystem) is a separate, sandbox-only concern that
# validates the Python patching logic on a real Mach-O -- it doesn't need
# to match this script byte-for-byte, and isn't affected by this change.
set -e
cd "$(dirname "$0")"

MACOS_BIN="../fixtures/macos_payload_macos_arm64"
IOS_BIN="/tmp/macos_payload_ios_$$"
OUT="PatchedMacOSPayload.dylib"

clang -target arm64-apple-macos11 -nostdlib ${EXTRA_CLANG_FLAGS:-} \
    -Wl,-e,__start -Wl,-platform_version,macos,11.0,11.0 -lSystem \
    ../fixtures/macos_payload.c -o "$MACOS_BIN"
echo "built $MACOS_BIN (macOS, never iOS)"

python3 ../tools/macho_patch.py patch "$MACOS_BIN" -o "$IOS_BIN" \
    --platform ios --min-os 15.0 --sdk 15.0

# Install name slot is always tiny for a normally-linked executable
# (LC_LOAD_DYLINKER names /usr/lib/dyld regardless of what the binary
# itself does) -- "p.dylib" is deliberately short, not arbitrary.
python3 ../tools/macho_patch.py dylibify "$IOS_BIN" -o "$OUT" \
    --install-name p.dylib --no-resign
rm -f "$IOS_BIN"

python3 ../tools/macho_patch.py symbols "$OUT" \
    --grep maciOS_patched_payload_entry --defined-only
echo "confirmed: maciOS_patched_payload_entry present and externally defined in $OUT"
