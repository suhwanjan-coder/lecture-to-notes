"""Small pure helpers from ground_slides, render_embeds, flag_asr_suspects,
finalize_to_vault, route_inputs, dedup_semantic."""
import os
import time

import pytest

import dedup_semantic as dd
import finalize_to_vault as fv
import flag_asr_suspects as fl
import ground_slides as gs
import render_embeds as re_
import route_inputs as ri


# ---- ground_slides ------------------------------------------------------------------------
def test_deep_merge_null_keeps_default_and_nested_override():
    base = {"a": {"x": 1, "y": [1]}, "b": 2}
    out = gs.deep_merge(base, {"a": {"y": None, "z": 3}, "b": None, "c": 4})
    assert out == {"a": {"x": 1, "y": [1], "z": 3}, "b": 2, "c": 4}
    assert base == {"a": {"x": 1, "y": [1]}, "b": 2}          # input untouched
    assert gs.deep_merge(base, None) == base


def test_window_segments_overlap_boundaries():
    segs = [{"start": 0, "end": 5}, {"start": 5, "end": 10}, {"start": 11, "end": 12}, {}]
    got = [i for i, _ in gs.window_segments(segs, 5, 10)]
    assert got == [0, 1]            # {} (start=end=0) and the 11-12 segment are outside
    assert [i for i, _ in gs.window_segments(segs, 0, 0)] == [0, 3]


def test_exact_hit_latin_word_start_only():
    assert gs._exact_hit("emotional state", "motion") is False
    assert gs._exact_hit("tendons were seen", "tendon") is True
    assert gs._exact_hit("a motion study", "motion") is True
    assert gs._exact_hit("c++ (stl)", "c++") is True            # non-latin-word kw -> plain substring
    assert gs._exact_hit("流感疫苗效果", "疫苗") is True        # CJK = plain substring


def test_cue_density():
    assert gs.cue_density([], ["x"]) == 0.0
    assert gs.cue_density(["Please NOTE this", "nothing", None], ["note"]) == pytest.approx(1 / 3)


def test_abbreviation_hit_is_case_sensitive_with_boundaries():
    assert gs.abbreviation_hit("Use CT or MRI", "CT") is True
    assert gs.abbreviation_hit("ACTIVE", "CT") is False
    assert gs.abbreviation_hit("ct scan", "CT") is False
    assert gs.abbreviation_hit("(CT)", "CT") is True
    assert gs.abbreviation_hit("VO2max", "VO2") is False
    assert gs.abbreviation_hit("a+b", "a+b") is True     # regex metachars are escaped


def test_cjk_hit_exact_for_short_and_bigram_coverage_for_long():
    runs = ["今天討論流感疫苗"]
    assert gs.cjk_hit(runs, set(), "疫苗", 0.4) is True
    assert gs.cjk_hit(runs, set(), "癌症", 0.4) is False
    big = {"流感", "感疫", "疫苗"}
    assert gs.cjk_hit([], big, "流感疫苗", 0.4) is True            # 3/3
    assert gs.cjk_hit([], {"流感"}, "流感疫苗", 0.4) is False       # 1/3 < 0.4


def test_classify_keyword():
    cfg = {"keyword_grounding": {"abbreviation_whitelist": ["HFrEF"], "drug_suffixes": ["mab"],
                                 "medical_term_suffixes": ["itis"]}}
    assert gs.classify_keyword("流感疫苗", cfg) == "cjk"
    assert gs.classify_keyword("", cfg) is None
    assert gs.classify_keyword("CT", cfg) == "abbreviation"
    assert gs.classify_keyword("hfref", cfg) == "abbreviation"
    assert gs.classify_keyword("rituximab", cfg) == "drug"
    assert gs.classify_keyword("tendinitis", cfg) == "medical_term"
    assert gs.classify_keyword("rehabilitation", cfg) == "general"
    assert gs.classify_keyword("cat", cfg) is None


