#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Diagnostic only: registers a NULL-name ("observe everything") listener
// on CFNotificationCenterGetDarwinNotifyCenter() and logs every
// notification it actually receives (up to a small cap), then stops.
//
// Tests one specific hypothesis directly on-device: CFNotificationCenter.h
// documents NULL as a wildcard ("if name is NULL... all notifications
// will match"), but that documentation isn't specific to which center --
// the Darwin center wraps the low-level notify(3) mechanism, whose real
// registration primitive (notify_register_dispatch) has historically
// required an *exact* name token per registration, unlike the local/
// distributed centers' own pure-userspace pub/sub. If that's true here,
// it explains why ProcessHostTester's own reply-channel observer (which
// registers with NULL specifically because it can't know its expected
// name -- the exit code -- in advance) never fires for anything,
// regardless of whether process-host/main.m posted its reply correctly.
// This sniffer logs ANY Darwin notification, including ones this app had
// nothing to do with, to test the registration mechanism itself in
// isolation from the rest of that channel.
void MaciOSSniffDarwinNotifications(void (^log)(NSString *line));

NS_ASSUME_NONNULL_END
