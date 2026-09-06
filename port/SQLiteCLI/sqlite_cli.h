#ifndef MACIOS_SQLITE_CLI_H
#define MACIOS_SQLITE_CLI_H

#ifdef __cplusplus
extern "C" {
#endif

// M3's real target (MILESTONES.md): SQLite's own amalgamation
// (third_party/sqlite/), recompiled for arm64-apple-ios via path A (source
// port -- the same approach port/CLICore's clicore_run() used for M1) but
// with a genuinely non-trivial, real-world engine instead of a one-line
// stub. sqlite3 is literally what macOS itself ships as /usr/bin/sqlite3
// and /usr/lib/libsqlite3.dylib.
//
// Runs a small, self-contained SQL workload against an in-memory database
// (no filesystem/sandbox-path questions to solve for this first M3 win --
// see third_party/sqlite/README.md) and returns 0 on success, matching the
// same int name(void) contract process-host/ already uses for M2
// payloads, so this could be exercised through process-host/ later
// without any changes here.
//
// resultOut receives a human-readable summary of what actually happened
// (row count, a computed aggregate, sqlite3's own version string) via a
// caller-owned buffer, so a passing return code isn't the whole story on
// device -- same spirit as M1/M2's distinctive return values (42/99).
int sqliteCLI_run(char *resultOut, int resultOutCapacity);

#ifdef __cplusplus
}
#endif

#endif
