import SwiftUI

// The whole point of M1: the clicore_run() call below is the only place
// the ported CLI's logic is invoked. It's the exact same object code
// whether clicore.c was compiled for arm64-apple-macos or arm64-apple-ios.
// Confirmed working on-device 2026-09-06 -- see MILESTONES.md.
//
// The "Test ProcessHost" button is M2's on-device test: does the
// com.apple.ar.viewer extension trick in process-host/ actually get a
// real, separate OS process? See ProcessHostTester.m for exactly what
// this does and doesn't verify.
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

            Button("Test ProcessHost") {
                testProcessHost()
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

    private func testProcessHost() {
        log.log("Test ProcessHost tapped: my pid is \(ProcessInfo.processInfo.processIdentifier)")
        MaciOSTestProcessHost { launched, pid, message in
            if launched {
                log.log("ProcessHost LAUNCHED, reported pid=\(pid)")
            } else {
                log.log("ProcessHost FAILED: \(message ?? "(no message)")")
            }
        }
    }
}

#Preview {
    ContentView()
}