# ---- render_embeds -------------------------------------------------------------------------------
def test_tier_of_fails_open():
    assert re_.tier_of({"tier": "T2 重要"}) == 2
    assert re_.tier_of({}) == 3
    assert re_.tier_of({"tier": "??"}) == 1


def test_attachment_name_prefers_attachment_name_then_filename_then_fabricates():
    miss = []
    assert re_.attachment_name({"attachment_name": "a.jpg", "filename": "b.jpg"}, miss) == "a.jpg"
    assert re_.attachment_name({"filename": "b.jpg"}, miss) == "b.jpg"
    assert miss == []
    assert re_.attachment_name({"slide_id": "7"}, miss) == "s07.jpg"
    assert miss == [7]


def test_load_final_skips_bad_rows_and_exits_when_missing(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        re_.load_final(str(tmp_path))
    assert e.value.code == 2
    (tmp_path / "slides_final.json").write_text(
        '[{"slide_id": "3", "x": 1}, {"slide_id": "abc"}, {"no": 1}, {"slide_id": null}]', encoding="utf-8")
    assert list(re_.load_final(str(tmp_path))) == [3]


def test_split_caption_short_returns_whole():
    assert re_.split_caption("短標題") == ("短標題", "")
    assert re_.split_caption(None) == ("", "")
    assert re_.split_caption("  x  ") == ("x", "")


def test_split_caption_splits_at_first_separator():
    cap = "加強型流感疫苗的相對保護力——高劑量對標準劑量於65歲以上族群的多國試驗結果摘要"
    short, rest = re_.split_caption(cap)
    assert short == "加強型流感疫苗的相對保護力"
    assert rest.startswith("高劑量")
    assert len(short) <= re_.CAPTION_MAX


def test_split_caption_no_separator_cuts_on_word_boundary_for_latin():
    cap = "Relative vaccine effectiveness of high dose versus standard dose influenza vaccine"
    short, rest = re_.split_caption(cap)
    assert len(short) <= re_.CAPTION_MAX
    assert not short.endswith(" ")
    assert (short + " " + rest).split() == cap.split()           # nothing lost or word-split


def test_split_caption_cjk_without_separator_hard_cuts():
    cap = "字" * 50
    short, rest = re_.split_caption(cap)
    assert short == "字" * re_.CAPTION_MAX and rest == "字" * 16


def test_split_caption_unmatched_brackets_dropped():
    cap = "某個很長的標題用來測試括號處理邏輯是否正確（" + "補充說明" * 5 + "）"
    short, rest = re_.split_caption(cap)
    assert "（" not in short
    assert rest.count("（") == rest.count("）")


# ---- flag_asr_suspects ------------------------------------------------------------------------------
@pytest.mark.parametrize("a,b,cap,want", [
    ("abc", "abc", 2, 0), ("abc", "abd", 2, 1), ("kitten", "sitting", 5, 3),
    ("abc", "abcdefgh", 2, 3),            # length gap beyond cap -> cap+1
    ("abcdef", "uvwxyz", 2, 3),           # early exit when row minimum exceeds cap
    ("", "ab", 5, 2),
])
def test_lev(a, b, cap, want):
    assert fl.lev(a, b, cap) == want


def test_hints_ranks_close_terms_and_excludes_exact():
    terms = {"metformin": "Metformin", "metoprolol": "Metoprolol", "insulin": "Insulin"}
    got = fl.hints("metformen", terms, 3)
    assert [g[2] for g in got] == ["Metformin"]
    assert all(g[0] > 0 for g in fl.hints("metformin", terms, 3))   # exact match is not a suspect


def test_load_glossary_missing_dir_does_not_crash(tmp_path):
    terms, stats = fl.load_glossary(str(tmp_path))
    assert isinstance(terms, dict) and isinstance(stats, list)


# ---- finalize_to_vault ------------------------------------------------------------------------------------
@pytest.mark.parametrize("slug,want", [
    ("20250914_L1_胡婉妍_流感疫苗", ("20250914", "胡婉妍", "流感疫苗")),
    ("20250914_胡婉妍_流感_疫苗", ("20250914", "胡婉妍", "流感_疫苗")),
    ("胡婉妍_流感疫苗", ("", "胡婉妍", "流感疫苗")),
    ("胡婉妍", ("", "胡婉妍", "lecture")),
    ("20250914", ("20250914", "unknown", "lecture")),
    ("", ("", "", "lecture")),
])
def test_derive(slug, want):
    assert fv.derive(slug) == want


def test_ref_re_extracts_attachment_name_and_escapes_dir():
    rx = fv.ref_re("99-Attachment")
    m = rx.search("![[99-Attachment/lecture_20250914_胡_題/胡_s01.jpg|700]]")
    assert m and m.group(1) == "胡_s01.jpg"
    assert fv.ref_re("a.b").search("![[aXb/lecture_x/y.jpg]]") is None


def test_needs_refresh(tmp_path):
    src, dst = tmp_path / "s.jpg", tmp_path / "d.jpg"
    src.write_bytes(b"1234")
    assert fv.needs_refresh(src, dst) is True               # dst absent
    dst.write_bytes(b"1234")
    os.utime(dst, (int(src.stat().st_mtime), int(src.stat().st_mtime)))
    assert fv.needs_refresh(src, dst) is False
    dst.write_bytes(b"12345")
    os.utime(dst, (int(src.stat().st_mtime), int(src.stat().st_mtime)))
    assert fv.needs_refresh(src, dst) is True               # size differs
    dst.write_bytes(b"1234")
    old = int(src.stat().st_mtime) - 100
    os.utime(dst, (old, old))
    assert fv.needs_refresh(src, dst) is True               # mtime differs


# ---- route_inputs.classify ----------------------------------------------------------------------------------
def test_classify_buckets_case_insensitive_and_bracket_dir(tmp_path):
    d = tmp_path / "2026-07 [workshop] 演講"          # glob would treat [workshop] as a class
    d.mkdir()
    for name in ("a.MP4", "b.m4a", "c.PDF", "d.jpg", "e.pptx", "f.txt", "g.mts", "noext"):
        (d / name).write_bytes(b"")
    (d / "sub").mkdir()
    b = ri.classify(str(d))
    base = lambda k: sorted(os.path.basename(p) for p in b[k])
    assert base("video") == ["a.MP4", "g.mts"]
    assert base("audio") == ["b.m4a"]
    assert base("pdf") == ["c.PDF"]
    assert base("image") == ["d.jpg"]
    assert base("convert_first") == ["e.pptx"]
    assert base("other") == ["f.txt", "noext"]


def test_classify_recursive_includes_subfolders(tmp_path):
    (tmp_path / "x y").mkdir()
    (tmp_path / "x y" / "v.mov").write_bytes(b"")
    assert ri.classify(str(tmp_path))["video"] == []
    assert len(ri.classify(str(tmp_path), recursive=True)["video"]) == 1


def test_q_quotes_only_when_needed():
    assert ri._q("a/b.mp4") == "a/b.mp4"
    assert ri._q("a b.mp4") == '"a b.mp4"'
    assert ri._q("a(1).mp4") == '"a(1).mp4"'


# ---- dedup_semantic ----------------------------------------------------------------------------------------------
def test_clean_quick_text_drops_ui_lines():
    tok = next(iter(dd._ZOOM_UI_TOKENS))
    assert dd.clean_quick_text(f"內容一\n{tok}列\n內容二") == "內容一\n內容二"
    assert dd.clean_quick_text("") == ""
    assert dd.clean_quick_text(None) is None


def test_text_subset_ratio_without_fuzz_lib_and_empty():
    assert dd.text_subset_ratio("a", "b", None) == 0.0

    class F:
        @staticmethod
        def partial_ratio(a, b):
            return 100 if a in b else 40
    assert dd.text_subset_ratio("ab", "xaby", F) == 1.0
    assert dd.text_subset_ratio("", "x", F) == 0.0
    assert dd.text_subset_ratio("xaby", "ab", F) == 1.0           # symmetric
