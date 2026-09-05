# Building and running M1 on your iPhone

Everything up to this point (the C core, the Swift shell) was written and
validated for compilation in a Linux sandbox with no Apple toolchain — see
`AUDIT.md` section 3 for exactly what could and couldn't be verified from
there. This document is the remaining steps, which require a Mac with
Xcode and your iPhone connected (USB, or same Wi-Fi network with Xcode's
wireless debugging enabled).

## 1. Create the Xcode project

1. Xcode → File → New → Project → iOS → **App**.
2. Product Name: `MaciOSPortApp`. Interface: **SwiftUI**. Language:
   **Swift**. Uncheck Core Data / Tests (not needed for this milestone).
3. Save it anywhere; Xcode generates its own `MaciOSPortApp.swift` and
   `ContentView.swift` — you'll overwrite both with this repo's versions.

## 2. Add this repo's files

1. Replace the generated `MaciOSPortApp.swift` and `ContentView.swift` with
   `port/MaciOSPortApp/MaciOSPortApp.swift` and
   `port/MaciOSPortApp/ContentView.swift` from this repo (drag them in,
   choosing "Copy items if needed").
2. Drag in `port/CLICore/clicore.c` and `port/CLICore/clicore.h` the same
   way, into the same target.
3. When Xcode adds the first C file to a Swift target, it offers to create
   an Objective-C bridging header — accept, or manually set **Build
   Settings → Objective-C Bridging Header** to the path of
   `port/MaciOSPortApp/MaciOSPortApp-Bridging-Header.h` (or just copy that
   file's one `#include` line into whatever bridging header Xcode
   generated).

## 3. Sign it with your own Apple ID (no paid account needed)

1. Select the project in the navigator → target `MaciOSPortApp` →
   **Signing & Capabilities**.
2. Check **Automatically manage signing**.
3. **Team**: Add Account… and sign in with your Apple ID if it's not
   listed, then select it. Xcode creates a free "personal team"
   provisioning profile scoped to your device.
4. Note: apps signed this way stop launching after **7 days** unless you
   have a paid Apple Developer Program membership — you'll need to
   re-run from Xcode periodically. This is an Apple platform limit, not
   something this project can work around (it's the same trust-chain
   requirement discussed in `AUDIT.md`).

## 4. Run it on your iPhone

1. Connect your iPhone 14 via USB (or set up wireless debugging:
   Window → Devices and Simulators → select your device → check
   "Connect via network").
2. On the iPhone, if this is its first time being used for development:
   Settings → Privacy & Security → **Developer Mode** → enable it → the
   phone reboots and asks you to confirm.
3. In Xcode's device/scheme picker (top toolbar), choose your iPhone as
   the run destination.
4. Press **Run** (▶). Xcode compiles `clicore.c` for `arm64-apple-ios`,
   links it with the Swift shell, signs the resulting `.app` with your
   personal-team profile, installs it via `installd`, and launches it.
5. First launch on-device may prompt: Settings → General → VPN & Device
   Management → trust your developer certificate, then relaunch the app
   from the home screen.

## Expected result

The app opens to a single screen showing:

```
hello from native arm64 maciOS (ported)
```

That string comes from `clicore_run()` in `port/CLICore/clicore.c`,
executing as native arm64 code inside a normally-launched, normally-signed
iOS app — no VM, no emulator, no jailbreak.

## If something doesn't match this doc

Xcode's exact UI moves between versions; if a menu item above is named or
placed slightly differently in your Xcode version, the underlying steps
(new SwiftUI App project → add these files → set bridging header →
automatic signing with your Apple ID → run on your connected device) are
what matters, not the exact click path.
