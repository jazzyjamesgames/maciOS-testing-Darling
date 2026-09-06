#import "MaciOSProcessHostDarwinReply.h"

static NSString *MaciOSProcessHostReplyPrefix(NSString *requestUUID) {
  return [NSString stringWithFormat:@"dev.local.maciOS.processhost.reply.%@.", requestUUID];
}

NSString *MaciOSProcessHostReplySuccessName(NSString *requestUUID, int exitCode) {
  return [NSString stringWithFormat:@"%@ok.%d", MaciOSProcessHostReplyPrefix(requestUUID), exitCode];
}

NSString *MaciOSProcessHostReplyFailureName(NSString *requestUUID, NSString *errorMessage) {
  // Alphanumerics-only allowed set (not NSCharacterSet.URLQueryAllowedCharacterSet,
  // which permits unescaped '.') so the encoded payload can never contain a
  // literal '.' -- that keeps "<prefix>.err.<payload>" unambiguous to parse
  // without needing to worry about the payload's own content colliding
  // with the separator.
  NSString *encoded = [errorMessage stringByAddingPercentEncodingWithAllowedCharacters:
                        [NSCharacterSet alphanumericCharacterSet]] ?: @"";
  if (encoded.length > 200) {
    // Darwin/notifyd notification names have no small documented hard
    // limit, but there's no reason to push it either -- 200 encoded
    // characters is plenty to identify a failure without risking an
    // unusually long name behaving oddly on a code path nobody has
    // exercised with names this long before.
    encoded = [encoded substringToIndex:200];
  }
  return [NSString stringWithFormat:@"%@err.%@", MaciOSProcessHostReplyPrefix(requestUUID), encoded];
}

BOOL MaciOSParseProcessHostReplyName(NSString *name, NSString *requestUUID,
                                     BOOL *outSuccess, int *outExitCode,
                                     NSString **outError) {
  NSString *prefix = MaciOSProcessHostReplyPrefix(requestUUID);
  if (![name hasPrefix:prefix]) {
    return NO;
  }
  NSString *rest = [name substringFromIndex:prefix.length];
  if ([rest hasPrefix:@"ok."]) {
    if (outSuccess) *outSuccess = YES;
    if (outExitCode) *outExitCode = [[rest substringFromIndex:3] intValue];
    if (outError) *outError = nil;
    return YES;
  }
  if ([rest hasPrefix:@"err."]) {
    NSString *encoded = [rest substringFromIndex:4];
    if (outSuccess) *outSuccess = NO;
    if (outExitCode) *outExitCode = 0;
    if (outError) *outError = [encoded stringByRemovingPercentEncoding] ?: encoded;
    return YES;
  }
  return NO;
}
