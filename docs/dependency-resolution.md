# Dependency resolution: what does a real binary actually need?

## Why this exists

M1 and M2 (`MILESTONES.md`) both use trivial payloads (`clicore_run()`,
`maciOS_test_entry`, `maciOS_patched_payload_entry`) with no framework
dependencies at all beyond what `-lSystem` requires (see
`test-payload/build-and-patch.sh`). A real macOS CLI tool won't be that
simple: it will link against real frameworks, some of which exist on iOS
under the same name, some of which have an iOS equivalent under a
*different* name, and some of which have no iOS equivalent at all. M2's
own follow-up note said this plainly: "expect to spend most of the effort
on dependency resolution" once a real target is picked.

Rather than wait for a specific target to start that work, `tools/macho_patch.py`
now has three subcommands that answer this concretely, for any binary, in
advance:

- **`deps`** -- lists every `LC_LOAD_DYLIB`-family dependency the binary
  declares (required, weak, re-exported, or upward), with its path and
  version info. This is just reading what's already in the file: no
  classification, no opinion.
- **`check-deps`** -- classifies each dependency as `available`,
  `unavailable`, or `unknown` on iOS, and for each `unavailable` one, lists
  the *exact* undefined symbols this specific binary actually needs from
  it (via each symbol's two-level-namespace library ordinal in `LC_SYMTAB`
  -- `undefined_symbols_by_dependency` in the tool). That's a precise stub
  target list, not a guess at a whole framework's surface.
- **`redirect-deps`** -- rewrites one dependency's path in place (e.g.
  `OpenGL.framework/OpenGL` -> `OpenGLES.framework/OpenGLES`, or a macOS-only
  framework -> a stub dylib bundled in the app), subject to the same
  in-place-only constraint `dylibify`'s install name hits: the replacement
  must fit in the load command's existing size (`cmdsize` is fixed at
  compile time; a longer replacement needs full load-command relocation,
  not implemented here).

## Two ways `check-deps` answers "is this available on iOS"

