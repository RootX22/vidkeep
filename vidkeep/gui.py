"""The vidkeep desktop window: paste a link, pick a quality, download. Built for Windows, runs anywhere
Tk does. All the work happens in vidkeep child processes (see desktop.py); this only draws it.
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from importlib import resources
from io import BytesIO
from tkinter import filedialog, messagebox, ttk

import darkdetect
import sv_ttk
from PIL import Image, ImageTk

from . import CREDIT, __version__, desktop

REPO = "https://github.com/RootX22/vidkeep"
POLL_MS = 300
THUMB = (176, 99)
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
STAGES = {
    "Merger": "Joining video and audio",
    "FFmpegMetadata": "Adding title and chapters",
    "EmbedThumbnail": "Adding cover image",
    "FFmpegEmbedSubtitle": "Adding subtitles",
    "FFmpegSubtitlesConvertor": "Converting subtitles",
    "FFmpegExtractAudio": "Converting audio",
}
# What each codec means for someone who just wants the file to open.
PLAYS = {
    "h264": "Plays everywhere",
    "h265": "Most new devices",
    "av1": "Smaller file, needs a recent player",
    "vp9": "Needs VLC or a browser",
}


# The theme draws every ttk label in its own text colour and ignores ours, so coloured text is a
# plain Tk label on the theme's background. These are Sun Valley's backgrounds and matching tones.
PALETTE = {
    "dark": {"background": "#1c1c1c", "muted": "#a3a3a3", "error": "#ff6b61", "love": "#ff4d7d"},
    "light": {"background": "#fafafa", "muted": "#5f5f5f", "error": "#c0392b", "love": "#e0245e"},
}


def tone(name: str) -> str:
    return PALETTE[sv_ttk.get_theme()][name]


def tinted(parent, text: str = "", colour: str = "muted", **options) -> tk.Label:
    return tk.Label(
        parent, text=text, fg=tone(colour), bg=tone("background"), font="SunValleyBodyFont", justify="left", **options
    )


def size_text(count: int | None) -> str:
    if not count:
        return "unknown size"
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if count >= scale:
            return f"{count / scale:.1f} {unit}"
    return f"{count} B"


def clock(seconds: float) -> str:
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def choices(info: dict) -> list[dict]:
    """The rows of the quality table, each with the format spec it downloads."""
    rows = [
        {
            "format": option["format"],
            "quality": f"{option['height']}p" if option.get("height") else option["codec"],
            "codec": option["codec"],
            "size": ("" if option["exact"] else "≈ ") + size_text(option["size"]),
            "plays": PLAYS.get(option["codec"], "Depends on the player"),
        }
        for option in info.get("options") or []
    ]
    audio = info.get("audio_only")
    if audio:
        rows.append(
            {
                "format": audio["format"],
                "quality": "Audio only",
                "codec": "m4a",
                "size": size_text(audio["size"]),
                "plays": "Plays everywhere",
            }
        )
    if not rows and info.get("default_format"):
        rows.append({"format": info["default_format"], "quality": "Original", "codec": "", "size": "", "plays": ""})
    return rows


def preferred_format(info: dict) -> str | None:
    """On Windows, files that open in the built-in player by default: the best H.264 up to 1080p."""
    if sys.platform == "win32":
        playable = [o for o in info.get("options") or [] if o["codec"] == "h264" and (o.get("height") or 0) <= 1080]
        if playable:
            return playable[0]["format"]
    return info.get("default_format")


def download_status(download: desktop.Download) -> tuple[str, str]:
    """(progress, status) text for one row of the downloads list."""
    videos = download.snapshot()
    total = sum(video.get("size") or 0 for video in videos)
    fetched = sum(
        (video.get("size") or 0) if video.get("status") in desktop.FINISHED else (video.get("downloaded") or 0)
        for video in videos
    )
    percent = f"{min(100, fetched * 100 // total)}%" if total else ""
    prefix = ""
    if len(videos) > 1:
        finished = sum(video.get("status") in desktop.FINISHED for video in videos)
        prefix = f"{finished} of {len(videos)} videos · "
    status = download.status
    if status == "done":
        return "100%", prefix + "Saved"
    if status == "stopped":
        return percent, prefix + "Stopped. Resume picks up where it stopped"
    if status == "failed":
        return percent, download.error or "Failed"
    stage = next((video.get("stage") for video in videos if video.get("status") == "processing"), None)
    if stage:
        return percent, prefix + STAGES.get(stage, "Finishing")
    speed = sum(video.get("speed") or 0 for video in videos if video.get("status") == "downloading")
    if not total or not speed:
        return percent, prefix + "Starting"
    return percent, prefix + f"{size_text(int(speed))}/s · {clock((total - fetched) / speed)} left"


def open_path(path: str, reveal: bool = False) -> None:
    if sys.platform == "win32":
        if reveal:
            subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
        else:
            os.startfile(path)  # noqa: S606 - opening the user's own downloaded file
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", path] if reveal else ["open", path], check=False)
    else:
        subprocess.run(["xdg-open", os.path.dirname(path) if reveal else path], check=False)


class App:
    def __init__(self, root: tk.Tk, look_up=desktop.look_up, command: list[str] | None = None, settings_path=None):
        self.root = root
        self.look_up = look_up
        self.command = command
        self.settings_path = settings_path
        self.settings = desktop.load_settings(settings_path)
        self.info: dict | None = None
        self.looked_up = ""
        self.rows: list[dict] = []
        self.downloads: dict[str, desktop.Download] = {}
        self.inbox: queue.Queue = queue.Queue()
        self.thumbnail = None  # Tk drops images nothing holds on to
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(POLL_MS, self.poll)

    # Layout

    def _build(self) -> None:
        self.root.title("vidkeep")
        self.root.minsize(820, 640)
        page = ttk.Frame(self.root, padding=16)
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=3)
        page.rowconfigure(5, weight=2)
        self._link_row(page).grid(row=0, column=0, sticky="ew")
        self.message = tinted(page, "Paste a YouTube video or playlist link.", wraplength=760)
        self.message.grid(row=1, column=0, sticky="w", pady=(8, 8))
        self.video = self._video_panel(page)
        self.video.grid(row=2, column=0, sticky="nsew")
        self.video.grid_remove()
        self._save_row(page).grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Separator(page).grid(row=4, column=0, sticky="ew", pady=12)
        self._downloads_panel(page).grid(row=5, column=0, sticky="nsew")
        self._footer(page).grid(row=6, column=0, sticky="ew", pady=(10, 0))

    def _link_row(self, parent) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.columnconfigure(0, weight=1)
        self.link = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.link)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _event: self.start_lookup())
        ttk.Button(row, text="Paste", command=self.paste).grid(row=0, column=1, padx=(8, 0))
        self.lookup_button = ttk.Button(row, text="Look up", style="Accent.TButton", command=self.start_lookup)
        self.lookup_button.grid(row=0, column=2, padx=(8, 0))
        return row

    def _video_panel(self, parent) -> ttk.Frame:
        panel = ttk.Frame(parent)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        head = ttk.Frame(panel)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.thumb = ttk.Label(head)
        self.thumb.grid(row=0, column=0, rowspan=2, padx=(0, 14))
        self.title = ttk.Label(head, font=("Segoe UI", 13, "bold"), wraplength=560)
        self.title.grid(row=0, column=1, sticky="w")
        self.details = tinted(head)
        self.details.grid(row=1, column=1, sticky="nw")

        self.single = ttk.Frame(panel)
        self.single.columnconfigure(0, weight=1)
        self.single.rowconfigure(0, weight=1)
        self.table = self._tree(
            self.single,
            (("quality", "Quality", 110), ("codec", "Codec", 70), ("size", "Size", 110), ("plays", "Plays on", 260)),
        )
        self.table.configure(selectmode="browse")

        self.playlist = ttk.Frame(panel)
        self.playlist.columnconfigure(0, weight=1)
        self.playlist.rowconfigure(2, weight=1)
        self.only_video = ttk.Frame(self.playlist)
        tinted(self.only_video, "This link is a video inside a playlist.").pack(side="left")
        ttk.Button(self.only_video, text="Download only that video", command=self.look_up_only_video).pack(
            side="left", padx=8
        )
        picks = ttk.Frame(self.playlist)
        picks.grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Label(picks, text="Highest quality").pack(side="left")
        self.quality = tk.StringVar(value="1080")
        ttk.Combobox(picks, textvariable=self.quality, values=QUALITIES, width=8, state="readonly").pack(
            side="left", padx=(6, 18)
        )
        ttk.Label(picks, text="Files").pack(side="left")
        self.codec = tk.StringVar(
            value="Plays everywhere (H.264)" if sys.platform == "win32" else "Smallest (AV1 first)"
        )
        ttk.Combobox(
            picks,
            textvariable=self.codec,
            values=("Plays everywhere (H.264)", "Smallest (AV1 first)"),
            width=24,
            state="readonly",
        ).pack(side="left", padx=6)
        self.entries = self._tree(
            self.playlist, (("number", "#", 40), ("name", "Video", 520), ("length", "Length", 80)), row=2
        )
        return panel

    def _tree(self, parent, columns, row=0) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[key for key, _, _ in columns], show="headings", height=6)
        for key, heading, width in columns:
            tree.heading(key, text=heading, anchor="w")
            tree.column(key, width=width, anchor="w", stretch=key in ("plays", "name", "title", "status"))
        scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=row, column=0, sticky="nsew")
        scroll.grid(row=row, column=1, sticky="ns")
        return tree

    def _save_row(self, parent) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.columnconfigure(3, weight=1)
        self.subtitles = tk.BooleanVar(value=self.settings["subtitles"])
        ttk.Checkbutton(row, text="Subtitles", variable=self.subtitles).grid(row=0, column=0)
        self.languages = tk.StringVar(value=self.settings["languages"])
        ttk.Entry(row, textvariable=self.languages, width=8).grid(row=0, column=1, padx=(6, 18))
        self.folder_label = tinted(row, self.settings["folder"])
        self.folder_label.grid(row=0, column=2, sticky="w")
        ttk.Button(row, text="Change…", command=self.choose_folder).grid(row=0, column=3, sticky="w", padx=8)
        self.download_button = ttk.Button(
            row, text="Download", style="Accent.TButton", command=self.download, state="disabled"
        )
        self.download_button.grid(row=0, column=4, sticky="e")
        return row

    def _downloads_panel(self, parent) -> ttk.Frame:
        panel = ttk.Frame(parent)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        bar = ttk.Frame(panel)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text="Downloads", font=("Segoe UI", 11, "bold")).pack(side="left")
        for text, action in (
            ("Clear finished", self.clear_finished),
            ("Show in folder", self.reveal),
            ("Resume", self.resume),
            ("Stop", self.stop),
        ):
            ttk.Button(bar, text=text, command=action).pack(side="right", padx=(6, 0))
        self.download_list = self._tree(
            panel,
            (("title", "Video", 380), ("quality", "Quality", 110), ("progress", "Done", 60), ("status", "Status", 300)),
            row=1,
        )
        self.download_list.bind("<Double-1>", lambda _event: self.open_selected())
        return panel

    def _footer(self, parent) -> ttk.Frame:
        row = ttk.Frame(parent)
        parts = (
            ttk.Label(row, text="Made with", cursor="hand2"),
            tinted(row, "♥", "love", cursor="hand2"),
            ttk.Label(row, text=f"by {CREDIT} from Egypt", cursor="hand2"),
        )
        for part in parts:
            part.pack(side="left", padx=(0, 4))
            part.bind("<Button-1>", lambda _event: webbrowser.open(REPO))
        tinted(row, f"vidkeep {__version__} · free and open source").pack(side="right")
        return row

    def say(self, text: str, colour: str = "muted") -> None:
        self.message.configure(text=text, fg=tone(colour))

    # Looking up

    def paste(self) -> None:
        try:
            self.link.set(self.root.clipboard_get().strip())
        except tk.TclError:
            return  # nothing, or nothing textual, on the clipboard
        self.start_lookup()

    def start_lookup(self) -> None:
        url = self.link.get().strip()
        if not url:
            return
        self.lookup_button.configure(state="disabled")
        self.download_button.configure(state="disabled")
        self.video.grid_remove()
        self.say("Looking up…")
        threading.Thread(target=self._lookup_worker, args=(url,), daemon=True).start()

    def _lookup_worker(self, url: str) -> None:
        try:
            self.inbox.put(("info", url, self.look_up(url, self.command)))
        except desktop.LookupFailed as err:
            self.inbox.put(("error", url, str(err)))

    def _thumbnail_worker(self, url: str) -> None:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - an https image URL from yt-dlp
                self.inbox.put(("thumbnail", url, response.read()))
        except OSError:
            return  # no picture is fine

    def look_up_only_video(self) -> None:
        single = desktop.only_the_video(self.looked_up)
        if single:
            self.link.set(single)
            self.start_lookup()

    def show(self, url: str, info: dict) -> None:
        self.info, self.looked_up = info, url
        playlist = info.get("playlist_count", 1) > 1
        self.title.configure(text=(info.get("playlist_title") if playlist else None) or info.get("title") or "Untitled")
        if playlist:
            entries = info.get("entries") or []
            durations = [entry.get("duration") for entry in entries]
            total = f" · {clock(sum(durations))} in all" if durations and all(durations) else ""
            self.details.configure(text=f"Playlist of {info['playlist_count']} videos{total}")
            self.entries.delete(*self.entries.get_children())
            for number, entry in enumerate(entries, 1):
                length = clock(entry["duration"]) if entry.get("duration") else ""
                self.entries.insert("", "end", values=(number, entry.get("title") or "Untitled video", length))
            if desktop.only_the_video(url):
                self.only_video.grid(row=0, column=0, sticky="w", pady=(0, 8))
            else:
                self.only_video.grid_remove()
            self.single.grid_remove()
            self.playlist.grid(row=1, column=0, sticky="nsew")
        else:
            parts = [info.get("channel"), info.get("duration_string")]
            if info.get("chapters"):
                parts.append(f"{info['chapters']} chapters")
            self.details.configure(text=" · ".join(part for part in parts if part))
            self.rows = choices(info)
            self.table.delete(*self.table.get_children())
            for row in self.rows:
                self.table.insert(
                    "", "end", iid=row["format"], values=(row["quality"], row["codec"], row["size"], row["plays"])
                )
            chosen = preferred_format(info)
            if chosen and self.table.exists(chosen):
                self.table.selection_set(chosen)
                self.table.see(chosen)
            self.playlist.grid_remove()
            self.single.grid(row=1, column=0, sticky="nsew")
        self.thumb.configure(image="")
        if info.get("thumbnail"):
            threading.Thread(target=self._thumbnail_worker, args=(info["thumbnail"],), daemon=True).start()
        self.say("")
        self.video.grid()
        self.download_button.configure(state="normal")

    def show_thumbnail(self, data: bytes) -> None:
        image = Image.open(BytesIO(data)).convert("RGB")
        image.thumbnail((THUMB[0] * 2, THUMB[1] * 2))
        image = image.resize(THUMB)
        self.thumbnail = ImageTk.PhotoImage(image)
        self.thumb.configure(image=self.thumbnail)

    # Downloading

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.settings["folder"], mustexist=False)
        if folder:
            self.settings["folder"] = folder
            self.folder_label.configure(text=folder)
            self._save()

    def _save(self) -> None:
        self.settings.update(subtitles=self.subtitles.get(), languages=self.languages.get().strip() or "en")
        desktop.save_settings(self.settings, self.settings_path)

    def request(self) -> desktop.Request | None:
        if not self.info:
            return None
        request = desktop.Request(url=self.looked_up, folder=self.settings["folder"])
        if self.info.get("playlist_count", 1) > 1:
            request.quality = self.quality.get()
            request.codec = "compatible" if self.codec.get().startswith("Plays") else "efficient"
        else:
            selected = self.table.selection()
            if not selected:
                return None
            request.format = selected[0]
        audio = (self.info.get("audio_only") or {}).get("format")
        if self.subtitles.get() and request.format != audio:
            request.subs = self.languages.get().replace(" ", "") or None
        return request

    def download(self) -> None:
        request = self.request()
        if not request:
            self.say("Pick a quality first.")
            return
        self._save()
        playlist = self.info.get("playlist_count", 1) > 1
        name = (self.info.get("playlist_title") if playlist else None) or self.info.get("title") or request.url
        kwargs = {"command": self.command} if self.command else {}
        self.add(desktop.Download(request, name, **kwargs).start())

    def add(self, download: desktop.Download) -> None:
        iid = str(id(download))
        self.downloads[iid] = download
        self.download_list.insert("", 0, iid=iid, values=(download.title, "", "", "Starting"))

    def selected(self) -> desktop.Download | None:
        chosen = self.download_list.selection()
        return self.downloads.get(chosen[0]) if chosen else None

    def stop(self) -> None:
        download = self.selected()
        if download:
            download.stop()

    def resume(self) -> None:
        download = self.selected()
        if download and download.status in ("stopped", "failed"):
            iid = str(id(download))
            self.download_list.delete(iid)
            del self.downloads[iid]
            self.add(download.again())

    def reveal(self) -> None:
        download = self.selected()
        path = download.saved_path if download else None
        if path and os.path.exists(path):
            open_path(path, reveal=True)
        elif download:
            folder = download.request.folder
            os.makedirs(folder, exist_ok=True)
            open_path(folder)

    def open_selected(self) -> None:
        download = self.selected()
        path = download.saved_path if download else None
        if path and os.path.exists(path):
            open_path(path)

    def clear_finished(self) -> None:
        for iid, download in list(self.downloads.items()):
            if download.status != "running":
                self.download_list.delete(iid)
                del self.downloads[iid]

    # The timer

    def poll(self) -> None:
        while True:
            try:
                kind, url, payload = self.inbox.get_nowait()
            except queue.Empty:
                break
            self.handle(kind, url, payload)
        for iid, download in self.downloads.items():
            progress, status = download_status(download)
            videos = download.snapshot()
            quality = next((video["quality"] for video in videos if video.get("quality")), "")
            self.download_list.item(iid, values=(download.title, quality, progress, status))
        self.root.after(POLL_MS, self.poll)

    def handle(self, kind: str, url: str, payload) -> None:
        if kind == "thumbnail":
            if self.info and self.info.get("thumbnail") == url:
                self.show_thumbnail(payload)
            return
        self.lookup_button.configure(state="normal")
        if url != self.link.get().strip():
            return  # the link changed while it was being looked up
        if kind == "info":
            self.show(url, payload)
        else:
            self.say(payload, "error")

    def close(self) -> None:
        running = [download for download in self.downloads.values() if download.status == "running"]
        if running and not messagebox.askyesno(
            "Downloads are still running",
            "Quit anyway? They stop now and resume where they left off when you download them again.",
        ):
            return
        for download in running:
            download.stop()
        self.root.destroy()


def _crisp_text_on_windows() -> None:
    """Without this, Windows draws the window at 96 dpi and blurs it on high-resolution screens."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            return


def make_window() -> tk.Tk:
    _crisp_text_on_windows()
    root = tk.Tk()
    sv_ttk.set_theme("dark" if darkdetect.isDark() else "light")
    style = ttk.Style(root)
    points_to_pixels = float(root.tk.call("tk", "scaling"))  # follows the screen's resolution
    style.configure("Treeview", rowheight=int(22 * points_to_pixels))
    icon = resources.files("vidkeep").joinpath("assets/icon-256.png")
    with resources.as_file(icon) as path:
        root.icon = tk.PhotoImage(file=str(path))  # held here: Tk drops images nothing refers to
    root.iconphoto(True, root.icon)
    root.geometry("960x760")
    return root


def main() -> None:
    root = make_window()
    app = App(root)
    links = [arg for arg in sys.argv[1:] if arg.startswith(("http://", "https://"))]
    if links:  # "Open with", or a shortcut that carries a link
        app.link.set(links[0])
        app.start_lookup()
    root.mainloop()


if __name__ == "__main__":
    main()
