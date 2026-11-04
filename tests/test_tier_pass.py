"""scripts/tier_pass.py: title cleaning, embed widths, deterministic scoring.
Scoring config comes from config.example.yaml (checked in, no private data)."""
from pathlib import Path

import pytest
import yaml

import tier_pass as tp

CFG = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "config.example.yaml").read_text(encoding="utf-8"))["scoring"]
TOKENS = ["聊天", "舉手", "麥克風"]


@pytest.fixture(autouse=True)
def _force1(monkeypatch):
    monkeypatch.setattr(tp, "FORCE1_FN", set(CFG["hard_overrides"]["force_tier_1_if_function_includes"]))


# ---- good_line -----------------------------------------------------------------------
@pytest.mark.parametrize("line,ok", [
    ("流感疫苗有效性", True), ("Vaccine efficacy", True),
    ("", False), (None, False), ("   ", False), ("a", False),
    ("12.5%", False), ("(3)", False), ("Zoom", False), ("  Zoom  ", False),
    ("正在發言: 小明", False), ("登入", False), ("螢幕共享中", False),
    ("按下舉手", False), ("開啟麥克風", False),
])
def test_good_line(line, ok):
    assert tp.good_line(line, TOKENS) is ok


# ---- clean_title ----------------------------------------------------------------------
def test_clean_title_prefers_guess_then_lines_then_labels():
    s = {"ocr": {"quick_title_guess": "Zoom", "quick_text": "123\n  疫苗   保護力  \n其他"},
         "vlm_signals": {"visible_labels": ["Label"]}}
    assert tp.clean_title(s, TOKENS) == "疫苗 保護力"
    s2 = {"ocr": {"quick_title_guess": "", "quick_text": "1\n2"}, "vlm_signals": {"visible_labels": ["Label"]}}
    assert tp.clean_title(s2, TOKENS) == "Label"


def test_clean_title_handles_missing_keys_and_none():
    assert tp.clean_title({}, TOKENS) == ""
    assert tp.clean_title({"ocr": None, "vlm_signals": None}, TOKENS) == ""
    assert tp.clean_title({"ocr": {"quick_text": None}}, TOKENS) == ""


# ---- ui_tokens --------------------------------------------------------------------------
def test_ui_tokens_from_config_and_fallback():
    assert tp.ui_tokens({"dedup": {"ui_chrome_tokens": ["a", 2]}}) == ["a", "2"]
    assert len(tp.ui_tokens({})) > 0
    assert len(tp.ui_tokens({"dedup": {"ui_chrome_tokens": []}})) > 0


# ---- embed_width ------------------------------------------------------------------------
@pytest.mark.parametrize("v,want", [
    ({"content_type": ["ultrasound"]}, 800),
    ({"contains_clinical_imaging": True}, 800),
    ({"content_type": ["anatomy"]}, 700),
    ({"content_type": ["flowchart"]}, 700),
    ({"contains_algorithm": True}, 700),
    ({"content_type": ["chart"]}, 700),
    ({"content_type": ["table", "title"]}, 600),
    ({"content_type": ["table", "text"]}, 700),
    ({}, 700),
    ({"content_type": None, "apparent_educational_function": None}, 700),
])
def test_embed_width_defaults(v, want):
    assert tp.embed_width(v) == want


def test_embed_width_config_override_and_force1_function():
    assert tp.embed_width({"content_type": ["table"]}, {"table": 500}) == 500
    assert tp.embed_width({"apparent_educational_function": ["treatment_protocol"]}) == 700
    assert tp.embed_width({"apparent_educational_function": ["treatment_protocol"]}, {"diagram": 650}) == 650


# ---- score_slide ----------------------------------------------------------------------------
def slide(vlm=None, t=None, **kw):
    s = {"vlm_signals": vlm, "transcript_signals": t or {}}
    s.update(kw)
    return s


def test_score_vlm_failed_is_tier3_conservative():
    assert tp.score_slide(slide(None), CFG) == (0.0, 3, "vlm_failed_conservative")
    assert tp.score_slide(slide({"content_type": ["text"]}, vlm_error="x"), CFG)[1] == 3


def test_score_weighted_sum_and_tier_thresholds():
    v = {"visual_complexity": 1, "non_textual_information_density": 1, "content_type": ["text"]}
    t = {"speaker_reference_density": 1, "speaker_emphasis_score": 1, "time_spent_seconds": 240}
    score, tier, reason = tp.score_slide(slide(v, t), CFG)
    assert score == pytest.approx(1.0) and tier == 1 and reason is None
    mid = tp.score_slide(slide({"visual_complexity": 1, "non_textual_information_density": 1,
                                "content_type": ["text"]}), CFG)
    assert mid[0] == pytest.approx(0.40) and mid[1] == 2          # boundary: >= tier_2
    low = tp.score_slide(slide({"visual_complexity": 1, "content_type": ["text"]}), CFG)
    assert low[0] == pytest.approx(0.15) and low[1] == 3


