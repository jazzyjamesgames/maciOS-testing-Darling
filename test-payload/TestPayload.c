// A minimal native payload for exercising process-host/ end to end.
//
// Deliberately built as a real Xcode target (project.yml's TestPayload,
// type: framework) rather than run through tools/macho_patch.py: M2's
// actual milestone is patching an EXISTING foreign macOS binary, but that
// needs a real target binary to point at. This tests the process-host
// mechanism itself first -- does the com.apple.ar.viewer extension trick
// really hand back a separate, real PID, does dlopen/dlsym really work on
// something reachable at Frameworks/TestPayload.framework/TestPayload --
// decoupled from the separate question of whether a *foreign* binary's
// dependencies resolve on iOS (AUDIT.md section 5), which only matters
// once a specific real target is chosen.
int maciOS_test_entry(void) {
    return 42;
}
