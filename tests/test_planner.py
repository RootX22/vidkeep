import pytest
from fixtures import GiB, MiB, audio, course_info, video

from vidkeep import planner
from vidkeep.planner import Request, choose


def spec(**kwargs):
    return choose(course_info(), Request(**kwargs)).format_spec


class TestChoose:
    def test_default_is_1080_in_the_most_efficient_codec(self):
        assert spec() == "399+140"

    def test_compatible_means_h264(self):
        assert spec(codec="compatible") == "137+140"

    def test_height_cap(self):
        assert spec(max_height=720) == "398+140"

    def test_best_takes_the_tallest(self):
        assert spec(max_height=None) == "399+140"

    def test_size_cap_drops_codec_then_height(self):
        # 1080p AV1 with medium audio is 1.75 GiB, so 1.5 GiB falls to 720p AV1.
        assert spec(max_size=int(1.5 * GiB)) == "398+140"

    def test_size_cap_with_low_audio_keeps_1080(self):
        assert spec(max_size=int(1.5 * GiB), audio_quality="low") == "399+139"

    def test_size_cap_tries_every_codec_at_a_height_before_going_lower(self):
        info = course_info()
        info["formats"] = [f for f in info["formats"] if f["format_id"] != "399"]
        # Without AV1, 1080p VP9 plus audio (2.73 GiB) fits in 2.8 GiB; H.264 (2.83 GiB) would not.
        assert choose(info, Request(max_size=int(2.8 * GiB))).format_spec == "248+140"

    def test_single_files_beat_streamed_variants_of_the_same_height(self):
        assert spec(codec="vp9") == "248+140"  # not the m3u8 614, whatever its bitrate

    def test_premium_drc_storyboard_and_dubs_are_never_picked(self):
        chosen = {s.format_id for s in planner.streams(course_info())}
        assert not chosen & {"616", "140-drc", "sb0"}
        assert spec(audio_quality="medium").endswith("+140")

    @pytest.mark.parametrize("quality,expected", [("low", "139"), ("medium", "140"), ("high", "140")])
    def test_audio_only(self, quality, expected):
        assert spec(audio_only=True, audio_quality=quality) == expected

    def test_high_audio_without_a_container_preference_takes_opus(self):
        info = course_info()
        plan = choose(info, Request(audio_only=True, audio_quality="high", prefer_audio_ext=None))
        assert plan.format_spec == "251"

    def test_muxed_streams_when_nothing_is_separate(self):
        info = course_info()
        info["formats"] = [f for f in info["formats"] if f["format_id"] == "18"]
        assert choose(info, Request()).format_spec == "18"

    def test_no_codec_match_suggests_efficient(self):
        info = {
            "duration": 10,
            "formats": [video("399", 1080, "av01.0.08M.08", 10 * MiB, 233), audio("140", "mp4a.40.2", "m4a", MiB, 129)],
        }
        with pytest.raises(planner.NoMatch, match="--codec efficient"):
            choose(info, Request(codec="compatible"))

    def test_nothing_fits_names_the_smallest_option(self):
        with pytest.raises(planner.NoMatch, match="smallest option is 720p av1"):
            choose(course_info(), Request(max_size=100 * MiB))

    def test_unknown_codec_is_rejected(self):
        with pytest.raises(ValueError):
            choose(course_info(), Request(codec="mpeg2"))


class TestPlan:
    def test_size_and_merge(self):
        plan = choose(course_info(), Request())
        assert plan.size == int(1.12 * GiB) + 640 * MiB
        assert plan.merges and plan.exact
        assert plan.describe() == "1080p av1"

    def test_unknown_size_makes_the_total_unknown(self):
        plan = planner.Plan(video=planner.Stream("1", "video", None, False, "https", "mp4", 720, "h264"))
        assert plan.size is None

    def test_estimated_sizes_fall_back_to_bitrate_times_duration(self):
        fmt = {"format_id": "x", "vcodec": "avc1", "acodec": "none", "height": 720, "tbr": 800, "protocol": "m3u8"}
        stream = planner.streams({"duration": 100, "formats": [fmt]})[0]
        assert stream.size == 800 * 1000 // 8 * 100
        assert not stream.exact


class TestMenu:
    def test_rows_tallest_first_in_efficiency_order(self):
        rows = planner.menu(course_info())
        assert [(r.plan.picture.height, r.plan.picture.codec) for r in rows] == [
            (1080, "av1"),
            (1080, "vp9"),
            (1080, "h264"),
            (720, "av1"),
            (720, "h264"),
        ]

    def test_quicktime_column(self):
        rows = {r.plan.picture.codec: r.quicktime for r in planner.menu(course_info())}
        assert rows == {"av1": "M3 or newer", "vp9": "no", "h264": "yes"}


class TestSizes:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("700M", 700 * MiB),
            ("1.5G", int(1.5 * GiB)),
            ("2GiB", 2 * GiB),
            ("512k", 512 * 1024),
            ("1000", 1000),
            (" 3 gb ", 3 * GiB),
        ],
    )
    def test_parse(self, text, expected):
        assert planner.parse_size(text) == expected

    @pytest.mark.parametrize("text", ["", "big", "1.5X", "-1G"])
    def test_parse_rejects(self, text):
        with pytest.raises(ValueError):
            planner.parse_size(text)

    @pytest.mark.parametrize(
        "n,expected", [(None, "?"), (500, "500 B"), (1536, "2 KiB"), (5 * MiB, "5.00 MiB"), (3 * GiB, "3.00 GiB")]
    )
    def test_format(self, n, expected):
        assert planner.format_size(n) == expected


@pytest.mark.parametrize(
    "vcodec,family",
    [
        ("av01.0.08M.08", "av1"),
        ("vp09.00.40.08", "vp9"),
        ("vp9", "vp9"),
        ("avc1.640028", "h264"),
        ("hvc1.1.6.L120", "h265"),
        ("none", None),
        (None, None),
    ],
)
def test_codec_family(vcodec, family):
    assert planner.codec_family(vcodec) == family
