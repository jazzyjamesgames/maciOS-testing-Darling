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
# On a real Mac this needs no special linker flag: Xcode's own ld64
# handles -target arm64-apple-macos11 natively. EXTRA_CLANG_FLAGS exists
# only so this same script can be exercised in environments without
# Apple's toolchain (this project's own Linux sandbox, where
# `EXTRA_CLANG_FLAGS=-fuse-ld=lld` is what makes it work) -- leave it
# unset on macOS/CI.
set -e
cd "$(dirname "$0")"

MACOS_BIN="../fixtures/macos_payload_macos_arm64"
IOS_BIN="/tmp/macos_payload_ios_$$"
OUT="PatchedMacOSPayload.dylib"

clang -target arm64-apple-macos11 -nostdlib ${EXTRA_CLANG_FLAGS:-} \
    -Wl,-e,__start -Wl,-platform_version,macos,11.0,11.0 \
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
