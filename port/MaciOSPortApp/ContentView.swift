import SwiftUI

// The whole point of M1: the clicore_run() call below is the only place
// the ported CLI's logic is invoked. It's the exact same object code
// whether clicore.c was compiled for arm64-apple-macos or arm64-apple-ios.
// Confirmed working on-device 2026-09-06 -- see MILESTONES.md.
//
// Two ProcessHost buttons, testing two different things:
//   - "Test ProcessHost (native)": TestPayload.c, compiled straight for
//     iOS by Xcode. A control -- confirms the com.apple.ar.viewer
//     extension trick itself gets a real separate process, decoupled
//     from any question about a foreign binary's own compatibility.
//     Confirmed working on-device 2026-09-06 (pid 1867 -> 1869).
//   - "Test ProcessHost (patched macOS binary)": PatchedMacOSPayload.dylib,
//     produced by test-payload/build-and-patch.sh from a binary compiled
//     for arm64-apple-macos and never recompiled for iOS -- run through
//     tools/macho_patch.py's actual patch+dylibify pipeline instead. This
//     is the real M2 claim ("you don't need the source"), not yet
//     confirmed on-device as of this build.
// See ProcessHostTester.m for exactly what either test does and doesn't
// verify.
//
// The log view + copy button exist so a real on-device run produces
// something that can actually be debugged: see DebugLog.swift for why the
// pasteboard, not a shared file, is the channel used.
struct ContentView: View {
    @ObservedObject private var log = DebugLog.shared
    @State private var result: String = "(not run yet)"

    var body: some View {
        VStack(spacing: 16) {
            Text(result)
                .padding()
                .multilineTextAlignment(.center)

            ScrollView {
                Text(log.text.isEmpty ? "(log is empty)" : log.text)
                    .font(.system(.footnote, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
            }
            .frame(maxHeight: .infinity)

            Button("Test ProcessHost (native)") {
                testProcessHost(frameworksRelativePath: "TestPayload.framework/TestPayload",
                                 entryPoint: "maciOS_test_entry",
                                 label: "native")
            }
            .buttonStyle(.bordered)

            Button("Test ProcessHost (patched macOS binary)") {
                testProcessHost(frameworksRelativePath: "PatchedMacOSPayload.dylib",
                                 entryPoint: "maciOS_patched_payload_entry",
                                 label: "patched")
            }
            .buttonStyle(.bordered)

            Button("Introspect NSExtension") {
                log.log("Introspect NSExtension tapped")
                MaciOSIntrospectNSExtension { line in
                    log.log(line)
                }
            }
            .buttonStyle(.bordered)

            Button("Introspect NSExtensionContext") {
                log.log("Introspect NSExtensionContext tapped")
                MaciOSIntrospectNSExtensionContext { line in
                    log.log(line)
                }
            }
            .buttonStyle(.bordered)

            Button("Copy Log") {
                log.copyToPasteboard()
            }
            .buttonStyle(.borderedProminent)
            .padding(.bottom)
        }
        .onAppear {
            log.log("app launched")
            let value = String(cString: clicore_run())
            result = value
            log.log("clicore_run() -> \(value)")
        }
    }

    private func testProcessHost(frameworksRelativePath: String, entryPoint: String, label: String) {
        log.log("Test ProcessHost (\(label)) tapped: my pid is \(ProcessInfo.processInfo.processIdentifier)")
        MaciOSTestProcessHost(frameworksRelativePath, entryPoint) { launched, pid, message in
            if launched {
                log.log("ProcessHost (\(label)) LAUNCHED, reported pid=\(pid)")
            } else {
                log.log("ProcessHost (\(label)) FAILED: \(message ?? "(no message)")")
            }
        }
    }
}

#Preview {
    ContentView()
}
