"""yt-dlp wired to the parallel range downloader."""

from __future__ import annotations

from yt_dlp import YoutubeDL

from . import accel


class VidkeepYDL(YoutubeDL):
    """YoutubeDL that hands single-file HTTP formats to :class:`accel.ParallelRangeFD`.

    Everything else (extraction, file naming, merging, chapters, subtitles,
    archives) stays with yt-dlp; only the byte transfer of eligible streams changes.
    """

    def dl(self, name, info, subtitle=False, test=False):
        if subtitle or test or name == "-" or not accel.supports(info, self.params):
            return super().dl(name, info, subtitle=subtitle, test=test)
        if not info.get("url"):
            self.raise_no_formats(info, True)
        fd = accel.ParallelRangeFD(self, self.params)
        for hook in self._progress_hooks:
            fd.add_progress_hook(hook)
        new_info = self._copy_infodict(info)
        if new_info.get("http_headers") is None:
            new_info["http_headers"] = self._calc_headers(new_info)
        return fd.download(name, new_info, subtitle)
