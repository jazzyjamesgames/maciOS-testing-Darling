// Invokes process-host/ using NSExtension -- a real but undocumented
// Foundation class (no public header), so it has to be forward-declared
// here rather than imported. This exact set of methods is the one
// ios18-probe's own Probe app confirmed working on real hardware ("LAUNCHED
// as a real separate process. uuid=%@ pid=%d"), adapted from LiveContainer's
// FoundationPrivate.h -- reused verbatim rather than guessed at, since this
// is the one part of this whole pipeline with no public documentation to
// fall back on if the shape is wrong.
//
// Parameterized over which payload to load so the same invocation code
// serves two different tests: TestPayload.framework/TestPayload (compiled
// straight for iOS by Xcode, a control -- proves the process-host
// mechanism itself works) and PatchedMacOSPayload.dylib (a real macOS
// binary run through tools/macho_patch.py's actual patch+dylibify
// pipeline -- proves the thing M2 is actually about).
#import "ProcessHostTester.h"
#import "../../process-host/MaciOSProcessHostDarwinReply.h"
#import <CoreFoundation/CoreFoundation.h>
#import <objc/runtime.h>

@interface NSExtension : NSObject
+ (instancetype)extensionWithIdentifier:(NSString *)identifier error:(NSError **)error;
- (void)beginExtensionRequestWithInputItems:(NSArray *)items completion:(void (^)(NSUUID *))callback;
- (int)pidForRequestIdentifier:(NSUUID *)identifier;
- (void)setRequestCancellationBlock:(void (^)(NSUUID *uuid, NSError *error))callback;
@end

// The host-side end of the Darwin-notification reply channel: registers
// for every Darwin notification system-wide (there is no way to filter
// server-side on a name that encodes a payload we don't know in advance)
// and filters by requestUUID prefix itself, per
// MaciOSProcessHostDarwinReply.h. Kept alive past MaciOSTestProcessHost's
// return via objc_setAssociatedObject on the NSExtension instance (which
// already outlives the call through its own captured blocks) -- the whole
// point is this fires *after* this function returns.
@interface MaciOSDarwinReplyObserver : NSObject
@property(nonatomic, copy) NSString *requestUUID;
@property(nonatomic, copy) MaciOSProcessHostExitCode completion;
- (void)start;
- (void)handleNotificationName:(NSString *)name;
@end

static void MaciOSDarwinNotifyTrampoline(CFNotificationCenterRef center, void *observerPtr,
                                          CFNotificationName name, const void *object,
                                          CFDictionaryRef userInfo) {
  MaciOSDarwinReplyObserver *observer = (__bridge MaciOSDarwinReplyObserver *)observerPtr;
  [observer handleNotificationName:(__bridge NSString *)name];
}

@implementation MaciOSDarwinReplyObserver

- (void)start {
  CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(),
                                   (__bridge const void *)self,
                                   MaciOSDarwinNotifyTrampoline,
                                   NULL,  // NULL name: observe everything, filter ourselves
                                   NULL,
                                   CFNotificationSuspensionBehaviorDeliverImmediately);
}

- (void)handleNotificationName:(NSString *)name {
  BOOL success = NO;
  int exitCode = 0;
  NSString *error = nil;
  if (!MaciOSParseProcessHostReplyName(name, self.requestUUID, &success, &exitCode, &error)) {
    return;  // not ours -- expected for almost every Darwin notification system-wide
  }
  CFNotificationCenterRemoveObserver(CFNotificationCenterGetDarwinNotifyCenter(),
                                      (__bridge const void *)self, NULL, NULL);
  if (self.completion) {
    self.completion(success, exitCode, error);
  }
}

@end

