"""crop_slide_region rule parsing and run_lecture identity handling.

The cloud box has neither cv2 nor the heavy ASR/OCR packages, so those imports are
stubbed *only if missing*; nothing here touches images, audio or the network."""
import importlib
import importlib.machinery
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


def _import_crop():
    if "cv2" not in sys.modules and importlib.util.find_spec("cv2") is None:
        stub = types.ModuleType("cv2")
        stub.__spec__ = importlib.machinery.ModuleSpec("cv2", None)   # keeps find_spec() happy
        sys.modules["cv2"] = stub
    return importlib.import_module("crop_slide_region")


def _import_run_lecture(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    try:
        return importlib.import_module("run_lecture")
    finally:
        monkeypatch.setattr(importlib.util, "find_spec", real)


@pytest.fixture
def crop():
    return _import_crop()


def lec_with(tmp_path, data):
    lec = tmp_path / "講座 一" / "_out"
    lec.mkdir(parents=True)
    (lec / "lecture.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return lec


# ---- crop_slide_region.lecture_dir / load_rules / box_for / sample ---------------------
def test_lecture_dir_finds_folder_or_its_out(crop, tmp_path):
    lec = lec_with(tmp_path, {})
    assert crop.lecture_dir(lec) == lec
    assert crop.lecture_dir(lec.parent) == lec
    with pytest.raises(ValueError, match="lecture.json not found"):
        crop.lecture_dir(tmp_path)


def test_load_rules_valid(crop, tmp_path):
    lec = lec_with(tmp_path, {"slide_crop": [
        {"slides": "1-2", "box": [0, 0, 200, 200]}, {"slides": "5", "box": [10, 20, 640, 360]}]})
    assert crop.load_rules(lec) == [(1, 2, [0, 0, 200, 200]), (5, 5, [10, 20, 640, 360])]


def test_load_rules_absent_or_empty_means_no_crop(crop, tmp_path):
    assert crop.load_rules(lec_with(tmp_path, {})) == []
    assert crop.load_rules(lec_with(tmp_path / "b", {"slide_crop": []})) == []


@pytest.mark.parametrize("rule,msg", [
    ({"slides": "0-3", "box": [0, 0, 300, 300]}, "positive range"),
    ({"slides": "3-1", "box": [0, 0, 300, 300]}, "ordered range"),
    ({"slides": "a", "box": [0, 0, 300, 300]}, "positive range"),
    ({"box": [0, 0, 300, 300]}, "positive range"),
    ({"slides": "1-3", "box": [0, 0, 300]}, "four integers"),
    ({"slides": "1-3", "box": [0, 0, 300.0, 300]}, "four integers"),
    ({"slides": "1-3", "box": [0, 0, True, 300]}, "four integers"),
    ({"slides": "1-3", "box": "0,0,300,300"}, "four integers"),
    ({"slides": "1-3", "box": [-1, 0, 300, 300]}, "ordered range"),
    ({"slides": "1-3", "box": [0, 0, 199, 300]}, "ordered range"),
    ("1-3", "expected slides and box"),
])
def test_load_rules_rejects_bad_rule(crop, tmp_path, rule, msg):
    lec = lec_with(tmp_path, {"slide_crop": [rule]})
    with pytest.raises(ValueError, match=msg):
        crop.load_rules(lec)


def test_load_rules_rejects_overlap_and_non_list(crop, tmp_path):
    a = {"slides": "1-5", "box": [0, 0, 300, 300]}
    with pytest.raises(ValueError, match="overlapping"):
        crop.load_rules(lec_with(tmp_path, {"slide_crop": [a, {"slides": "5-8", "box": [0, 0, 300, 300]}]}))
    with pytest.raises(ValueError, match="must be a list"):
        crop.load_rules(lec_with(tmp_path / "b", {"slide_crop": {"slides": "1"}}))


def test_load_rules_adjacent_ranges_are_fine(crop, tmp_path):
    lec = lec_with(tmp_path, {"slide_crop": [
        {"slides": "1-4", "box": [0, 0, 300, 300]}, {"slides": "5-8", "box": [0, 0, 300, 300]}]})
    assert len(crop.load_rules(lec)) == 2


def test_box_for(crop):
    rules = [(1, 3, [0, 0, 300, 300]), (7, 9, [1, 1, 400, 400])]
    assert crop.box_for(1, rules) == [0, 0, 300, 300]
    assert crop.box_for(3, rules) == [0, 0, 300, 300]
    assert crop.box_for(4, rules) is None
    assert crop.box_for(9, rules) == [1, 1, 400, 400]
    assert crop.box_for(1, []) is None


def test_sample_spacing_and_edges(crop):
    rows = list(range(10))
    assert crop.sample(rows, 20) == rows
    assert crop.sample(rows, 2) == [0, 9]
    assert crop.sample(rows, 3) == [0, 4, 9]          # round(4.5) -> 4 (banker's rounding)
    assert crop.sample(rows, 1) == [0]


def test_lecture_frames_uses_note_slide_ids_and_skips_missing(crop, tmp_path):
    lec = lec_with(tmp_path, {})
    (lec / "slides_final.json").write_text(json.dumps([
        {"slide_id": 9, "filename": "f9.jpg"},
        {"slide_id": 2, "filename": "f2.jpg"},
        {"slide_id": 5, "filename": "f5.jpg", "missing_frame": True},
        {"slide_id": 6},
    ]), encoding="utf-8")
    assert crop.lecture_frames(lec) == [(2, lec / "slides" / "f2.jpg"), (9, lec / "slides" / "f9.jpg")]


def test_lecture_frames_without_metadata_raises(crop, tmp_path):
    with pytest.raises(ValueError, match="No slide metadata"):
        crop.lecture_frames(lec_with(tmp_path, {}))


def test_check_box(crop):
    class Img:
        shape = (450, 800, 3)
    crop.check_box(Img, [0, 0, 800, 450], "s01")
    with pytest.raises(ValueError, match="outside original image"):
        crop.check_box(Img, [1, 0, 800, 450], "s01")


def test_frame_files_filters_by_regex(crop, tmp_path):
    for n in ("講座_s01.jpg", "講座_s02.jpg", "notes.txt", "cover.jpg"):
        (tmp_path / n).write_bytes(b"")
    (tmp_path / "_orig").mkdir()
    names = [p.name for p in crop.frame_files(tmp_path)]
    assert names == ["講座_s01.jpg", "講座_s02.jpg"]


def test_restore_moves_originals_back_and_removes_backup(crop, tmp_path, capsys):
    folder = tmp_path / "附件"
    (folder / "_orig").mkdir(parents=True)
    (folder / "x_s01.jpg").write_bytes(b"cropped")
    (folder / "_orig" / "x_s01.jpg").write_bytes(b"original")
    crop.restore(folder)
    assert (folder / "x_s01.jpg").read_bytes() == b"original"
    assert not (folder / "_orig").exists()
    assert "Restored 1" in capsys.readouterr().out


# ---- run_lecture.require_identity ------------------------------------------------------------
@pytest.fixture
def rl(monkeypatch):
    return _import_run_lecture(monkeypatch)


def test_require_identity_stops_when_fields_missing(rl, tmp_path):
    (tmp_path / "lecture.json").write_text(json.dumps({"speaker": "胡婉妍"}), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        rl.require_identity(tmp_path)
    assert "topic" in str(e.value) and "date" in str(e.value) and "speaker" not in str(e.value).split("missing")[1].split("]")[0]


def test_require_identity_without_lecture_json(rl, tmp_path):
    with pytest.raises(SystemExit):
        rl.require_identity(tmp_path)


def test_require_identity_derives_names_and_respects_overrides(rl, tmp_path):
    (tmp_path / "lecture.json").write_text(json.dumps(
        {"speaker": "胡婉妍", "topic": "流感疫苗", "date": "20261004", "note_name": "custom.md"},
        ensure_ascii=False), encoding="utf-8")
    info = rl.require_identity(tmp_path)
    assert info["slug"] == "20261004_胡婉妍_流感疫苗"
    assert info["note_name"] == "custom.md"
    assert info["prefix"] == "胡婉妍_流感疫苗"


def test_lecture_json_roundtrip_keeps_cjk(rl, tmp_path):
    rl.save_lecture_json(tmp_path, {"speaker": "胡婉妍"})
    assert "胡婉妍" in (tmp_path / "lecture.json").read_text(encoding="utf-8")
    assert rl.lecture_json(tmp_path) == {"speaker": "胡婉妍"}


def test_vault_root_prefers_config_then_env(rl, monkeypatch):
    monkeypatch.setenv("CLAUDE_VAULT_ROOT", "/env/vault")
    assert rl.vault_root({"paths": {"vault_root": " /cfg/vault "}}) == Path("/cfg/vault")
    assert rl.vault_root({}) == Path("/env/vault")
    monkeypatch.delenv("CLAUDE_VAULT_ROOT")
    assert rl.vault_root({}) is None


def test_attach_rel_substitutes_slug(rl, monkeypatch):
    monkeypatch.setattr(rl, "load_config", lambda: {"paths": {"vault_attach_pattern": "99-Attachment/lecture_{slug}"}})
    assert rl.attach_rel({"slug": "s1"}) == "99-Attachment/lecture_s1"
    monkeypatch.setattr(rl, "load_config", lambda: {})
    assert rl.attach_rel({"slug": "s1"}) == "99Attachment/lecture_s1"


@pytest.mark.parametrize("field,bad", [("topic", "COVID/Flu"), ("speaker", "A:B"), ("topic", 'q?"x')])
def test_require_identity_rejects_filename_unsafe_chars(rl, tmp_path, field, bad):
    data = {"speaker": "A", "topic": "T", "date": "20261004", field: bad}
    (tmp_path / "lecture.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        rl.require_identity(tmp_path)
    assert field in str(e.value) and "not allowed" in str(e.value)
