import AppKit
import Foundation

/// One video in the downloads list.
@MainActor
final class VideoRow: ObservableObject, Identifiable {
    enum State: Equatable {
        case starting, downloading, processing(String), done(path: String?), failed(String), stopped

        var isActive: Bool {
            switch self {
            case .starting, .downloading, .processing: true
            default: false
            }
        }
    }

    let id = UUID()
    @Published var title: String
    @Published var quality = ""
    @Published var total: Int64?
    @Published var downloaded: Int64 = 0
    @Published var speed: Double?
    @Published var state: State = .starting
    @Published var savedSize: Int64?
    fileprivate(set) var run: DownloadRun?

    init(title: String) { self.title = title }

    var fraction: Double? {
        guard let total, total > 0 else { return nil }
        return min(1, Double(downloaded) / Double(total))
    }

    var secondsLeft: Double? {
        guard let total, let speed, speed > 0 else { return nil }
        return Double(max(0, total - downloaded)) / speed
    }
}

/// One `vidkeep get` process; a playlist run feeds several rows.
@MainActor
final class DownloadRun {
    let arguments: [String]
    var process: Process?
    var rowsByEvent: [Int: VideoRow] = [:]
    var waiting: [VideoRow]  // rows shown before vidkeep has named its videos
    var cancelled = false

    init(arguments: [String], placeholder: VideoRow) {
        self.arguments = arguments
        self.waiting = [placeholder]
    }

    var rows: [VideoRow] { Array(rowsByEvent.values) + waiting }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var link = ""
    @Published private(set) var info: VideoInfo?
    @Published private(set) var choices: [Choice] = []
    @Published var selection: Choice.ID?
    @Published private(set) var lookingUp = false
    @Published private(set) var lookupError: String?
    @Published private(set) var rows: [VideoRow] = []
    @Published private(set) var cliMissing = VidkeepCLI.locate() == nil
    @Published var playlistQuality = "1080"
    @Published var playlistCodec = "efficient"
    @Published var folder: URL { didSet { defaults.set(folder.path, forKey: "folder") } }
    @Published var subtitles: Bool { didSet { defaults.set(subtitles, forKey: "subtitles") } }
    @Published var subtitleLanguages: String { didSet { defaults.set(subtitleLanguages, forKey: "subtitleLanguages") } }
    @Published var browser: String { didSet { defaults.set(browser, forKey: "browser") } }

    /// The model behind the app window, for the app delegate's quit check.
    static weak var current: AppModel?

