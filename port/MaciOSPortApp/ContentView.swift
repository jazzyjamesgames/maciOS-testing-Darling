import SwiftUI

// The whole point of M1: the clicore_run() call below is the only place
// the ported CLI's logic is invoked. It's the exact same object code
// whether clicore.c was compiled for arm64-apple-macos or arm64-apple-ios.
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
}

#Preview {
    ContentView()
}
