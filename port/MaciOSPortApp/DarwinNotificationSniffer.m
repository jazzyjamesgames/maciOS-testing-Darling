#import "DarwinNotificationSniffer.h"
#import <CoreFoundation/CoreFoundation.h>

static void (^gSniffLogger)(NSString *line);
static int gSniffCount;
static char kSnifferObserverToken;

static void MaciOSSniffTrampoline(CFNotificationCenterRef center, void *observer,
                                  CFNotificationName name, const void *object,
                                  CFDictionaryRef userInfo) {
  gSniffCount++;
  if (gSniffLogger) {
    gSniffLogger([NSString stringWithFormat:@"sniffed Darwin notification #%d: %@",
                                             gSniffCount, (__bridge NSString *)name]);
  }
  if (gSniffCount >= 30) {
    CFNotificationCenterRemoveObserver(CFNotificationCenterGetDarwinNotifyCenter(),
                                        &kSnifferObserverToken, NULL, NULL);
    if (gSniffLogger) {
      gSniffLogger(@"sniffer: stopped after 30 notifications");
    }
  }
}

void MaciOSSniffDarwinNotifications(void (^log)(NSString *line)) {
  gSniffLogger = [log copy];
  gSniffCount = 0;
  log(@"Darwin notification sniffer started (NULL-name registration, "
      "logs up to 30 notifications, any source)");
  CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(),
                                   &kSnifferObserverToken,
                                   MaciOSSniffTrampoline,
                                   NULL,  // NULL name: the exact behavior under test
                                   NULL,
                                   CFNotificationSuspensionBehaviorDeliverImmediately);
}
