import AppKit
import SwiftUI

struct ContentView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            if model.cliMissing { MissingCLIBanner() }
            LinkBar(model: model)
            Divider()
            Group {
                if model.lookingUp {
                    ProgressView("Reading the video…").controlSize(.large)
                } else if let error = model.lookupError {
                    VStack(spacing: 4) {
                        Message(symbol: "exclamationmark.triangle", title: "Couldn't read that link", detail: error)
                        if model.lookupNeedsSignIn { SignInHint(model: model) }
                    }
                } else if let info = model.info {
                    VideoPanel(model: model, info: info)
                } else {
                    Message(
                        symbol: "arrow.down.circle",
                        title: "Paste a link to begin",
                        detail: "You'll see every quality with its real download size before anything is saved.")
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            DownloadsPanel(model: model)
                .frame(height: 250)
        }
        .frame(minWidth: 780, minHeight: 680)
    }
}

private struct MissingCLIBanner: View {
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "wrench.and.screwdriver").foregroundStyle(.orange)
            Text("The vidkeep command isn't installed. Run this in Terminal:")
            Text(VidkeepCLI.installCommand).font(.system(.body, design: .monospaced)).textSelection(.enabled)
            Spacer()
            Button("Copy") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(VidkeepCLI.installCommand, forType: .string)
            }
        }
        .padding(10)
        .background(.orange.opacity(0.12))
    }
}

private struct LinkBar: View {
    @ObservedObject var model: AppModel
    @FocusState private var focused: Bool

    private var isEmpty: Bool { model.link.trimmingCharacters(in: .whitespaces).isEmpty }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "link").foregroundStyle(.secondary)
            TextField("Paste a YouTube video or playlist link", text: $model.link)
                .textFieldStyle(.plain)
                .font(.title3)
                .focused($focused)
                .onSubmit { Task { await model.lookUp() } }
            BrowserPicker(selection: $model.browser) {
                Image(systemName: model.browser.isEmpty ? "person.crop.circle" : "person.crop.circle.fill")
            }
            .pickerStyle(.menu)
            .fixedSize()
            .help("Use your YouTube sign-in from a browser, for videos YouTube only gives to signed-in viewers")
            Button("Paste") {
                guard let text = NSPasteboard.general.string(forType: .string) else { return }
                model.link = text
                Task { await model.lookUp() }
            }
            Button("Look Up") { Task { await model.lookUp() } }
                .buttonStyle(.borderedProminent)
                .disabled(isEmpty || model.lookingUp)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .onAppear { focused = true }
    }
}

private struct BrowserPicker<Label: View>: View {
    @Binding var selection: String
    @ViewBuilder let label: () -> Label

    var body: some View {
        Picker(selection: $selection) {
            Text("No sign-in").tag("")
            Divider()
            ForEach(VidkeepCLI.browsers.filter { !$0.isEmpty }, id: \.self) { browser in
                Text("Signed in with \(browser.capitalized)").tag(browser)
            }
        } label: {
            label()
        }
    }
}

private struct SignInHint: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(spacing: 8) {
            HStack {
                BrowserPicker(selection: $model.browser) { Text("YouTube sign-in") }
                    .frame(width: 300)
                Button("Try Again") { Task { await model.lookUp() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.browser.isEmpty)
            }
            Text("Pick a browser where you're signed in to YouTube. macOS may ask once to let Vidkeep read it.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

private struct Message: View {
    let symbol: String
    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: symbol).font(.system(size: 40)).foregroundStyle(.tertiary)
            Text(title).font(.title3.weight(.semibold))
            Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 440)
        }
        .padding()
    }
}

private struct VideoPanel: View {
    @ObservedObject var model: AppModel
    let info: VideoInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VideoHeader(info: info)
            if info.isPlaylist {
                PlaylistOptions(model: model, count: info.playlistCount)
                Spacer(minLength: 0)
            } else {
                QualityTable(model: model)
            }
            SaveBar(model: model)
        }
        .padding(16)
    }
}

