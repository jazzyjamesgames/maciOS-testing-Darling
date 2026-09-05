#ifndef CLICORE_H
#define CLICORE_H

/* Stand-in for a real CLI tool's logic. Deliberately framework-free (no
 * Foundation, no AppKit) so it compiles unchanged for arm64-apple-macos or
 * arm64-apple-ios -- only the surrounding app shell differs between the
 * two targets, not this file. */
const char *clicore_run(void);

#endif
