# Firefox Add-ons listing

Text for the vidkeep add-on's page on addons.mozilla.org. Upload the zip that CI builds
(`vidkeep-firefox-<version>.zip`, from the `firefox-addon` job or a release).

## Name

vidkeep

## Summary

Save the video you are watching for offline viewing with vidkeep on your computer: every quality
with its real size, downloads that keep going after the popup closes, stop and resume.

## Description

vidkeep is a free, open-source video downloader built on yt-dlp. This add-on is its button in
Firefox.

Requires the vidkeep program on your computer (macOS or Linux). Setup takes two commands:
pipx install git+https://github.com/RootX22/vidkeep, then vidkeep browser install.
The add-on shows these steps until vidkeep is connected.

• Every quality with its real download size, and whether it plays in QuickTime.
• Downloads run in vidkeep, not in the browser: they keep going after you close the popup, and
  can be stopped and resumed from where they stopped.
• Fast: vidkeep downloads a file over several connections at once.
• Subtitles, chapters and the cover image saved into the file.
• Playlists: choose the highest quality and whether files should be small or play everywhere.
• Works with the sites yt-dlp supports.

Download only what you have the right to keep, and follow the terms of the sites you use.

Source code and help: https://github.com/RootX22/vidkeep

## Category and tags

Category: Download Management. Tags: download, video, offline, yt-dlp.

## Notes to reviewer

The package is the source; nothing is minified or bundled. It is the `browser/firefox` folder of
https://github.com/RootX22/vidkeep (MIT).

The add-on talks only to the native host `io.github.rootx22.vidkeep`, which is part of the same
repository (`vidkeep/native.py`) and is installed by the user with `vidkeep browser install`. The
add-on makes no network requests itself; thumbnails shown in the popup are loaded from the URL the
site gives.

Data collection: none. The page address goes only to the native host on the user's own computer,
when the user opens the popup on that page.

To try it: on macOS or Linux run `pipx install git+https://github.com/RootX22/vidkeep` and
`vidkeep browser install` (needs ffmpeg), then open a video page and click the toolbar button.