private struct VideoHeader: View {
    let info: VideoInfo

    private var details: String {
        var parts = [info.channel, info.durationString].compactMap { $0 }
        if info.chapters > 0 { parts.append("\(info.chapters) chapters") }
        if info.isPlaylist { parts.append("playlist of \(info.playlistCount) videos") }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            AsyncImage(url: info.thumbnail.flatMap(URL.init(string:))) { image in
                image.resizable().aspectRatio(contentMode: .fill)
            } placeholder: {
                Rectangle().fill(.quaternary)
            }
            .frame(width: 176, height: 99)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 6) {
                Text(info.title ?? "Untitled").font(.title3.weight(.semibold)).lineLimit(2)
                Text(details).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
    }
}

private struct QualityTable: View {
    @ObservedObject var model: AppModel

    var body: some View {
        Table(model.choices, selection: $model.selection) {
            TableColumn("Quality") { choice in Text(choice.quality).fontWeight(.medium) }
                .width(min: 80, ideal: 96)
            TableColumn("Codec") { choice in Text(choice.codec).foregroundStyle(.secondary) }
                .width(min: 50, ideal: 60)
            TableColumn("Size") { choice in
                Text((choice.estimated ? "≈ " : "") + Bytes.text(choice.size)).monospacedDigit()
            }
            .width(min: 80, ideal: 100)
            TableColumn("Plays on") { choice in PlaybackLabel(plays: choice.plays) }
        }
        .frame(minHeight: 150)
    }
}

private struct PlaybackLabel: View {
    let plays: Choice.Playback

    private var symbol: (name: String, color: Color) {
        switch plays {
        case .everywhere: ("checkmark.circle.fill", .green)
        case .newerMacs: ("cpu", .orange)
        case .needsPlayer: ("play.rectangle", .secondary)
        case .unknown: ("questionmark.circle", .secondary)
        }
    }

    var body: some View {
        Label {
            Text(plays.label)
        } icon: {
            Image(systemName: symbol.name).foregroundStyle(symbol.color)
        }
    }
}

private struct PlaylistOptions: View {
    @ObservedObject var model: AppModel
    let count: Int

    private let qualities = ["best", "2160", "1440", "1080", "720", "480", "360"]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 20) {
                Picker("Highest quality", selection: $model.playlistQuality) {
                    ForEach(qualities, id: \.self) { quality in
                        Text(quality == "best" ? "Best available" : "\(quality)p").tag(quality)
                    }
                }
                .frame(width: 260)
                Picker("Files", selection: $model.playlistCodec) {
                    Text("Smallest (AV1 first)").tag("efficient")
                    Text("Plays everywhere (H.264)").tag("compatible")
                }
                .frame(width: 300)
            }
            Text("Applies to each of the \(count) videos. They're saved, numbered, in a folder named after the playlist.")
                .foregroundStyle(.secondary)
        }
    }
}

private struct SaveBar: View {
    @ObservedObject var model: AppModel

    private var folderText: String {
        model.folder.path.replacingOccurrences(of: FileManager.default.homeDirectoryForCurrentUser.path, with: "~")
    }

