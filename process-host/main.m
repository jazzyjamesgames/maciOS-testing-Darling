// ProcessHost -- a generic "run this payload in a real, separate OS
// process" extension. See docs/process-host.md for the full picture; in
// short, this is a deliberately scoped-down version of LiveContainer's
// LiveProcess component (github.com/LiveContainer/LiveContainer):
//
//   - Same extension-point trick (Info.plist: com.apple.ar.viewer,
//     _MultipleInstances, _ProcessType "App") to get a genuine separate,
//     app-class OS process without calling posix_spawn (which, per
//     AUDIT.md, measures as EPERM inside the app sandbox regardless).
//
//   - UNLIKE LiveContainer, this does NOT shadow UIApplicationMain or hook
//     dlopen to intercept UIKit's own load. LiveContainer needs that
//     because its spawned processes have to behave like a full guest iOS
//     app (open their own UIWindowScene, etc.) for "nativeWindow"
//     multitasking. A ported CLI payload doesn't need any of that -- it
//     just needs to run as a real process and report a result back, which
//     the ordinary, undisturbed NSExtensionRequestHandling protocol
//     already does. Less machinery, less to get wrong.
//
// Payload contract: the caller passes the extension request an
// NSExtensionItem whose userInfo carries:
//   "dylibPath"   -- absolute path to a Mach-O already converted with
//                    tools/macho_patch.py dylibify (MH_EXECUTE -> MH_DYLIB,
//                    platform tag already flipped to iOS) and, if it's
//                    going through an app bundle SideStore will resign,
//                    left otherwise unsigned (see AUDIT.md section 2).
//   "entryPoint"  -- the C symbol name of an exported `int name(void)`
//                    function in that dylib. No argv/envp passing in this
//                    minimal form; a payload that needs arguments can read
//                    them out of a file path passed via a second userInfo
//                    key instead, since app-extension launches don't carry
//                    a shell-style argv anyway.
//
// Reply: one NSExtensionItem back, userInfo carrying:
//   "pid"         -- getpid() of this process, so the caller can confirm
//                    it really is a separate PID
//   "exitCode"    -- the payload function's return value, present only on
//                    success
//   "error"       -- a human-readable failure string, present only on
//                    failure (dlopen/dlsym failure, or a crash is simply
//                    the process dying -- there is no way to catch that
//                    from here, same limitation noted throughout
//                    ios18-probe's own probe app)
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <unistd.h>

typedef int (*ProcessHostEntry)(void);

@interface ProcessHostHandler : NSObject <NSExtensionRequestHandling>
@end

@implementation ProcessHostHandler

- (void)beginRequestWithExtensionContext:(NSExtensionContext *)context {
  NSDictionary *request = [context.inputItems.firstObject userInfo];
  NSString *dylibPath = request[@"dylibPath"];
  NSString *entryPointName = request[@"entryPoint"];

  NSMutableDictionary *reply = [NSMutableDictionary dictionary];
  reply[@"pid"] = @(getpid());

  void (^finish)(void) = ^{
    NSExtensionItem *replyItem = [NSExtensionItem new];
    replyItem.userInfo = reply;
    [context completeRequestReturningItems:@[ replyItem ] completionHandler:nil];
  };

  if (!dylibPath.length || !entryPointName.length) {
    reply[@"error"] = @"missing dylibPath or entryPoint in the extension request";
    finish();
    return;
  }

  dlerror();  // clear any stale error before the calls below
  void *handle = dlopen(dylibPath.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
  if (!handle) {
    const char *err = dlerror();
    reply[@"error"] = [NSString stringWithFormat:@"dlopen(%@) failed: %s",
                                                  dylibPath, err ? err : "(no error string)"];
    finish();
    return;
  }

  ProcessHostEntry entry = (ProcessHostEntry)dlsym(handle, entryPointName.UTF8String);
  if (!entry) {
    const char *err = dlerror();
    reply[@"error"] = [NSString stringWithFormat:@"dlsym(%@) failed: %s",
                                                  entryPointName, err ? err : "(no error string)"];
    finish();
    return;
  }

  // If this crashes, the process dies and the caller sees the extension
  // request get cancelled -- there is no signal to add here for that case,
  // same limitation documented in ios18-probe's own probe app.
  int exitCode = entry();
  reply[@"exitCode"] = @(exitCode);
  finish();
}

@end

// Deliberately no main() here. Foundation's real NSExtensionMain is the
// correct process entry point for an app extension, and its exact
// signature isn't in any public header (LiveContainer's own code resolves
// it with dlsym(RTLD_NEXT, "NSExtensionMain") rather than declaring it) --
// writing a hand-written main() that calls it by a guessed signature risks
// an ABI mismatch this sandbox has no way to catch, having neither the
// iOS SDK nor a device to test against. The correct, already-proven way to
// wire this up (confirmed working in ios18-probe's LaunchHelper) is to let
// Xcode's App Extension target template handle it: create this as an
// extension target (not a plain executable), which sets the linker entry
// point to _NSExtensionMain automatically, and drop only this file's
// ProcessHostHandler class in as NSExtensionPrincipalClass. If hand-rolling
// the target instead of using the template, the equivalent is an Other
// Linker Flags entry of -Wl,-e,_NSExtensionMain with no main() in the
// target at all. See docs/process-host.md.
