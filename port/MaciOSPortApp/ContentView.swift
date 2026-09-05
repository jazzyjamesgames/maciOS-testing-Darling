import SwiftUI

// The whole point of M1: this line is the only place the ported CLI's
// logic is invoked. clicore_run() is the exact same object code whether
// clicore.c was compiled for arm64-apple-macos or arm64-apple-ios.
struct ContentView: View {
    var body: some View {
        Text(String(cString: clicore_run()))
            .padding()
            .multilineTextAlignment(.center)
    }
}

#Preview {
    ContentView()
}
