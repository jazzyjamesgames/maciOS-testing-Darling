#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Logs the REAL NSExtension class's full method surface (every selector,
// with its raw Objective-C type encoding) via the given logger. See
// docs/process-host.md's reply-channel section for why: our own forward
// declaration of NSExtension (in ProcessHostTester.m) only has the
// methods LiveContainer/ios18-probe found and used --
// beginExtensionRequestWithInputItems:completion:, pidForRequestIdentifier:,
// setRequestCancellationBlock: -- and it's a real, open question whether a
// distinct method exists for "the extension completed and handed back
// reply items" that neither of those needed for their own narrower tests.
// This asks the actual loaded runtime what's really there instead of
// guessing at a wider block signature -- the same technique ios18-probe
// used against CoreSimulator's real Objective-C surface ("ask the runtime
// directly ... ground truth from the actual loaded binary, not
// reverse-engineered").
void MaciOSIntrospectNSExtension(void (^log)(NSString *line));

NS_ASSUME_NONNULL_END
