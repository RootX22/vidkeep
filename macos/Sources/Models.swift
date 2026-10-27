import Foundation

/// What `vidkeep info --json` prints.
struct VideoInfo: Decodable {
    struct Option: Decodable, Hashable {
        let height: Int?
        let codec: String
        let format: String
        let size: Int64?
        let exact: Bool
        let quicktime: String
    }

    struct AudioOnly: Decodable, Hashable {
        let format: String
        let size: Int64?
    }

    let id: String?
    let title: String?
    let channel: String?
    let durationString: String?
    let thumbnail: String?
    let chapters: Int
    let playlistCount: Int
    let defaultFormat: String?
    let audioOnly: AudioOnly?
    let options: [Option]

    var isPlaylist: Bool { playlistCount > 1 }
}

/// One row of the quality table: a video quality, or audio alone.
struct Choice: Identifiable, Hashable {
    let id: String  // the format spec handed to `vidkeep get -f`
    let quality: String
    let codec: String
    let size: Int64?
    let estimated: Bool
    let plays: Playback

    enum Playback: Hashable {
        case everywhere, newerMacs, needsPlayer, unknown

        init(quicktime: String) {
            switch quicktime {
            case "yes": self = .everywhere
            case "no": self = .needsPlayer
            case "M3 or newer": self = .newerMacs
            default: self = .unknown
            }
        }

        var label: String {
            switch self {
            case .everywhere: "QuickTime, iPhone, TV"
            case .newerMacs: "Macs with M3 or newer"
            case .needsPlayer: "Needs IINA or VLC"
            case .unknown: "Depends on the file"
            }
        }
    }

    static func all(for info: VideoInfo) -> [Choice] {
        var rows = info.options.map { option in
            Choice(
                id: option.format, quality: option.height.map { "\($0)p" } ?? "Original",
                codec: option.codec.uppercased(),
                size: option.size, estimated: !option.exact, plays: Playback(quicktime: option.quicktime))
        }
        if let audio = info.audioOnly {
            rows.append(Choice(
                id: audio.format, quality: "Audio only", codec: "M4A", size: audio.size,
                estimated: false, plays: .everywhere))
        }
        return rows
    }
}

/// One line of `vidkeep get --json`.
struct DownloadEvent: Decodable {
    let event: String
    let id: Int
    let url: String?
    let title: String?
    let quality: String?
    let size: Int64?
    let downloaded: Int64?
    let speed: Double?
    let stage: String?
    let status: String?
    let path: String?
    let error: String?
}

enum Bytes {
    static func text(_ count: Int64?) -> String {
        guard let count else { return "unknown size" }
        return ByteCountFormatter.string(fromByteCount: count, countStyle: .file)
    }
}
