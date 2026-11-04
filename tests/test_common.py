"""scripts/_common.py: transcript I/O, tier parsing, stem matching, config/model resolution."""
import json

import pytest

import _common as c


# ---- fmt_hms ---------------------------------------------------------------
@pytest.mark.parametrize("sec,want", [
    (0, "00:00"), (59.9, "00:59"), (60, "01:00"), (3599.9, "59:59"),
    (3600, "1:00:00"), (3725, "1:02:05"), (-5, "00:00"),
])
def test_fmt_hms(sec, want):
    assert c.fmt_hms(sec) == want


# ---- load_segments ----------------------------------------------------------
def test_load_segments_accepts_both_shapes(tmp_path):
    segs = [{"start": 0, "end": 1, "text": "嗨"}]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(segs), encoding="utf-8")
    b.write_text(json.dumps({"segments": segs}), encoding="utf-8")
    assert c.load_segments(str(a)) == segs
    assert c.load_segments(str(b)) == segs


def test_load_segments_missing_file_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        c.load_segments(str(tmp_path / "nope.json"))
    assert e.value.code == 2
    assert "not found" in capsys.readouterr().err


def test_load_segments_bad_json_and_bad_shape_exit_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        c.load_segments(str(bad))
    assert e.value.code == 2
    odd = tmp_path / "odd.json"
    odd.write_text('{"segments": "x"}', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        c.load_segments(str(odd))
    assert e.value.code == 2


def test_load_segments_path_with_space_and_cjk(tmp_path):
    d = tmp_path / "講座 一 (final)"
    d.mkdir()
    p = d / "逐字稿.json"
    p.write_text("[]", encoding="utf-8")
    assert c.load_segments(str(p)) == []


# ---- write_transcript_lines / atomic_write_json ----------------------------
def test_write_transcript_lines_format_and_idempotent(tmp_path):
    segs = [{"start": 0, "text": "  你好  "}, {"start": 3725.2, "text": None}, {"text": "x"}]
    p = tmp_path / "t.txt"
    c.write_transcript_lines(segs, str(p))
    assert p.read_text(encoding="utf-8") == "[00:00] 你好\n[1:02:05] \n[00:00] x\n"
    first = p.read_text(encoding="utf-8")
    c.write_transcript_lines(segs, str(p))
    assert p.read_text(encoding="utf-8") == first


def test_atomic_write_json_keeps_cjk_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "s.json"
    c.atomic_write_json(str(p), {"講者": "胡婉妍"})
    assert "胡婉妍" in p.read_text(encoding="utf-8")        # ensure_ascii=False
    assert json.loads(p.read_text(encoding="utf-8")) == {"講者": "胡婉妍"}
    assert not (tmp_path / "s.json.tmp").exists()


def test_atomic_write_json_failure_keeps_old_file(tmp_path):
    p = tmp_path / "s.json"
    c.atomic_write_json(str(p), {"ok": 1})
    with pytest.raises(TypeError):
        c.atomic_write_json(str(p), {"bad": object()})
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": 1}


# ---- parse_tier --------------------------------------------------------------
@pytest.mark.parametrize("val,want", [
    (1, 1), (2, 2), (3, 3), ("1", 1), ("T1", 1), ("t2", 2), ("T1 核心", 1),
    ("tier 2", 2), ("Tier 3", 3), (1.0, 1), (2.9, 2),
])
def test_parse_tier_ok(val, want):
    assert c.parse_tier(val) == want


def test_parse_tier_none_uses_default():
    assert c.parse_tier(None) == 3
    assert c.parse_tier(None, default=2) == 2


@pytest.mark.parametrize("val", ["", "核心", "T", True, False, "T0", [], {}, float("nan"), float("inf"), float("-inf")])
def test_parse_tier_unparseable_keeps_slide(val, capsys):
    assert c.parse_tier(val) == 1
    assert "WARNING" in capsys.readouterr().err


# ---- match_stem ---------------------------------------------------------------
def test_match_stem_exact_beats_substring():
    idx = {"clip1": "A", "clip10": "B"}
    assert c.match_stem("clip1", idx) == "A"
    assert c.match_stem("clip10", idx) == "B"


def test_match_stem_unique_substring():
    assert c.match_stem("clip1_asr", {"clip1": "A", "other": "B"}) == "A"


def test_match_stem_ambiguous_returns_none_and_warns(capsys):
    assert c.match_stem("clip1", {"clip1_a": "A", "clip1_b": "B"}) is None
    assert "ambiguously" in capsys.readouterr().err


def test_match_stem_absent():
    assert c.match_stem("zzz", {"clip1": "A"}) is None


# ---- load_config -----------------------------------------------------------------
def test_load_config_missing_returns_empty(tmp_path):
    assert c.load_config(str(tmp_path)) == {}


def test_load_config_reads_cjk_yaml_and_empty_file(tmp_path):
    (tmp_path / "config.yaml").write_text("paths:\n  vault_inbox_dir: 收件匣\n", encoding="utf-8")
    assert c.load_config(str(tmp_path))["paths"]["vault_inbox_dir"] == "收件匣"
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert c.load_config(str(tmp_path), "empty.yaml") == {}


# ---- resolve_model / resolve_pause_flag --------------------------------------------
def test_resolve_model_plain_name_passthrough():
    assert c.resolve_model("large-v3", {}) == "large-v3"


def test_resolve_model_alias_with_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("LECTURE_WHISPER_MODEL_DIR", raising=False)
    (tmp_path / "m").mkdir()
    cfg = {"models": {"aliases": {"x": "{whisper_model_dir}/m"}},
           "paths": {"whisper_model_dir": str(tmp_path) + "/"}}
    assert c.resolve_model("x", cfg) == f"{tmp_path}/m"      # trailing slash trimmed


def test_resolve_model_env_overrides_config(tmp_path, monkeypatch):
    (tmp_path / "m").mkdir()
    monkeypatch.setenv("LECTURE_WHISPER_MODEL_DIR", str(tmp_path))
    cfg = {"models": {"aliases": {"x": "{whisper_model_dir}/m"}},
           "paths": {"whisper_model_dir": "/does/not/exist"}}
    assert c.resolve_model("x", cfg) == f"{tmp_path}/m"


def test_resolve_model_alias_without_dir_exits(monkeypatch):
    monkeypatch.delenv("LECTURE_WHISPER_MODEL_DIR", raising=False)
    with pytest.raises(SystemExit) as e:
        c.resolve_model("x", {"models": {"aliases": {"x": "{whisper_model_dir}/m"}}})
    assert e.value.code == 2


def test_resolve_model_missing_path_exits(monkeypatch, tmp_path):
    monkeypatch.delenv("LECTURE_WHISPER_MODEL_DIR", raising=False)
    with pytest.raises(SystemExit) as e:
        c.resolve_model(str(tmp_path / "nope"), {})
    assert e.value.code == 2


def test_resolve_pause_flag_precedence(monkeypatch):
    monkeypatch.delenv("LECTURE_PAUSE_FLAG", raising=False)
    assert c.resolve_pause_flag(None) is None
    assert c.resolve_pause_flag({"paths": {"pause_flag": "  "}}) is None
    assert c.resolve_pause_flag({"paths": {"pause_flag": "/a"}}) == "/a"
    monkeypatch.setenv("LECTURE_PAUSE_FLAG", " /env ")
    assert c.resolve_pause_flag({"paths": {"pause_flag": "/a"}}) == "/env"
