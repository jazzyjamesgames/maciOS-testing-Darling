#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Shared naming convention for the process-host reply channel between
// port/MaciOSPortApp/ProcessHostTester.m (host, observes) and
// process-host/main.m (extension, posts), via
// CFNotificationCenterGetDarwinNotifyCenter() -- real, public, documented
// API, unlike the two private-API mechanisms already tried and found not
// to work (see docs/process-host.md: completeRequestReturningItems: has
// no host-side accessor at all, and the NSXPCListenerEndpoint/
// _auxiliaryListener approach was rejected outright by this extension
// point on-device).
//
// Darwin notifications carry no payload of their own -- there is no
// userInfo dictionary, unlike CFNotificationCenterGetLocalCenter() --  so
// the exit code/error is encoded directly into the notification's own
// name, scoped by a requestUUID the HOST chooses (not the system's own
// opaque per-launch request identifier from
// beginExtensionRequestWithInputItems:completion:) so concurrent requests
// can't collide and so the host can start observing before the extension
// could possibly have anything to reply with.
NSString *MaciOSProcessHostReplySuccessName(NSString *requestUUID, int exitCode);
NSString *MaciOSProcessHostReplyFailureName(NSString *requestUUID, NSString *errorMessage);

// Parses any posted Darwin notification name. Returns NO if it doesn't
// match requestUUID's reply prefix at all -- expected for almost every
// call, since the host observes *every* Darwin notification posted
// system-wide (CFNotificationCenterAddObserver has no way to filter
// server-side on a name it doesn't know in advance, since the payload is
// encoded into the name itself) and must filter here instead.
BOOL MaciOSParseProcessHostReplyName(NSString *name, NSString *requestUUID,
                                     BOOL *outSuccess, int *outExitCode,
                                     NSString * _Nullable * _Nullable outError);

NS_ASSUME_NONNULL_END
