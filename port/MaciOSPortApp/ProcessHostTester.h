#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Invokes process-host/ for real, from the running app, and reports back
// whether it got a genuinely separate OS process. See ProcessHostTester.m
// for why this is scoped to exactly that question -- pid/cancellation
// only, not the exitCode reply path -- and docs/process-host.md for the
// full mechanism.
typedef void (^MaciOSProcessHostResult)(BOOL launched, int pid, NSString * _Nullable message);

// frameworksRelativePath is resolved against Bundle.main.privateFrameworksURL,
// e.g. "TestPayload.framework/TestPayload" (Xcode-compiled control) or
// "PatchedMacOSPayload.dylib" (the actual macho_patch.py pipeline result --
// see test-payload/build-and-patch.sh).
void MaciOSTestProcessHost(NSString *frameworksRelativePath,
                           NSString *entryPoint,
                           MaciOSProcessHostResult completion);

NS_ASSUME_NONNULL_END
