# Getting a log back to me, for debugging

I have no direct access to your Mac, your iPhone, or GitHub Actions runners
as they execute — I can only see what gets pasted into the chat, or what I
pull from GitHub's API after a run finishes. Here's every channel that
actually exists, in the order you'll hit them:

## 1. CI build failures (`.github/workflows/build.yml`)

I can pull these myself — I don't need you to do anything. If you want to
check yourself: open the **Actions** tab on the repo, click the failing
run, click the `build` job, and the raw log is right there. The two
build steps (`Build for iOS Simulator`, `Build for a real device`) are
where a real compile error will show up; `xcodebuild`'s own `error:` lines
are the ones that matter, everything else is compiler invocation noise.

## 2. Xcode's console, while running from Xcode (M1, `docs/xcode-setup.md`)

When you press Run in Xcode, its bottom console pane shows everything the
app prints (`NSLog`, stdout/stderr, and crash backtraces if it dies). This
is the richest channel available and the first place to look if the app
doesn't launch at all, or crashes before its own in-app log has anything
in it. Select all the text in that pane and paste it in — don't summarize
it, the details (exact exception type, offending selector, line number)
are exactly what's needed.

## 3. The app's own in-app log (`DebugLog.swift` / the Copy Log button)

Once the app is running (from Xcode, or later, installed via SideStore), it
keeps a running, timestamped log of what it's done (`port/MaciOSPortApp/DebugLog.swift`)
and shows it on screen with a **Copy Log** button. Tap it, then paste
into the chat. This is the channel that survives once you're no longer
tethered to Xcode's console (i.e. after a SideStore install, for M2) —
deliberately not file- or App-Group-based, because `ios18-probe`'s own
research found that channel unreliable after SideStore's resign step ("No
shared log found (App Group entitlement may not have carried through
resigning...)"). The pasteboard held up in practice; this mirrors that.

## 4. A crash the in-app log never sees

If the app crashes hard enough that it never gets back to its own log code
(a `dispatch` barrier abort, an uncatchable exception — `ios18-probe` hit
this repeatedly), the in-app log won't have the answer. Two options,
either works:

- **Console.app** (on your Mac, with the iPhone connected): Window →
  Devices and Simulators, or open Console.app directly, select your
  iPhone in the sidebar, and filter by the app's process name
  (`MaciOSPortApp` or `ProcessHost`). Copy the relevant lines around the
  crash.
- **A sysdiagnose**, if Console.app doesn't show enough: on the iPhone,
  hold Volume Up + Volume Down + Side button briefly (this triggers a
  system diagnostic capture, no crash-specific steps needed), wait a few
  minutes, then find it under Settings → Privacy & Security → Analytics &
  Improvements → Analytics Data (filenamed `sysdiagnose_...`), share it via
  AirDrop/Mail to your Mac. This is a large file — you don't need to send
  the whole thing, just search it for the app's process name and the
  timestamp of the crash, and paste the relevant excerpt.

## What to actually send

Prefer more over less — a full paste of "here's the log" beats a
paraphrase every time, since the exact wording of an error (a selector
name, a Mach exception type, an errno) is usually the whole answer. If a
log is too long to paste in one message, the first and last ~50 lines
around the point where things went wrong are usually enough; say so if
you're trimming it.
