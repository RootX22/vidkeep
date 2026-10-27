import AppKit
import SwiftUI

@main
enum Entry {
    static func main() {
        let arguments = CommandLine.arguments
        if let index = arguments.firstIndex(of: "--selftest") {
            SelfTest.start(Array(arguments[(index + 1)...]))
        } else {
            VidkeepApp.main()
        }
    }
}

struct VidkeepApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model: AppModel

    init() {
        let snapshot = Snapshot.requestedPath != nil
        let model = AppModel(defaults: snapshot ? UserDefaults(suiteName: "vidkeep-snapshot")! : .standard)
        if snapshot { Snapshot.fill(model) }
        AppModel.current = model
        _model = StateObject(wrappedValue: model)
    }

    var body: some Scene {
        Window("Vidkeep", id: "main") {
            ContentView(model: model)
                .onAppear(perform: Snapshot.captureIfRequested)
        }
        .defaultSize(width: 920, height: 780)
        .commands { CommandGroup(replacing: .newItem) {} }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    // Closing the window must not stop downloads; the Dock icon brings it back.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    @MainActor
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model = AppModel.current, model.hasActiveDownloads else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Downloads are still running"
        alert.informativeText = "Quit anyway? They stop now and resume where they left off when you download them again."
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Keep Downloading")
        guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        model.stopAll()
        return .terminateNow
    }
}
