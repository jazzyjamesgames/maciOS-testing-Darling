#import "NSExtensionIntrospection.h"
#import <objc/runtime.h>

void MaciOSIntrospectNSExtension(void (^log)(NSString *line)) {
  Class cls = NSClassFromString(@"NSExtension");
  if (!cls) {
    log(@"NSExtension: class not found via NSClassFromString");
    return;
  }
  log([NSString stringWithFormat:@"NSExtension real class found: %@", cls]);

  unsigned int count = 0;
  Method *methods = class_copyMethodList(cls, &count);
  log([NSString stringWithFormat:@"NSExtension instance methods: %u", count]);
  for (unsigned int i = 0; i < count; i++) {
    SEL sel = method_getName(methods[i]);
    const char *encoding = method_getTypeEncoding(methods[i]);
    log([NSString stringWithFormat:@"  -%@  %s", NSStringFromSelector(sel),
                                    encoding ?: "(no encoding)"]);
  }
  if (methods) free(methods);

  unsigned int classCount = 0;
  Method *classMethods = class_copyMethodList(object_getClass(cls), &classCount);
  log([NSString stringWithFormat:@"NSExtension class methods: %u", classCount]);
  for (unsigned int i = 0; i < classCount; i++) {
    SEL sel = method_getName(classMethods[i]);
    const char *encoding = method_getTypeEncoding(classMethods[i]);
    log([NSString stringWithFormat:@"  +%@  %s", NSStringFromSelector(sel),
                                    encoding ?: "(no encoding)"]);
  }
  if (classMethods) free(classMethods);
}
