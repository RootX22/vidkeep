import Foundation

/// Runs the `vidkeep` command line, which does all the downloading; this app only drives it.
enum VidkeepCLI {
    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static let installCommand = "uv tool install git+https://github.com/RootX22/vidkeep"

    private static var home: String { FileManager.default.homeDirectoryForCurrentUser.path }

    /// Apps opened from Finder get a bare PATH, without Homebrew or ~/.local/bin,
    /// so vidkeep, ffmpeg and deno would not be found. Add the usual places.
    private static var searchPath: [String] {
        ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "\(home)/.deno/bin", "/usr/bin", "/bin"]
    }

    static func locate() -> URL? {
        if let custom = ProcessInfo.processInfo.environment["VIDKEEP_CLI"] {
            return URL(fileURLWithPath: custom)  // a development checkout, for example
        }
        return searchPath
            .map { URL(fileURLWithPath: $0).appendingPathComponent("vidkeep") }
            .first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    private static var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = (searchPath + [env["PATH"] ?? ""]).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        env["NO_COLOR"] = "1"
        env["COLUMNS"] = "200"
        return env
    }

    private static func makeProcess(_ arguments: [String]) throws -> Process {
        guard let binary = locate() else {
            throw Failure(message: "The vidkeep command is not installed. Run: \(installCommand)")
        }
        let process = Process()
        process.executableURL = binary
        process.arguments = arguments
        process.environment = environment
        return process
    }

    /// Browsers yt-dlp can borrow a YouTube sign-in from; "" means none.
    static let browsers = ["", "firefox", "chrome", "safari", "edge", "brave"]

    static func signInArguments(_ browser: String) -> [String] {
        browser.isEmpty ? [] : ["--cookies-from-browser", browser]
    }

    /// `vidkeep info --json URL`, decoded.
    static func info(for url: String, browser: String) async throws -> VideoInfo {
        let process = try makeProcess(["info", "--json", url] + signInArguments(browser))
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    try process.run()
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    let stderr = errors.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    guard process.terminationStatus == 0 else {
                        throw Failure(message: lastMeaningfulLine(of: stderr) ?? "vidkeep could not read this link")
                    }
                    let decoder = JSONDecoder()
                    decoder.keyDecodingStrategy = .convertFromSnakeCase
                    continuation.resume(returning: try decoder.decode(VideoInfo.self, from: data))
                } catch is DecodingError {
                    // Older vidkeep releases print fewer fields than this app reads.
                    continuation.resume(throwing: Failure(
                        message: "Your vidkeep command is older than this app. Update it: uv tool upgrade vidkeep"))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    /// Start `vidkeep get --json ...`; events and the exit arrive on the main queue.
    static func download(
        _ arguments: [String],
        onEvent: @escaping (DownloadEvent) -> Void,
        onExit: @escaping (_ status: Int32, _ lastError: String?) -> Void
    ) throws -> Process {
        let process = try makeProcess(["get", "--json"] + arguments)
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        let lines = LineSplitter { line in
            guard let event = try? JSONDecoder().decode(DownloadEvent.self, from: line) else { return }
            DispatchQueue.main.async { onEvent(event) }
        }
        output.fileHandleForReading.readabilityHandler = { handle in lines.feed(handle.availableData) }
        let stderr = Tail()
        errors.fileHandleForReading.readabilityHandler = { handle in stderr.append(handle.availableData) }
        process.terminationHandler = { finished in
            output.fileHandleForReading.readabilityHandler = nil
            errors.fileHandleForReading.readabilityHandler = nil
            lines.feed(output.fileHandleForReading.readDataToEndOfFile())
            let message = lastMeaningfulLine(of: stderr.data)
            DispatchQueue.main.async { onExit(finished.terminationStatus, message) }
        }
        try process.run()
        return process
    }

    static func lastMeaningfulLine(of data: Data) -> String? {
        String(decoding: data, as: UTF8.self)
            .split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .last { !$0.isEmpty && !$0.hasPrefix("WARNING") }
            .map { $0.replacingOccurrences(of: "ERROR: ", with: "") }
    }
}

/// Reassembles whole lines from the chunks a pipe delivers.
final class LineSplitter: @unchecked Sendable {
    private var buffer = Data()
    private let lock = NSLock()
    private let handle: (Data) -> Void

    init(_ handle: @escaping (Data) -> Void) { self.handle = handle }

    func feed(_ chunk: Data) {
        lock.lock()
        buffer.append(chunk)
        var lines: [Data] = []
        while let newline = buffer.firstIndex(of: UInt8(ascii: "\n")) {
            lines.append(buffer[buffer.startIndex..<newline])
            buffer.removeSubrange(buffer.startIndex...newline)
        }
        lock.unlock()
        lines.filter { !$0.isEmpty }.forEach(handle)
    }
}

/// The last few kilobytes of a stream, enough for an error message.
final class Tail: @unchecked Sendable {
    private var storage = Data()
    private let lock = NSLock()
    private let limit = 8192

    func append(_ chunk: Data) {
        lock.lock()
        storage.append(chunk)
        if storage.count > limit { storage = storage.suffix(limit) }
        lock.unlock()
    }

    var data: Data {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}
