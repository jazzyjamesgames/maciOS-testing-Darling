// A stand-in for "an existing, already-compiled macOS CLI tool you don't
// have (or don't need) the source recompiled for" -- built as a normal
// arm64-apple-macos executable, then run through tools/macho_patch.py's
// full pipeline (patch --platform ios, then dylibify) exactly as a real
// foreign binary would be. Unlike test-payload/TestPayload.c (compiled
// straight for iOS by Xcode, as its own control), the whole point of this
// file is that it is NEVER compiled for iOS at all -- only for macOS.
//
// Exports one function process-host/ can dlsym and call after patching.
// _start exists only because a linked Mach-O executable needs SOME entry
// point to be valid in the first place; process-host/ never calls it, and
// dylibify's conversion to MH_DYLIB makes the point moot for execution
// purposes regardless (a leftover LC_MAIN pointing at it is expected --
// see test-payload/build-and-patch.sh for whether that turns out to
// matter to dyld on real hardware, since that's genuinely unverified here).
__attribute__((visibility("default")))
int maciOS_patched_payload_entry(void) {
    return 99;
}

void _start(void) {}
