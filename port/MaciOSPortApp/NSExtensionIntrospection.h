#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Logs a named class's full real method surface (every selector, with its
// raw Objective-C type encoding) via the given logger -- "ask the runtime
// directly" instead of guessing at signatures, the same technique
// ios18-probe used against CoreSimulator's real Objective-C surface
// ("ground truth from the actual loaded binary, not reverse-engineered").
void MaciOSIntrospectClass(NSString *className, void (^log)(NSString *line));

// NSExtension: see docs/process-host.md's reply-channel section for why.
// Our own forward declaration (in ProcessHostTester.m) only has the
// methods LiveContainer/ios18-probe found and used --
// beginExtensionRequestWithInputItems:completion:, pidForRequestIdentifier:,
// setRequestCancellationBlock: -- and it was a real, open question whether
// a distinct method exists for "the extension completed and handed back
// reply items." The dump answered that: no such accessor exists, but it
// surfaced beginExtensionRequestWithInputItems:listenerEndpoint:completion:
// as a lead -- an NSXPCListenerEndpoint passed into the request, which
// would need NSExtensionContext (below) to retrieve on the extension side.
void MaciOSIntrospectNSExtension(void (^log)(NSString *line));

// NSExtensionContext: the class process-host/main.m's ProcessHostHandler
// actually receives in beginRequestWithExtensionContext:. Dumped for the
// same reason as NSExtension above -- specifically to find out whether/how
// it exposes the NSXPCListenerEndpoint that
// beginExtensionRequestWithInputItems:listenerEndpoint:completion: (found
// on NSExtension) passes in, before assuming any particular accessor name.
void MaciOSIntrospectNSExtensionContext(void (^log)(NSString *line));

NS_ASSUME_NONNULL_END