def test_score_time_spent_is_capped():
    a = tp.score_slide(slide({"content_type": ["text"]}, {"time_spent_seconds": 120}), CFG)[0]
    b = tp.score_slide(slide({"content_type": ["text"]}, {"time_spent_seconds": 9999}), CFG)[0]
    assert a == b == pytest.approx(0.15)


def test_score_clipped_to_unit_interval():
    neg = tp.score_slide(slide({"content_type": ["decorative", "text"]}), CFG)
    assert neg[0] == 0.0
    v = {"visual_complexity": 1, "non_textual_information_density": 1,
         "content_type": ["flowchart", "kaplan_meier", "ultrasound"]}
    assert tp.score_slide(slide(v, {"speaker_reference_density": 1, "speaker_emphasis_score": 1,
                                    "time_spent_seconds": 999}), CFG)[0] == 1.0


def test_score_penalties_apply():
    v = {"visual_complexity": 1, "non_textual_information_density": 1, "content_type": ["text"]}
    base = tp.score_slide(slide(v, {"speaker_reference_density": 1}), CFG)[0]
    skip = tp.score_slide(slide(v, {"speaker_reference_density": 1, "speaker_skip_score": 1}), CFG)[0]
    assert skip == pytest.approx(base - 0.30)
    red = dict(v, text_redundancy=0.71)
    assert tp.score_slide(slide(red, {"speaker_reference_density": 1}), CFG)[0] == pytest.approx(base - 0.20)
    at_threshold = dict(v, text_redundancy=0.7)          # strictly greater-than
    assert tp.score_slide(slide(at_threshold, {"speaker_reference_density": 1}), CFG)[0] == pytest.approx(base)


def test_override_force_tier1_by_function():
    v = {"content_type": ["text"], "apparent_educational_function": ["staging_schema"]}
    score, tier, reason = tp.score_slide(slide(v), CFG)
    assert tier == 1 and "staging_schema" in reason and score == 0.0


@pytest.mark.parametrize("ct", [["title"], ["decorative"], ["title", "decorative"]])
def test_override_title_decorative_only_is_tier3(ct):
    v = {"visual_complexity": 1, "non_textual_information_density": 1, "content_type": ct}
    t = {"speaker_reference_density": 1, "speaker_emphasis_score": 1, "time_spent_seconds": 200}
    _, tier, reason = tp.score_slide(slide(v, t), CFG)
    assert tier == 3 and reason == "content_type only title/decorative"


def test_override_text_redundant_is_tier3_but_not_with_chart_or_imaging():
    t = {"speaker_reference_density": 1, "speaker_emphasis_score": 1, "time_spent_seconds": 200}
    base = {"visual_complexity": 1, "non_textual_information_density": 1, "text_redundancy": 0.8}
    _, tier, reason = tp.score_slide(slide(dict(base, content_type=["text"]), t), CFG)
    assert tier == 3 and reason == "text-redundant, no visual signal"
    assert tp.score_slide(slide(dict(base, content_type=["text", "chart"]), t), CFG)[1] in (1, 2)
    assert tp.score_slide(slide(dict(base, content_type=["text"], contains_clinical_imaging=True), t), CFG)[2] is None


def test_imaging_floor_promotes_canonical_tier3_to_tier2_only():
    v = {"content_type": ["ultrasound"], "contains_clinical_imaging": True}
    s = slide(v, dedup={"is_canonical": True})
    _, tier, reason = tp.score_slide(s, CFG)
    assert tier == 2 and "imaging floor" in reason
    _, tier2, _ = tp.score_slide(slide(v, dedup={"is_canonical": False}), CFG)
    assert tier2 == 3
    _, tier3, _ = tp.score_slide(slide(v), CFG)
    assert tier3 == 3


def test_imaging_floor_does_not_override_forced_tier3():
    v = {"content_type": ["title"], "contains_clinical_imaging": True}
    _, tier, _ = tp.score_slide(slide(v, dedup={"is_canonical": True}), CFG)
    assert tier == 3


def test_score_handles_none_signal_values():
    v = {"visual_complexity": None, "non_textual_information_density": None, "content_type": None,
         "apparent_educational_function": None, "text_redundancy": None}
    t = {"speaker_reference_density": None, "time_spent_seconds": None}
    assert tp.score_slide(slide(v, t), CFG)[:2] == (0.0, 3)
