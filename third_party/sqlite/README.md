# SQLite amalgamation (vendored, unmodified)

`sqlite3.c` and `sqlite3.h` are the official SQLite amalgamation, version
3.53.4, downloaded verbatim from <https://sqlite.org/2026/sqlite-amalgamation-3530400.zip>
(2026-09-06). Public domain -- see the disclaimer at the top of `sqlite3.c`.

This is M3's real target (`MILESTONES.md`): a genuinely non-trivial,
widely-used piece of "macOS-targeted software" -- SQLite is literally what
macOS itself ships as `/usr/bin/sqlite3` and `/usr/lib/libsqlite3.dylib` --
picked specifically because path A (recompile from source for iOS, the
same path M1's `port/CLICore` used) applies cleanly: the amalgamation's
default build (no `SQLITE_ENABLE_FTS5`/`ICU`/`RTREE` etc.) needs nothing
beyond the C standard library and pthreads, both present on iOS, so this
target doesn't exercise `tools/macho_patch.py`'s dependency-redirection
work at all -- that's expected, not a gap; a future M3 target with real
macOS-framework dependencies is what that tooling is for.

`shell.c` (the interactive `sqlite3` command-line shell, also in the
official amalgamation download) is deliberately **not** vendored here:
its `main()` expects a real stdin/stdout terminal, which doesn't exist in
a sandboxed iOS app. `port/SQLiteCLI/sqlite_cli.c` drives the same
underlying engine (`sqlite3.c`) directly through its public C API instead
-- the actual SQL engine is the real, non-trivial "macOS CLI tool" being
proven here; the interactive REPL on top of it is a separate concern this
milestone doesn't need to solve.

Do not hand-edit `sqlite3.c`/`sqlite3.h` -- if a newer version or a build
option is needed, re-download the amalgamation and replace both files
wholesale, updating the version/URL/date above.
