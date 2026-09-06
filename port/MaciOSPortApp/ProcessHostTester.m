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
#import "../../process-host/MaciOSProcessHostReply.h"
#import <objc/runtime.h>

@interface NSExtension : NSObject
+ (instancetype)extensionWithIdentifier:(NSString *)identifier error:(NSError **)error;
- (void)beginExtensionRequestWithInputItems:(NSArray *)items completion:(void (^)(NSUUID *))callback;
// Found by introspecting NSExtension's real method surface (see
// docs/process-host.md) -- the listenerEndpoint variant that gets this
// experiment its reply channel. Not used by ios18-probe/LiveContainer,
// which only ever needed the plain completion: form above.
- (void)beginExtensionRequestWithInputItems:(NSArray *)items
                            listenerEndpoint:(id)listenerEndpoint
                                  completion:(void (^)(NSUUID *))callback;
- (int)pidForRequestIdentifier:(NSUUID *)identifier;
- (void)setRequestCancellationBlock:(void (^)(NSUUID *uuid, NSError *error))callback;
@end

// The host-side end of the auxiliary-connection reply channel: accepts the
// one incoming NSXPCConnection process-host/main.m opens back using the
// listenerEndpoint we hand into beginExtensionRequestWithInputItems:
// listenerEndpoint:completion:, and forwards whatever it reports to
// exitCodeCompletion. Kept alive past MaciOSTestProcessHost's return via
// objc_setAssociatedObject on the NSExtension instance below -- it has to
// be, since the whole point is this fires *after* this function returns.
@interface MaciOSProcessHostReplyReceiver : NSObject <NSXPCListenerDelegate, MaciOSProcessHostReply>
@property(nonatomic, copy) MaciOSProcessHostExitCode completion;
@property(nonatomic, strong) NSXPCListener *listener;
@end

@implementation MaciOSProcessHostReplyReceiver

- (BOOL)listener:(NSXPCListener *)listener shouldAcceptNewConnection:(NSXPCConnection *)newConnection {
  newConnection.exportedInterface = [NSXPCInterface interfaceWithProtocol:@protocol(MaciOSProcessHostReply)];
  newConnection.exportedObject = self;
  [newConnection resume];
  return YES;
}

- (void)processHostDidFinishWithPID:(pid_t)pid exitCode:(int)exitCode error:(NSString *)error {
  if (self.completion) {
    self.completion(YES, exitCode, error);
  }
}

@end

// completion fires on launch, using the two signals ios18-probe already
// confirmed work (pidForRequestIdentifier:, the cancellation block for
// failure) -- the load-bearing claim behind the whole approach, confirmed
// on-device already (see MILESTONES.md).
//
// exitCodeCompletion is the actual experiment: an NSXPCListener built here,
// its endpoint handed into beginExtensionRequestWithInputItems:
// listenerEndpoint:completion:, on the theory (from introspecting
// NSExtension/NSExtensionContext's real method surfaces -- see
// docs/process-host.md) that process-host/main.m can retrieve that same
// endpoint via NSExtensionContext's private _auxiliaryListener and connect
// back through it directly, bypassing completeRequestReturningItems:
// entirely (which NSExtension's dump confirmed has no host-side accessor
// at all). If the hypothesis is wrong, exitCodeCompletion simply never
// fires -- that's itself the answer, not a crash or a hang.
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

  NSExtensionItem *item = [NSExtensionItem new];
  item.userInfo = @{
    @"dylibPath" : dylibPath ?: @"",
    @"entryPoint" : entryPoint,
  };

  [ext setRequestCancellationBlock:^(NSUUID *uuid, NSError *cancelError) {
    completion(NO, 0, [NSString stringWithFormat:@"request cancelled (process likely died): %@",
                                                  cancelError]);
  }];

  MaciOSProcessHostReplyReceiver *receiver = [MaciOSProcessHostReplyReceiver new];
  receiver.completion = exitCodeCompletion;
  NSXPCListener *listener = [NSXPCListener anonymousListener];
  listener.delegate = receiver;
  receiver.listener = listener;
  [listener resume];

  // ext already outlives this function's return via the retain cycle its
  // own captured completion/cancellation blocks create (a pre-existing
  // pattern, not new here) -- piggyback the receiver on that same
  // lifetime rather than inventing a second one.
  static const void *kReplyReceiverKey = &kReplyReceiverKey;
  objc_setAssociatedObject(ext, kReplyReceiverKey, receiver, OBJC_ASSOCIATION_RETAIN);

  [ext beginExtensionRequestWithInputItems:@[ item ]
                          listenerEndpoint:listener.endpoint
                                completion:^(NSUUID *requestIdentifier) {
    if (!requestIdentifier) {
      completion(NO, 0, @"beginExtensionRequestWithInputItems: returned a nil identifier");
      return;
    }
    int pid = [ext pidForRequestIdentifier:requestIdentifier];
    completion(YES, pid, nil);
  }];
}
