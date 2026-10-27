import AppKit
import Foundation

/// `Vidkeep --snapshot out.png`: open the window with sample content, save a picture of it, quit.
/// The app draws its own window, so no screen-recording permission is involved.
enum Snapshot {
    static var requestedPath: String? {
        let arguments = CommandLine.arguments
        guard let index = arguments.firstIndex(of: "--snapshot"), arguments.count > index + 1 else { return nil }
        return arguments[index + 1]
    }

    @MainActor
    static func fill(_ model: AppModel) {
        // Big Buck Bunny (Blender Foundation, CC BY 3.0), so the picture can be shared.
        let sample = """
        {"id": "aqz-KE-bpKQ", "title": "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film",
         "channel": "Blender", "duration_string": "10:35",
         "thumbnail": "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg", "chapters": 0, "playlist_count": 1,
         "default_format": "399+140", "audio_only": {"format": "140", "size": 10276000},
         "options": [
          {"height": 2160, "codec": "av1", "format": "401+140", "size": 722720000, "exact": true, "quicktime": "M3 or newer"},
          {"height": 2160, "codec": "vp9", "format": "313+140", "size": 1374000000, "exact": true, "quicktime": "no"},
          {"height": 1080, "codec": "av1", "format": "399+140", "size": 160300000, "exact": true, "quicktime": "M3 or newer"},
          {"height": 1080, "codec": "h264", "format": "137+140", "size": 269640000, "exact": true, "quicktime": "yes"},
          {"height": 720, "codec": "av1", "format": "398+140", "size": 81560000, "exact": true, "quicktime": "M3 or newer"},
          {"height": 480, "codec": "h264", "format": "135+140", "size": 39910000, "exact": true, "quicktime": "yes"}
         ]}
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let info = try? decoder.decode(VideoInfo.self, from: Data(sample.utf8)) else { return }
        model.link = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        model.present(info, link: model.link)
        model.subtitles = true

        let running = VideoRow(title: "Sintel - Open Movie by Blender Foundation")
        running.quality = "1080p av1"
        running.total = 236_000_000
        running.downloaded = 99_100_000
        running.speed = 7_130_000
        running.state = .downloading
        let joining = VideoRow(title: "Tears of Steel - Blender Foundation's Open Movie")
        joining.quality = "720p av1"
        joining.state = .processing(AppModel.describe(stage: "Merger"))
        let saved = VideoRow(title: "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film")
        saved.quality = "1080p h264"
        saved.savedSize = 269_640_000
        saved.state = .done(path: "/tmp/sample.mp4")
        [running, joining, saved].forEach(model.show)
    }

    static func captureIfRequested() {
        guard let path = requestedPath else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            // SwiftUI draws into Core Animation layers, which cacheDisplay(in:to:) skips,
            // so render the window's whole layer tree instead.
            guard
                let view = NSApp.windows.first(where: \.isVisible)?.contentView?.superview,
                let layer = view.layer,
                let bitmap = NSBitmapImageRep(
                    bitmapDataPlanes: nil, pixelsWide: Int(view.bounds.width * 2), pixelsHigh: Int(view.bounds.height * 2),
                    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
                let context = NSGraphicsContext(bitmapImageRep: bitmap)?.cgContext
            else { exit(1) }
            context.scaleBy(x: 2, y: 2)
            layer.render(in: context)
            let saved = (try? bitmap.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))) != nil
            exit(saved ? 0 : 1)  // not terminate(): the sample rows would trigger the quit prompt
        }
    }
}

/// `Vidkeep --selftest URL FOLDER [SECONDS]`: drive the real app model with no window.
/// Looks the link up, starts the default download, stops it after SECONDS, resumes it,
/// and exits 0 only if the resumed download ends with the file on disk.
enum SelfTest {
    static func start(_ arguments: [String]) {
        guard arguments.count >= 2 else {
            print("usage: Vidkeep --selftest URL FOLDER [SECONDS]")
            exit(2)
        }
        let stopAfter = arguments.count > 2 ? Double(arguments[2]) ?? 3 : 3
        Task { @MainActor in
            exit(await run(link: arguments[0], folder: arguments[1], stopAfter: stopAfter))
        }
        dispatchMain()
    }

    @MainActor
    private static func run(link: String, folder: String, stopAfter: Double) async -> Int32 {
        let model = AppModel(defaults: UserDefaults(suiteName: "vidkeep-selftest")!)
        model.folder = URL(fileURLWithPath: folder)
        model.link = link
        await model.lookUp()
        guard let info = model.info else {
            print("lookup failed: \(model.lookupError ?? "unknown error")")
            return 1
        }
        print("looked up: \(info.title ?? "?") · \(model.choices.count) choices · default \(model.selection ?? "none")")

        model.download()
        guard let first = model.rows.first else { return 1 }
        try? await Task.sleep(for: .seconds(stopAfter))
        print("before stop: \(first.state) · \(first.downloaded) of \(first.total ?? 0) bytes")
        model.cancel(first)
        guard await wait(for: first) == .stopped else {
            print("expected stopped, got \(first.state)")
            return 1
        }
        print("stopped: \(first.downloaded) bytes on disk")

        model.retry(first)
        guard let resumed = model.rows.first else { return 1 }
        let end = await wait(for: resumed)
        print("after resume: \(end) · saved \(Bytes.text(resumed.savedSize))")
        guard case let .done(path?) = end, FileManager.default.fileExists(atPath: path) else { return 1 }
        return 0
    }

    @MainActor
    private static func wait(for row: VideoRow, timeout: Double = 900) async -> VideoRow.State {
        let deadline = Date().addingTimeInterval(timeout)
        while row.state.isActive, Date() < deadline {
            try? await Task.sleep(for: .milliseconds(200))
        }
        return row.state
    }
}
