#import "NSExtensionIntrospection.h"
#import <objc/runtime.h>

static void logMethods(Class cls, BOOL isMeta, void (^log)(NSString *line)) {
  unsigned int count = 0;
  Method *methods = class_copyMethodList(cls, &count);
  log([NSString stringWithFormat:@"%@ %@ methods: %u", NSStringFromClass(cls),
                                  isMeta ? @"class" : @"instance", count]);
  for (unsigned int i = 0; i < count; i++) {
    SEL sel = method_getName(methods[i]);
    const char *encoding = method_getTypeEncoding(methods[i]);
    log([NSString stringWithFormat:@"  %@%@  %s", isMeta ? @"+" : @"-",
                                    NSStringFromSelector(sel),
                                    encoding ?: "(no encoding)"]);
  }
  if (methods) free(methods);
}

void MaciOSIntrospectClass(NSString *className, void (^log)(NSString *line)) {
  Class cls = NSClassFromString(className);
  if (!cls) {
    log([NSString stringWithFormat:@"%@: class not found via NSClassFromString", className]);
    return;
  }
  log([NSString stringWithFormat:@"%@ real class found: %@", className, cls]);
  logMethods(cls, NO, log);
  logMethods(object_getClass(cls), YES, log);
}

void MaciOSIntrospectNSExtension(void (^log)(NSString *line)) {
  MaciOSIntrospectClass(@"NSExtension", log);
}

void MaciOSIntrospectNSExtensionContext(void (^log)(NSString *line)) {
  MaciOSIntrospectClass(@"NSExtensionContext", log);
}
