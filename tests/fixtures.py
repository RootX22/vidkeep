"""Format lists shaped like what yt-dlp returns for a long YouTube video."""

GiB = 1024**3
MiB = 1024**2


def video(fid, height, vcodec, size, tbr, protocol="https", note=None, exact=True):
    fmt = {
        "format_id": fid,
        "height": height,
        "vcodec": vcodec,
        "acodec": "none",
        "protocol": protocol,
        "ext": "mp4",
        "tbr": tbr,
        "format_note": note or f"{height}p",
    }
    fmt["filesize" if exact else "filesize_approx"] = size
    return fmt


def audio(fid, acodec, ext, size, abr, language_preference=10):
    return {
        "format_id": fid,
        "vcodec": "none",
        "acodec": acodec,
        "ext": ext,
        "protocol": "https",
        "filesize": size,
        "abr": abr,
        "tbr": abr,
        "language_preference": language_preference,
    }


def course_info():
    return {
        "id": "Kb-sw00KJ10",
        "title": "Course",
        "duration": 41501,
        "formats": [
            {"format_id": "sb0", "ext": "mhtml", "protocol": "mhtml", "vcodec": "none", "acodec": "none"},
            audio("139", "mp4a.40.5", "m4a", 241 * MiB, 49),
            audio("140", "mp4a.40.2", "m4a", 640 * MiB, 129),
            audio("140-drc", "mp4a.40.2", "m4a", 640 * MiB, 129),
            audio("251", "opus", "webm", 687 * MiB, 139),
            audio("140-1", "mp4a.40.2", "m4a", 600 * MiB, 129, language_preference=-1),  # a dub
            video("398", 720, "av01.0.05M.08", 687 * MiB, 139),
            video("136", 720, "avc1.64001F", int(1.14 * GiB), 235),
            video("399", 1080, "av01.0.08M.08", int(1.12 * GiB), 233),
            video("248", 1080, "vp9", int(2.11 * GiB), 436),
            video("137", 1080, "avc1.640028", int(2.20 * GiB), 455),
            video("614", 1080, "vp09.00.40.08", int(13.4 * GiB), 2767, protocol="m3u8_native", exact=False),
            video(
                "616", 1080, "vp09.00.40.08", int(21 * GiB), 4423, protocol="m3u8_native", note="Premium", exact=False
            ),
            {
                "format_id": "18",
                "height": 360,
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "protocol": "https",
                "ext": "mp4",
                "filesize": 651 * MiB,
                "tbr": 132,
            },
        ],
    }
