"""scripts/fetch_media.py pure helpers (no yt-dlp / ffprobe / network)."""
import json

import pytest

import fetch_media as fm


# ---- parse_time / parse_section ---------------------------------------------
@pytest.mark.parametrize("s,want", [
    ("90", 90.0), ("1:30", 90.0), ("1:02:03", 3723.0), ("0:0.5", 0.5), (" 5 ", 5.0),
])
def test_parse_time_ok(s, want):
    assert fm.parse_time(s) == want


@pytest.mark.parametrize("s", ["", "  ", "1:2:3:4", "a", "1..2", "1:xx"])
def test_parse_time_bad(s):
    with pytest.raises(ValueError):
        fm.parse_time(s)


def test_parse_section_ok_and_star_prefix():
    assert fm.parse_section("60-150") == (60.0, 150.0)
    assert fm.parse_section("1:00 - 2:30") == (60.0, 150.0)
    assert fm.parse_section("*60-150") == (60.0, 150.0)


@pytest.mark.parametrize("spec", ["150-60", "60-60", "abc", "60", "-5-10", "1:00-"])
def test_parse_section_bad_exits_2(spec, capsys):
    with pytest.raises(SystemExit) as e:
        fm.parse_section(spec)
    assert e.value.code == 2
    assert "--section" in capsys.readouterr().err


# ---- safe_name -------------------------------------------------------------------
def test_safe_name_keeps_cjk_drops_shell_chars():
    n = fm.safe_name('胡婉妍: 流感疫苗 "2026" [final] 100%&more', "abc_123")
    assert n.endswith("_abc_123")
    for ch in ' :"[]%&':
        assert ch not in n
    assert "胡婉妍" in n and "流感疫苗" in n


def test_safe_name_truncates_title_not_id():
    n = fm.safe_name("x" * 200, "VID")
    assert n == "x" * 40 + "_VID"


def test_safe_name_fallbacks():
    assert fm.safe_name("", "") == "media"
    assert fm.safe_name("", "abc") == "abc"
    assert fm.safe_name("???", "") == "media"
    assert fm.safe_name(None, None) == "media"
    assert fm.safe_name("title", "a/b\\c") == "title_abc"      # id stripped of separators


def test_safe_name_never_contains_path_separators():
    n = fm.safe_name("../../etc/passwd", "..")
    assert "/" not in n and "\\" not in n and ".." not in n


# ---- _ts / parse_vtt ---------------------------------------------------------------
@pytest.mark.parametrize("s,want", [
    ("00:00:01.500", 1.5), ("01:02.250", 62.25), ("1:00:00,000", 3600.0),
    ("00:00:01.5", 1.5), ("00:00:01.12345", 1.123),
])
def test_ts(s, want):
    assert fm._ts(s) == pytest.approx(want)


def test_ts_bad():
    with pytest.raises(ValueError):
        fm._ts("nonsense")


VTT = """﻿WEBVTT
Kind: captions

1
00:00:01.000 --> 00:00:03.000 align:start position:0%
Hello <c>world</c> &amp; friends

00:00:03.000 --> 00:00:05.000
Hello world &amp; friends
this is new

NOTE ignored block

00:00:05.000 --> 00:00:06.000
this is new
"""


def test_parse_vtt_strips_tags_entities_and_rolling_duplicates():
    segs = fm.parse_vtt(VTT)
    assert [s["text"] for s in segs] == ["Hello world & friends", "this is new"]
    assert segs[0] == {"start": 1.0, "end": 3.0, "text": "Hello world & friends"}
    assert segs[1]["start"] == 3.0


def test_parse_vtt_skips_bad_timestamp_blocks_and_handles_crlf():
    text = "WEBVTT\r\n\r\nxx --> yy\r\nbad\r\n\r\n00:00:01.000 --> 00:00:02.000\r\n你好\r\n"
    assert fm.parse_vtt(text) == [{"start": 1.0, "end": 2.0, "text": "你好"}]


def test_parse_vtt_empty():
    assert fm.parse_vtt("") == []
    assert fm.parse_vtt("WEBVTT\n") == []


# ---- pick_track ------------------------------------------------------------------------
def test_pick_track_manual_beats_auto():
    info = {"subtitles": {"en": [1]}, "automatic_captions": {"zh-Hant": [1]}}
    assert fm.pick_track(info, ["zh-Hant", "en"]) == ("manual", "en")


def test_pick_track_prefix_match_and_live_chat_ignored():
    info = {"subtitles": {"live_chat": [1]}, "automatic_captions": {"zh-Hans": [1]}}
    assert fm.pick_track(info, ["zh"]) == ("auto", "zh-Hans")
    assert fm.pick_track({"subtitles": {"live_chat": [1]}}, ["live_chat"]) is None


def test_pick_track_none_and_empty_lists():
    assert fm.pick_track({}, ["en"]) is None
    assert fm.pick_track({"subtitles": {"en": []}}, ["en"]) is None


def test_write_json_atomic_cjk(tmp_path):
    p = tmp_path / "m.json"
    fm.write_json_atomic(str(p), {"title": "講座"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"title": "講座"}
    assert not (tmp_path / "m.json.tmp").exists()


# ---- do_local_folder (walk only; never picks media) ----------------------------------------
def test_do_local_folder_skips_hidden_and_archive_dirs(tmp_path, monkeypatch):
    (tmp_path / "a b").mkdir()
    (tmp_path / "a b" / "talk.MP4").write_bytes(b"")
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "old.mp4").write_bytes(b"")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x.mp4").write_bytes(b"")
    (tmp_path / "影片筆記整合").mkdir()
    (tmp_path / "影片筆記整合" / "y.mp4").write_bytes(b"")
    (tmp_path / "deck.pdf").write_bytes(b"")
    seen = {}
    monkeypatch.setattr(fm, "final", lambda media, **kw: seen.update(media=media, **kw))
    fm.do_local_folder(str(tmp_path))
    assert seen["media"] is None
    assert seen["counts"] == {"video": 1, "audio": 0, "pdf": 1, "pptx": 0}