// completion fires on launch, using the two signals ios18-probe already
// confirmed work (pidForRequestIdentifier:, the cancellation block for
// failure) -- the load-bearing claim behind the whole approach, confirmed
// on-device already (see MILESTONES.md).
//
// exitCodeCompletion is the reply-channel experiment, currently on its
// third attempt: completeRequestReturningItems: has no host-side accessor
// at all (per NSExtension's own dumped method surface), and passing an
// NSXPCListenerEndpoint via beginExtensionRequestWithInputItems:
// listenerEndpoint:completion: was tested on-device and rejected outright
// by this extension point (see docs/process-host.md). This attempt uses
// CFNotificationCenterGetDarwinNotifyCenter() instead -- real, public,
// documented API that crosses the app/extension sandbox boundary without
// needing an App Group (which ios18-probe found doesn't survive
// SideStore's resign anyway). Not yet confirmed on-device.
void MaciOSTestProcessHost(NSString *frameworksRelativePath,
                           NSString *entryPoint,
                           MaciOSProcessHostResult completion,
                           MaciOSProcessHostExitCode exitCodeCompletion) {
  NSURL *plugInsURL = [[NSBundle mainBundle] builtInPlugInsURL];
  NSArray<NSURL *> *contents = [[NSFileManager defaultManager]
      contentsOfDirectoryAtURL:plugInsURL
     includingPropertiesForKeys:nil
                        options:0
                          error:nil];

  // The identifier declared in process-host/Info.plist is NOT what's
  // actually registered on-device: SideStore's resign inserts its own team
  // segment into the middle of it (confirmed directly -- installd reported
  // "dev.local.maciOS.PortApp.676TQUDKG7.ProcessHost" for a source
  // identifier of "dev.local.maciOS.PortApp.ProcessHost"). Reading it back
  // from the installed .appex's own Info.plist, rather than hardcoding the
  // design-time string, is the only way this matches regardless of what
  // any given install rewrote it to.
  NSURL *appexURL = nil;
  for (NSURL *url in contents) {
    if ([url.pathExtension isEqualToString:@"appex"]) {
      appexURL = url;
      break;
    }
  }
  if (!appexURL) {
    completion(NO, 0, @"no .appex found under builtInPlugInsURL");
    return;
  }

  NSDictionary *appexInfo = [NSDictionary
      dictionaryWithContentsOfURL:[appexURL URLByAppendingPathComponent:@"Info.plist"]];
  NSString *identifier = appexInfo[@"CFBundleIdentifier"];
  if (!identifier) {
    completion(NO, 0, @"installed .appex Info.plist has no CFBundleIdentifier");
    return;
  }

  NSError *error = nil;
  NSExtension *ext = [NSExtension extensionWithIdentifier:identifier error:&error];
  if (!ext) {
    completion(NO, 0, [NSString stringWithFormat:@"extensionWithIdentifier:%@ failed: %@",
                                                  identifier, error]);
    return;
  }

  NSString *dylibPath = [[[NSBundle mainBundle] privateFrameworksURL]
      URLByAppendingPathComponent:frameworksRelativePath].path;

  // Our own request UUID, not the one beginExtensionRequestWithInputItems:
  // completion:'s callback later hands back -- generated up front so we
  // can register the Darwin notification observer before the extension
  // could possibly have anything to reply with, and passed through so
  // process-host/main.m can address its reply to exactly this call.
  NSString *requestUUID = [[NSUUID UUID] UUIDString];

  NSExtensionItem *item = [NSExtensionItem new];
  item.userInfo = @{
    @"dylibPath" : dylibPath ?: @"",
    @"entryPoint" : entryPoint,
    @"requestUUID" : requestUUID,
  };

  MaciOSDarwinReplyObserver *observer = [MaciOSDarwinReplyObserver new];
  observer.requestUUID = requestUUID;
  observer.completion = exitCodeCompletion;
  [observer start];

  // ext already outlives this function's return via the retain cycle its
  // own captured completion/cancellation blocks create (a pre-existing
  // pattern, not new here) -- piggyback the observer on that same
  // lifetime rather than inventing a second one.
  static const void *kReplyObserverKey = &kReplyObserverKey;
  objc_setAssociatedObject(ext, kReplyObserverKey, observer, OBJC_ASSOCIATION_RETAIN);

  [ext setRequestCancellationBlock:^(NSUUID *uuid, NSError *cancelError) {
    completion(NO, 0, [NSString stringWithFormat:@"request cancelled (process likely died): %@",
                                                  cancelError]);
  }];

  [ext beginExtensionRequestWithInputItems:@[ item ]
                                 completion:^(NSUUID *requestIdentifier) {
    if (!requestIdentifier) {
      completion(NO, 0, @"beginExtensionRequestWithInputItems: returned a nil identifier");
      return;
    }
    int pid = [ext pidForRequestIdentifier:requestIdentifier];
    completion(YES, pid, nil);
  }];
}
