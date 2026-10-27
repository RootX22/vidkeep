from fixtures import course_info

from vidkeep import options, planner
from vidkeep.options import Settings, download_args, to_params


def plan(**kwargs):
    return planner.choose(course_info(), planner.Request(**kwargs))


def keys(params):
    return [pp["key"] for pp in params.get("postprocessors", [])]


def test_video_download_is_merged_with_chapters_metadata_and_cover():
    args = download_args(plan(), Settings(out_dir="/videos"))
    params = to_params(args, Settings())
    assert params["format"] == "399+140"
    assert params["merge_output_format"] == "mp4"
    assert params["paths"]["home"] == "/videos"
    assert {"FFmpegMetadata", "EmbedThumbnail"} <= set(keys(params))
    metadata = next(pp for pp in params["postprocessors"] if pp["key"] == "FFmpegMetadata")
    assert metadata["add_chapters"] and metadata["add_metadata"]


def test_parallel_settings_reach_the_downloader():
    params = to_params(download_args(plan(), Settings()), Settings(connections=6, chunk_size=1024))
    assert params["vidkeep_connections"] == 6
    assert params["vidkeep_chunk_size"] == 1024


def test_subtitles_are_written_converted_and_embedded():
    params = to_params(download_args(plan(), Settings(subs="en,ar")), Settings())
    assert params["writesubtitles"] and params["writeautomaticsub"]
    assert params["subtitleslangs"] == ["en", "ar"]
    assert {"FFmpegSubtitlesConvertor", "FFmpegEmbedSubtitle"} <= set(keys(params))


def test_audio_only_in_its_own_container_is_not_re_encoded():
    args = download_args(plan(audio_only=True), Settings(audio_format="m4a"))
    assert "-x" not in args
    assert "FFmpegExtractAudio" not in keys(to_params(args, Settings()))


def test_audio_only_to_mp3_converts():
    params = to_params(download_args(plan(audio_only=True), Settings(audio_format="mp3")), Settings())
    extract = next(pp for pp in params["postprocessors"] if pp["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == "mp3"


def test_audio_only_never_embeds_subtitles_or_chapters():
    args = download_args(plan(audio_only=True), Settings(subs="en"))
    assert "--embed-subs" not in args and "--embed-chapters" not in args


def test_opus_gets_cover_art_like_the_other_audio_formats():
    args = download_args(plan(audio_only=True), Settings(audio_format="opus"))
    assert "--embed-thumbnail" in args


def test_containers_that_cannot_hold_extras_are_not_asked_to():
    # An Internet Archive .avi: yt-dlp fails the whole download if asked to embed a cover into it.
    avi = planner.Plan(muxed=planner.Stream("2", "muxed", 332_243_668, True, "https", "avi", 720))
    args = download_args(avi, Settings(subs="en"))
    assert not {"--embed-thumbnail", "--embed-chapters", "--embed-subs"} & set(args)
    assert "--embed-metadata" in args and "--write-subs" in args


def test_final_extension():
    assert options.final_ext(plan(), Settings(container="mkv")) == "mkv"
    assert options.final_ext(plan(audio_only=True), Settings(audio_format="mp3")) == "mp3"
    assert options.final_ext(plan(audio_only=True), Settings(audio_format="m4a")) == "m4a"


def test_archive_rate_and_template():
    settings = Settings(archive="seen.txt", limit_rate="2M", template="%(id)s.%(ext)s")
    params = to_params(download_args(plan(), settings, template="list/001 - %(id)s.%(ext)s"), settings)
    assert params["download_archive"] == "seen.txt"
    assert params["ratelimit"] == 2 * 1024 * 1024
    assert params["outtmpl"]["default"] == "list/001 - %(id)s.%(ext)s"


def test_split_chapters_writes_numbered_files():
    params = to_params(download_args(plan(), Settings(split_chapters=True)), Settings())
    assert "FFmpegSplitChapters" in keys(params)
    assert "%(section_number)03d" in params["outtmpl"]["chapter"]


def test_disabling_extras():
    args = download_args(plan(), Settings(metadata=False, thumbnail=False))
    assert not {"--embed-metadata", "--embed-chapters", "--embed-thumbnail"} & set(args)


def test_extraction_flags_carry_cookies_and_runtime():
    settings = Settings(cookies_from_browser="firefox", js_runtime_args=("--js-runtimes", "node"))
    params = to_params(options.base_args(settings), settings)
    assert params["cookiesfrombrowser"][0] == "firefox"
    assert "node" in params["js_runtimes"]