    var body: some View {
        HStack(spacing: 12) {
            Label {
                Text(folderText).lineLimit(1).truncationMode(.middle)
            } icon: {
                Image(systemName: "folder")
            }
            .frame(maxWidth: 260, alignment: .leading)
            Button("Change…", action: chooseFolder)
            Divider().frame(height: 18)
            Toggle("Subtitles", isOn: $model.subtitles)
            TextField("en", text: $model.subtitleLanguages)
                .frame(width: 64)
                .disabled(!model.subtitles)
                .help("Languages, separated by commas, e.g. en,ar")
            Spacer()
            if let choice = model.selectedChoice, model.info?.isPlaylist == false {
                Text(Bytes.text(choice.size)).foregroundStyle(.secondary).monospacedDigit()
            }
            Button(action: model.download) {
                Label("Download", systemImage: "arrow.down.circle.fill")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut("d", modifiers: .command)
            .disabled(!model.canDownload)
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Save Here"
        panel.directoryURL = model.folder
        if panel.runModal() == .OK, let url = panel.url { model.folder = url }
    }
}

private struct DownloadsPanel: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Downloads").font(.headline)
                Spacer()
                if model.rows.contains(where: { !$0.state.isActive }) {
                    Button("Clear Finished", action: model.clearFinished).buttonStyle(.link)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            if model.rows.isEmpty {
                Text("Nothing yet. Downloads keep going while you look up the next link.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(model.rows) { row in DownloadRowView(row: row, model: model) }
                    .listStyle(.inset)
            }
        }
    }
}

private struct DownloadRowView: View {
    @ObservedObject var row: VideoRow
    let model: AppModel

    var body: some View {
        HStack(spacing: 12) {
            StatusIcon(state: row.state)
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(row.title).fontWeight(.medium).lineLimit(1)
                    Spacer()
                    Text(row.quality).font(.caption).foregroundStyle(.secondary)
                }
                if row.state == .downloading, let fraction = row.fraction {
                    ProgressView(value: fraction)
                } else if row.state.isActive {
                    ProgressView().progressViewStyle(.linear)
                }
                Text(Detail.text(for: row)).font(.caption).foregroundStyle(Detail.color(for: row.state)).lineLimit(2)
            }
            actions.buttonStyle(.borderless).imageScale(.large)
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder private var actions: some View {
        switch row.state {
        case .starting, .downloading, .processing:
            Button { model.cancel(row) } label: { Image(systemName: "stop.circle") }.help("Stop (resume later)")
        case .done(path: .some):
            Button { model.open(row) } label: { Image(systemName: "play.circle") }.help("Open")
            Button { model.reveal(row) } label: { Image(systemName: "magnifyingglass.circle") }.help("Show in Finder")
        case .failed, .stopped:
            Button { model.retry(row) } label: { Image(systemName: "arrow.clockwise.circle") }.help("Resume")
        case .done(path: .none):
            EmptyView()
        }
    }
}

private struct StatusIcon: View {
    let state: VideoRow.State

    var body: some View {
        Group {
            switch state {
            case .starting, .downloading: Image(systemName: "arrow.down.circle").foregroundStyle(.blue)
            case .processing: Image(systemName: "gearshape.2").foregroundStyle(.purple)
            case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            case .failed: Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
            case .stopped: Image(systemName: "pause.circle.fill").foregroundStyle(.orange)
            }
        }
        .font(.title2)
        .frame(width: 28)
    }
}

enum Detail {
    private static let timeLeft: DateComponentsFormatter = {
        let formatter = DateComponentsFormatter()
        formatter.unitsStyle = .abbreviated
        formatter.allowedUnits = [.hour, .minute, .second]
        formatter.maximumUnitCount = 2
        return formatter
    }()

    @MainActor static func text(for row: VideoRow) -> String {
        switch row.state {
        case .starting:
            return "Getting ready…"
        case .downloading:
            var parts = ["\(Bytes.text(row.downloaded)) of \(Bytes.text(row.total))"]
            if let speed = row.speed { parts.append("\(Bytes.text(Int64(speed)))/s") }
            if let left = row.secondsLeft, let text = timeLeft.string(from: left) { parts.append("\(text) left") }
            return parts.joined(separator: " · ")
        case let .processing(stage):
            return "\(stage)…"
        case .done(path: .some):
            return "Saved · \(Bytes.text(row.savedSize))"
        case .done(path: .none):
            return "Already downloaded"
        case let .failed(message):
            return message
        case .stopped:
            return "Stopped. Resume continues from where it left off."
        }
    }

    static func color(for state: VideoRow.State) -> Color {
        if case .failed = state { return .red }
        return .secondary
    }
}
