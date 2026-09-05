import Foundation
import UIKit

// A minimal, always-on log the user can copy off the device and hand back
// for debugging -- Files-app sharing is unreliable once an app has gone
// through SideStore's resign (ios18-probe hit this directly: "No shared
// log found (App Group entitlement may not have carried through
// resigning...)"), so the pasteboard is the one channel that's held up in
// practice. Kept deliberately tiny: one shared instance, append-only,
// timestamped, with a copy-to-pasteboard action the UI calls directly.
final class DebugLog: ObservableObject {
    static let shared = DebugLog()

    @Published private(set) var text: String = ""

    private let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss.SSS"
        return f
    }()

    func log(_ line: String) {
        let timestamp = formatter.string(from: Date())
        DispatchQueue.main.async {
            self.text += "[\(timestamp)] \(line)\n"
        }
    }

    func copyToPasteboard() {
        UIPasteboard.general.string = text.isEmpty ? "(log is empty)" : text
    }
}
