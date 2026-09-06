#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Invokes process-host/ for real, from the running app, and reports back
// whether it got a genuinely separate OS process. See ProcessHostTester.m
// for why this is scoped to exactly that question -- pid/cancellation
// only, not the exitCode reply path -- and docs/process-host.md for the
// full mechanism.
typedef void (^MaciOSProcessHostResult)(BOOL launched, int pid, NSString * _Nullable message);

// Fires later than completion above, if at all -- separately, over the
// private NSExtensionContext "auxiliary connection" reply channel found by
// introspecting NSExtension/NSExtensionContext's real method surfaces (see
// docs/process-host.md's "Investigating the reply-channel question").
// received==NO means process-host/main.m either never attempted the reply
// (older build) or the channel didn't work as hypothesized -- this is the
// actual open question being tested, not assumed to work.
typedef void (^MaciOSProcessHostExitCode)(BOOL received, int exitCode, NSString * _Nullable error);

// frameworksRelativePath is resolved against Bundle.main.privateFrameworksURL,
// e.g. "TestPayload.framework/TestPayload" (Xcode-compiled control) or
// "PatchedMacOSPayload.dylib" (the actual macho_patch.py pipeline result --
// see test-payload/build-and-patch.sh).
void MaciOSTestProcessHost(NSString *frameworksRelativePath,
                           NSString *entryPoint,
                           MaciOSProcessHostResult completion,
                           MaciOSProcessHostExitCode exitCodeCompletion);

NS_ASSUME_NONNULL_END
