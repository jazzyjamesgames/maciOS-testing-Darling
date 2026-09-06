#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Shared between port/MaciOSPortApp/ProcessHostTester.m (host side) and
// process-host/main.m (extension side) -- the actual reply channel this
// project's NSExtension/NSExtensionContext introspection dumps led to.
// See docs/process-host.md's "Investigating the reply-channel question"
// section for how this was found:
//
//   - NSExtension's dump showed no accessor for completeRequestReturningItems:'s
//     payload, but *did* show beginExtensionRequestWithInputItems:listenerEndpoint:completion:,
//     which takes an NSXPCListenerEndpoint the plain completion: variant
//     doesn't.
//   - NSExtensionContext's dump showed the matching private slot on the
//     other end: _auxiliaryListener/_setAuxiliaryListener: (and
//     _auxiliaryConnection/_setAuxiliaryConnection:), which line up with
//     initWithInputItems:listenerEndpoint:contextUUID: -- the constructor
//     NSExtension must use internally when servicing a request made with
//     a listenerEndpoint.
//
// This protocol is this project's own -- not anything Apple defines --
// used as the exportedInterface/remoteObjectInterface on a plain
// NSXPCConnection built from that private slot. Whether _auxiliaryListener
// actually carries what was passed in is exactly what wiring this in and
// running it on-device answers.
@protocol MaciOSProcessHostReply <NSObject>
- (void)processHostDidFinishWithPID:(pid_t)pid
                            exitCode:(int)exitCode
                               error:(nullable NSString *)error;
@end

NS_ASSUME_NONNULL_END
