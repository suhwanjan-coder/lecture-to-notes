"""scripts/asr_audit.py text/number helpers. Numbers matter most for the notes."""
import pytest

import asr_audit as a


# ---- cn_to_num -------------------------------------------------------------------
@pytest.mark.parametrize("s,want", [
    ("十", "10"), ("二十", "20"), ("十五", "15"), ("一百零五", "105"), ("兩百", "200"),
    ("一千二百三十四", "1234"), ("一萬二千三百", "12300"), ("二零二四", "2024"),
    ("三點五", "3.5"), ("十點五", "10.5"), ("二億", "200000000"),
])
def test_cn_to_num(s, want):
    assert a.cn_to_num(s) == want


@pytest.mark.parametrize("s", ["點五", "三點", "三點五十", "abc"])
def test_cn_to_num_rejects_non_numerals(s):
    assert a.cn_to_num(s) is None


# ---- extract_numbers ------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [
    ("有80%的人", ["80%"]),
    ("百分之八十", ["80%"]),
    ("百分之12.5", ["12.5%"]),
    ("12.50", ["12.5"]),
    ("共1,234,567例", ["1234567"]),
    ("1957,1968", ["1957", "1968"]),          # a list of years, not one number
    ("7、8、9月", ["789"]),                   # single-digit list collapses
    ("３萬", ["30000"]),                      # full-width digit, NFKC
    ("1.5億", ["150000000"]),
    ("二零二四年", ["2024"]),
    ("十五歲", ["15"]),
    ("三點五公分", ["3.5"]),
    ("第一個", []),                            # ordinal
    ("萬一出事", []),                          # idiom, not 10000
    ("一個人", []),                            # 一 + 個 is not counted
    ("", []),
])
def test_extract_numbers(text, want):
    assert a.extract_numbers(text) == want


def test_extract_numbers_mixed_arabic_cjk_units():
    assert a.extract_numbers("2千3百萬") == ["23000000"]
    assert a.extract_numbers("230萬") == ["2300000"]


def test_extract_numbers_arabic_and_chinese_forms_agree():
    """The audit compares sets from both ASR engines; both spellings must match."""
    assert a.extract_numbers("百分之八十") == a.extract_numbers("80%")
    assert a.extract_numbers("三萬") == a.extract_numbers("30000") == ["30000"]


@pytest.mark.parametrize("text,want", [
    ("一百二", ["120"]), ("三萬五", ["35000"]), ("兩千三", ["2300"]),
    ("一百零二", ["102"]),            # 零 keeps it literal
    ("一百二十", ["120"]), ("三萬零五", ["30005"]),
])
def test_extract_numbers_colloquial_truncated_numerals(text, want):
    assert a.extract_numbers(text) == want


# ---- squash / jaccard / bigrams -------------------------------------------------------
def test_squash_strips_punct_width_case():
    assert a.squash("Ａ，B。 c！") == "abc"
    assert a.squash("你好， 世界！") == "你好世界"


def test_bigrams():
    assert a.bigrams("abc") == {"ab", "bc"}
    assert a.bigrams("a") == set()


def test_jaccard_identical_disjoint_and_too_short():
    t = "今天討論流感疫苗的有效性與安全性以及接種後的長期追蹤結果"
    assert a.jaccard(t, t) == 1.0
    assert a.jaccard(t, "完全不同的另外一段文字內容說明用來測試相似度計算結果") < 0.2
    assert a.jaccard("短", t) is None


def test_jaccard_ignores_punctuation_difference():
    assert a.jaccard("今天討論流感疫苗，的有效性與安全性以及接種後。", "今天討論流感疫苗的有效性與安全性以及接種後") == 1.0


# ---- loops --------------------------------------------------------------------------
def test_loop_info_normal_text_is_not_loop():
    lines = ["今天要討論的是流感疫苗", "第一個問題是效力", "第二個問題是安全性",
             "我們來看這張圖表", "結論是建議每年接種"] * 1
    info = a.loop_info(lines)
    assert info["loop"] is False


def test_loop_info_repeated_lines():
    info = a.loop_info(["淋巴結是淋巴結"] * 5)
    assert info["loop"] is True
    assert info["max_line_repeats"] == 5


def test_inline_repeat_detected_and_digit_lists_ignored():
    unit, n = a.inline_repeat(["淋巴結是淋巴結淋巴結是淋巴結淋巴結是淋巴結淋巴結是淋巴結"])
    assert n >= 4 and unit
    assert a.inline_repeat(["20% 20% 20% 20% 20%"]) == ("", 0)


# ---- assign_windows -----------------------------------------------------------------
def test_assign_windows_by_midpoint_and_clamping():
    segs = [{"start": 0, "end": 10, "text": "a"},
            {"start": 55, "end": 70, "text": "b"},       # mid 62.5 -> window 1
            {"start": 500, "end": 510, "text": "late"},   # clamped to last
            {"start": 5, "end": 6, "text": "  "},         # blank skipped
            {"start": -30, "end": -20, "text": "neg"}]    # clamped to 0
    w = a.assign_windows(segs, 60.0, 3)
    assert w == [["a", "neg"], ["b"], ["late"]]


# ---- percentile -----------------------------------------------------------------------
def test_percentile_basic():
    v = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert a.percentile(v, 0) == 1.0
    assert a.percentile(v, 1) == 5.0
    assert a.percentile(v, 0.5) == 3.0
