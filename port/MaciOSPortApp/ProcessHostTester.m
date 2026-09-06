// Invokes process-host/ using NSExtension -- a real but undocumented
// Foundation class (no public header), so it has to be forward-declared
// here rather than imported. This exact set of methods is the one
// ios18-probe's own Probe app confirmed working on real hardware ("LAUNCHED
// as a real separate process. uuid=%@ pid=%d"), adapted from LiveContainer's
// FoundationPrivate.h -- reused verbatim rather than guessed at, since this
// is the one part of this whole pipeline with no public documentation to
// fall back on if the shape is wrong.
#import "ProcessHostTester.h"

@interface NSExtension : NSObject
+ (instancetype)extensionWithIdentifier:(NSString *)identifier error:(NSError **)error;
- (void)beginExtensionRequestWithInputItems:(NSArray *)items completion:(void (^)(NSUUID *))callback;
- (int)pidForRequestIdentifier:(NSUUID *)identifier;
- (void)setRequestCancellationBlock:(void (^)(NSUUID *uuid, NSError *error))callback;
@end

// Scoped deliberately narrow: this answers "did process-host/'s
// com.apple.ar.viewer extension trick get a genuinely separate real OS
// process at all" -- the load-bearing claim behind the whole approach --
// using only the two signals ios18-probe already confirmed work
// (pidForRequestIdentifier:, the cancellation block for failure). It does
// NOT attempt to read back process-host/main.m's exitCode/error reply
// payload: whether that reply channel (completeRequestReturningItems: on
// the extension side) is actually observable from here via any documented
// or proven mechanism is still an open question -- see docs/process-host.md.
// Better to ship a smaller, honestly-scoped test now than a bigger one
// built on an unverified assumption.
void MaciOSTestProcessHost(MaciOSProcessHostResult completion) {
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
      URLByAppendingPathComponent:@"TestPayload.framework/TestPayload"].path;

  NSExtensionItem *item = [NSExtensionItem new];
  item.userInfo = @{
    @"dylibPath" : dylibPath ?: @"",
    @"entryPoint" : @"maciOS_test_entry",
  };

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
