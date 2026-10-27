# vidkeep

Fast, size-aware video downloads for offline viewing, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

yt-dlp does the hard part: reading YouTube and hundreds of other sites. vidkeep adds three things on top:

- **Parallel range downloads.** A video or audio stream that is a single file is fetched over several
  connections at once, in bounded 10 MiB ranges, written in place, and resumed range by range after an
  interruption.
- **Quality picked to fit your disk.** `--max-size 1.5G` gets the best resolution and codec whose video
  plus audio fits in 1.5 GiB. `vidkeep info` lists every quality with its real download size.
- **No half-finished downloads on a full disk.** Before starting, vidkeep checks free space for the
  download *and* the merge step, which briefly needs room for both copies.

## Speed

Measured on one home connection with `vidkeep bench`, which downloads a sample from a different offset
of the same YouTube stream for each connection count. One connection is exactly what plain yt-dlp does.

| connections | run 1 (60 MiB each) | run 2 (40 MiB each, interleaved) |
|------------:|--------------------:|---------------------------------:|
| 1           | 0.41 MiB/s          | 0.43, then 1.10 MiB/s            |
| 2           | 0.96 MiB/s          | 5.05 MiB/s                       |
| 4           | 1.64 MiB/s          | 0.85, then 6.82 MiB/s            |
| 6           | –                   | 6.98 MiB/s                       |
| 8           | 0.92 MiB/s          | –                                |

Throughput swings a lot from minute to minute and from video to video (popular videos sit on closer
servers), so compare rows within a run, not across runs. Within each run, four connections beat one by
roughly 4x to 6x; eight were slower than four. Your line and location differ: run
`vidkeep bench URL` and pass the winner as `-n`.

Ranges are always bounded (`Range: bytes=start-end`). Open-ended ranges are what YouTube throttles
to playback speed, which is why a generic accelerator such as aria2 can end up slower than yt-dlp alone.

## Install

vidkeep needs Python 3.10+, [ffmpeg](https://ffmpeg.org) to join video and audio, and a JavaScript
runtime ([deno](https://deno.com), or node) that yt-dlp uses to answer YouTube's challenges.

```sh
brew install ffmpeg deno                      # macOS; use your package manager elsewhere
pipx install git+https://github.com/RootX22/vidkeep
vidkeep doctor                                # checks ffmpeg, the runtime, yt-dlp's age and free space
```

`uv tool install git+https://github.com/RootX22/vidkeep` works too.

## Use

```sh
vidkeep info "https://www.youtube.com/watch?v=VIDEO"          # every quality, with sizes
vidkeep get  "https://www.youtube.com/watch?v=VIDEO"          # 1080p, smallest codec, mp4
vidkeep get URL --max-size 1.5G                               # best quality that fits in 1.5 GiB
vidkeep get URL -q 720 -c compatible                          # H.264: plays on every device
vidkeep get URL -i                                            # pick from a menu
vidkeep get URL --subs en,ar -o ~/Movies/Courses              # embed subtitles, keep .srt copies
vidkeep get URL -a --audio-format mp3                         # audio only
vidkeep get PLAYLIST_URL -j 3 --archive done.txt              # a playlist, 3 videos at a time
vidkeep get --batch urls.txt --dry-run                        # show the plan, download nothing
```

`vidkeep info` on an 11.5-hour course:

```text
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ quality ┃ codec ┃       size ┃ QuickTime   ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 1080p   │ av1   │   1.75 GiB │ M3 or newer │
│ 1080p   │ vp9   │   2.73 GiB │ no          │
│ 1080p   │ h264  │   2.82 GiB │ yes         │
│ 720p    │ av1   │   1.30 GiB │ M3 or newer │
│ ...     │       │            │             │
```

Sizes include the audio. The QuickTime column says whether the file opens in macOS's built-in player:
AV1 needs an Apple M3 chip or newer, VP9 needs a player such as IINA or VLC.

Every download embeds the title, description, chapters and cover image unless `--no-metadata` or
`--no-thumbnail` is given. Playlists are saved into a folder named after the playlist, numbered in order.

### Choosing the codec

| `--codec`     | picks                                     | when                                      |
|---------------|-------------------------------------------|-------------------------------------------|
| `efficient`   | AV1, then VP9, then H.264 (the default)   | smallest file for the picture             |
| `compatible`  | H.264 only                                | phones, TVs, older Macs, editing software |
| `av1` `vp9` `h264` `h265` | that codec only               | you know what you need                    |

### All commands

| command                  | does                                                                 |
|--------------------------|----------------------------------------------------------------------|
| `vidkeep get URL...`     | download videos, playlists or a `--batch` file of URLs               |
| `vidkeep info URL`       | title, length, chapters, subtitles and every quality with its size (`--json` too) |
| `vidkeep doctor`         | check ffmpeg, ffprobe, a JavaScript runtime, yt-dlp's age, free space |
| `vidkeep bench URL`      | time 1, 2, 4, 6 and 8 connections on your network                    |

`vidkeep get --help` lists every option: `--jobs`, `--connections`, `--chunk-size`, `--limit-rate`,
`--container`, `--template`, `--split-chapters`, `--cookies-from-browser`, `--force`, `--verbose`.

### Interrupting and resuming

Press Ctrl+C at any time and run the same command again. Finished 10 MiB ranges are recorded next to
the partial file and are not downloaded twice. A lock stops two vidkeep processes from writing the same
file.

## How it works

1. yt-dlp extracts the video and lists its streams.
2. The planner pairs a video stream with an audio stream by height, codec preference and size cap,
   preferring single-file streams (exact size, parallel-capable) over segmented ones.
3. vidkeep subclasses `YoutubeDL` and routes single-file HTTP streams of 20 MiB or more to its own
   downloader, a `FileDownloader` subclass. It honours the wait YouTube now imposes before a stream URL
   may be used, sends the same headers yt-dlp would, accepts only `206` replies whose `Content-Range`
   matches the request, and retries a broken range from its last written byte.
4. Everything else stays yt-dlp's: file names, merging, chapters, subtitles, cover art and archives are
   configured by passing yt-dlp's own command-line flags through `yt_dlp.parse_options`.

## Limits

- No time-range downloads. yt-dlp hands those to ffmpeg, whose open-ended reads YouTube stalls; in
  testing a 3-minute clip of a long video never finished, with or without vidkeep.
- No DRM-protected or YouTube Premium-only formats.
- Age-restricted or members-only videos need `--cookies-from-browser` with a browser where you are
  signed in.
- YouTube changes often. When downloads start failing, update yt-dlp first:
  `pipx inject vidkeep -U 'yt-dlp[default]'` (or `pip install -U 'yt-dlp[default]'`).

Download only what you have the right to keep, and follow the terms of the sites you use.

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest                    # offline: parallel ranges against a local HTTP server, planner, options, CLI
pytest -m network         # end to end against YouTube (Big Buck Bunny, CC BY 3.0)
```

## License

MIT