1. **Verified against a real SDK** (`--ios-sdk-path <path>`, or
   `--auto-detect-sdk` to try `xcrun --sdk iphoneos --show-sdk-path` --
   only works on a real macOS toolchain). First checks whether
   `<sdk><path>` exists as a file -- true for many frameworks, whose
   `.tbd` (text-based stub) replaces the real binary at the exact same
   on-disk path. If that finds nothing, `classify_dependency` also scans
   every `.tbd` under `<sdk>/usr/lib` and
   `<sdk>/System/Library/(Private)Frameworks` for one that *declares* the
   dependency's exact install name (`ld` resolves by a `.tbd`'s declared
   name, not by the stub file's own path -- confirmed necessary directly:
   a real iPhoneOS26.5 SDK has no file at the literal path
   `/usr/lib/libSystem.B.dylib`, because that dylib's own `.tbd` lives
   under a different name).

   **One further exception, found the same way and now hardcoded rather
   than searched for:** the umbrella `libSystem.B.dylib` has no
   discoverable `.tbd` *anywhere* in a real SDK, under any name -- a CI
   diagnostic step confirmed only its individual sub-libraries
   (`libsystem_kernel.dylib`, `libsystem_malloc.dylib`, etc.) have their
   own stubs, nested under `usr/lib/system/`, not the umbrella name
   itself. `_ALWAYS_AVAILABLE_BASENAMES` special-cases it as always
   available before either SDK check runs, on the grounds that it isn't
   actually a variable question in the first place: this project's own
   tooling already established (`test-payload/build-and-patch.sh`) that
   `ld64` requires every dynamic executable/dylib to link `libSystem`, on
   any Apple platform, so it's guaranteed present by construction, unlike
   a real framework dependency that may or may not be there.

   `.github/workflows/build.yml` runs `check-deps` on every push against
   `test-payload/PatchedMacOSPayload.dylib` (which links
   `/usr/lib/libSystem.B.dylib` after the `-lSystem` fix -- see
   `test-payload/build-and-patch.sh`). **Confirmed 2026-09-06 (CI run
   19)**: `libSystem.B.dylib` now reports `available`, with the expected
   detail ("always present on any dynamically linked Apple binary, by
   construction -- not resolved via SDK lookup") -- the step no longer
   needs `--allow-unavailable` and gates the build for real.
2. **The built-in `KNOWN_IOS_AVAILABILITY` table** (used when no SDK path
   is available -- this repo's own Linux sandbox has no iOS SDK at all).
   Deliberately non-exhaustive and explicitly labeled a "best-effort
   guess, not SDK-verified" in `check-deps`'s own output -- trust the
   SDK-verified path whenever one is reachable (CI, or a real Mac).
   Extend this table as real targets name frameworks it doesn't cover yet.

What's in the seed table, and why:

- **Available, same name**: `Foundation`, `CoreFoundation`, `CoreGraphics`,
  `CoreText`, `CoreImage`, `Security`, `Network`, `CFNetwork`,
  `SystemConfiguration`, `CoreAudio`, `AudioToolbox`, `AVFoundation`,
  `CoreMedia`, `ImageIO`, `QuartzCore`, `LocalAuthentication`, `Combine`,
  `SwiftUI`, `CryptoKit`, `UniformTypeIdentifiers`, `WebKit`, `PDFKit`,
  `Metal`, `MetalKit`, `StoreKit`, plus the core dylibs every binary
  effectively needs (`libSystem.B.dylib`, `libobjc.A.dylib`, `libc++.1.dylib`,
  `libc++abi.dylib`, `libz.1.dylib`, `libsqlite3.dylib`, `libxml2.2.dylib`,
  `libcompression.dylib`).
- **Unavailable, no iOS counterpart at all**: `AppKit`, `Cocoa`, `Carbon`,
  `CoreServices`, `DiskArbitration`, `ServiceManagement`, `IOBluetooth`,
  `IOKit` (present in a restricted/private form on iOS, not usable the
  same way a portable CLI tool would use it on macOS), `Quartz`,
  `ScriptingBridge`, `OpenDirectory`, `Automator`, `PreferencePanes`,
  `InstallerPlugins`, `CoreWLAN`, `SecurityInterface`.
- **`OpenGL`** is deliberately marked unavailable rather than mapped
  automatically: iOS has `OpenGLES.framework` instead, a genuinely
  different framework (different header/link name), which is exactly what
  `redirect-deps` is for -- a manual decision per binary, not something
  `check-deps` should silently paper over.

## Once `check-deps` finds something unavailable

Two options, same as `AUDIT.md` section 4 and `MILESTONES.md`'s M2
follow-up laid out in the abstract -- this is where they become concrete:

1. **Redirect**, if an iOS equivalent exists under a different path (like
   `OpenGL` -> `OpenGLES`): `tools/macho_patch.py redirect-deps <binary>
   --redirect OLDPATH=NEWPATH`. Cheap, no new code -- but only works when
   an equivalent genuinely exists and the binary's own use of it doesn't
   rely on macOS-only behavior.
2. **Stub**, if there's no iOS equivalent at all (`AppKit`,
   `DiskArbitration`, `ServiceManagement`, etc.): write a small dylib that
   exports exactly the symbols `check-deps` lists under that dependency
   (nothing more -- that list is precise, not a guess at the whole
   framework), give it whatever minimal behavior the binary can tolerate
   (often just "return an error" or a no-op is enough if that code path
   isn't exercised on the CLI's actual usage), build it for iOS, embed it
   in the app bundle the same way `test-payload/TestPayload.c` is embedded
   (a `project.yml` framework target + Copy Files build phase), then
   `redirect-deps` the original path to point at it.

Not automated here: generating the stub's actual source from the symbol
list. `check-deps`'s output is the precise input to that (function names,
nothing else -- no argument types or calling convention info, since that's
not recoverable from a stripped symbol table alone), but writing the stub
itself still needs a human decision about what behavior is safe to fake.
Revisit automating this once a real M3 target's own unavailable-dependency
list shows whether the same handful of symbols recur across real tools
(making a small pre-built stub library worth generating in advance) or
whether it's different enough per-tool that hand-writing each one is
simply the right amount of effort.