    private let defaults: UserDefaults
    private var lookedUpLink = ""

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let movies = FileManager.default.urls(for: .moviesDirectory, in: .userDomainMask)[0]
        folder = defaults.string(forKey: "folder").map { URL(fileURLWithPath: $0) }
            ?? movies.appendingPathComponent("vidkeep")
        subtitles = defaults.bool(forKey: "subtitles")
        subtitleLanguages = defaults.string(forKey: "subtitleLanguages") ?? "en"
        browser = defaults.string(forKey: "browser") ?? ""
    }

    /// YouTube's "confirm you're not a bot" check, answered by signing in through a browser.
    var lookupNeedsSignIn: Bool {
        guard let lookupError else { return false }
        return lookupError.contains("confirm you") || lookupError.contains("cookies")
    }

    var canDownload: Bool {
        guard let info else { return false }
        return info.isPlaylist || selection != nil
    }

    var selectedChoice: Choice? { choices.first { $0.id == selection } }

    func lookUp() async {
        let link = link.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !link.isEmpty, !lookingUp else { return }
        lookingUp = true
        lookupError = nil
        info = nil
        choices = []
        defer { lookingUp = false }
        do {
            present(try await VidkeepCLI.info(for: link, browser: browser), link: link)
        } catch {
            lookupError = error.localizedDescription
        }
        cliMissing = VidkeepCLI.locate() == nil
    }

    func present(_ found: VideoInfo, link: String) {
        info = found
        lookedUpLink = link
        choices = Choice.all(for: found)
        selection = found.defaultFormat ?? choices.first?.id
    }

    var hasActiveDownloads: Bool { rows.contains { $0.state.isActive } }

    func stopAll() {
        rows.filter(\.state.isActive).forEach(cancel)
    }

    /// Put a row in the list without a process behind it (used for the screenshot).
    func show(_ row: VideoRow) {
        rows.append(row)
    }

    func download() {
        guard let info else { return }
        var arguments = [lookedUpLink, "-o", folder.path] + VidkeepCLI.signInArguments(browser)
        if info.isPlaylist {
            arguments += ["-q", playlistQuality, "-c", playlistCodec]
        } else if let selection {
            arguments += ["-f", selection]
        }
        let audioOnly = selection != nil && selection == info.audioOnly?.format
        if subtitles, !audioOnly, !subtitleLanguages.trimmingCharacters(in: .whitespaces).isEmpty {
            arguments += ["--subs", subtitleLanguages.replacingOccurrences(of: " ", with: "")]
        }
        start(arguments: arguments, title: info.title ?? lookedUpLink)
    }

    func cancel(_ row: VideoRow) {
        guard let run = row.run else { return }
        run.cancelled = true
        run.process?.terminate()  // vidkeep treats SIGTERM like Ctrl+C: partial files stay resumable
    }

    /// Run the same command again; vidkeep picks up finished ranges from disk.
    func retry(_ row: VideoRow) {
        guard let old = row.run else { return }
        rows.removeAll { candidate in old.rows.contains { $0 === candidate } }
        start(arguments: old.arguments, title: row.title)
    }

    func reveal(_ row: VideoRow) {
        guard case let .done(path?) = row.state else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    func open(_ row: VideoRow) {
        guard case let .done(path?) = row.state else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    func clearFinished() {
        rows.removeAll { !$0.state.isActive }
    }

    private func start(arguments: [String], title: String) {
        let placeholder = VideoRow(title: title)
        let run = DownloadRun(arguments: arguments, placeholder: placeholder)
        placeholder.run = run
        rows.insert(placeholder, at: 0)
        do {
            run.process = try VidkeepCLI.download(
                arguments,
                onEvent: { [weak self] event in self?.apply(event, to: run) },
                onExit: { [weak self] status, error in self?.finish(run, status: status, error: error) })
        } catch {
            placeholder.state = .failed(error.localizedDescription)
        }
    }

    private func row(for event: DownloadEvent, in run: DownloadRun) -> VideoRow {
        if let row = run.rowsByEvent[event.id] { return row }
        let row = run.waiting.isEmpty ? VideoRow(title: event.url ?? "") : run.waiting.removeFirst()
        row.run = run
        run.rowsByEvent[event.id] = row
        if !rows.contains(where: { $0 === row }) { rows.insert(row, at: 0) }
        return row
    }

    private func apply(_ event: DownloadEvent, to run: DownloadRun) {
        let row = row(for: event, in: run)
        switch event.event {
        case "plan":
            row.title = event.title ?? row.title
            row.quality = event.quality ?? ""
            row.total = event.size
            row.state = .downloading
        case "progress":
            row.downloaded = event.downloaded ?? row.downloaded
            row.speed = event.speed
            row.state = .downloading
        case "stage":
            row.state = .processing(Self.describe(stage: event.stage))
        case "finished":
            if let title = event.title, !title.isEmpty { row.title = title }
            row.savedSize = event.size
            switch event.status {
            case "done": row.state = .done(path: event.path)
            case "skipped": row.state = .done(path: nil)
            default: row.state = .failed(event.error ?? "download failed")
            }
        default:
            break
        }
    }

    private func finish(_ run: DownloadRun, status: Int32, error: String?) {
        let reason = error ?? (status == 0 ? "Nothing was downloaded" : "vidkeep stopped (exit \(status))")
        for row in run.rows where row.state.isActive {
            row.state = run.cancelled ? .stopped : .failed(reason)
        }
    }

    static func describe(stage: String?) -> String {
        switch stage {
        case "Merger": "Joining video and audio"
        case "FFmpegMetadata": "Adding title and chapters"
        case "EmbedThumbnail": "Adding cover image"
        case "FFmpegEmbedSubtitle": "Adding subtitles"
        case "FFmpegSubtitlesConvertor": "Converting subtitles"
        case "FFmpegExtractAudio": "Converting audio"
        default: "Finishing"
        }
    }
}
