#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Invokes process-host/ for real, from the running app, and reports back
// whether it got a genuinely separate OS process. See ProcessHostTester.m
// for why the launch signal (this completion) is kept separate from the
// exitCode reply below, and docs/process-host.md for the full mechanism
// and the two reply-channel approaches already tried and ruled out before
// landing on Darwin notifications.
typedef void (^MaciOSProcessHostResult)(BOOL launched, int pid, NSString * _Nullable message);

// Fires later than completion above, if at all -- separately, over
// CFNotificationCenterGetDarwinNotifyCenter(), real public API (unlike the
// two private-API mechanisms already tried and ruled out -- see
// docs/process-host.md). received==NO means process-host/main.m never
// posted a reply within this call's lifetime; that's a real, observable
// outcome to log, not assumed to always fire.
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
