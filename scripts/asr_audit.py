"""ASR audit: compare the PRIMARY (local CPU Whisper) transcript against Groq.

Groq is an EXTERNAL AUDITOR only. This script never rewrites, replaces or
"fixes" the primary transcript -- it only writes asr_audit.json / asr_audit.md
telling the note writer which time windows of the primary are untrustworthy.
(Same flag-only philosophy as flag_asr_suspects.py; see its docstring for why.)

Usage
    python asr_audit.py <lecture_dir> [--media FILE] [--groq-json FILE]
                        [--window 60] [--lang zh]
    python asr_audit.py --selftest

Inputs
    <lecture_dir>/transcript.json     primary transcript, [{start,end,text}]
    Groq transcript, first match wins:
        --groq-json FILE
        <lecture_dir>/asr_groq/transcript.json   (cached from an earlier run)
        fresh Groq call on --media (or the single video in/next to lecture_dir),
        saved to <lecture_dir>/asr_groq/transcript.json (+ .txt)

Outputs (in lecture_dir, UTF-8)
    asr_audit.json   per-window metrics + flags
    asr_audit.md     summary, then every flagged window with both texts

Flags (per fixed window)
    GAP_PRIMARY     primary < 20% of Groq's chars while Groq >= 40 chars
    LOOP_PRIMARY    degenerate repetition in the primary window
    LOOP_GROQ       degenerate repetition in the Groq window -> Groq's fault,
                    the other comparative flags are SUPPRESSED for that window
    NUMBER_MISMATCH multiset of numbers differs (normalised to digits)
    LOW_SIM         char-bigram Jaccard below LOW_SIM_THRESHOLD

Exit codes: 0 ok / 1 Groq call failed or selftest failed (existing asr_audit.*
untouched) / 2 bad input.

Why OpenCC 's2tw' (not 's2t' / 's2twp') -- measured 2026-10-07:
    s2t   is character-only and leaves mainland variant glyphs: 臨床試驗 -> 臨牀試驗,
          裡面 -> 裏面. Those never match Whisper's Taiwan output, so similarity
          would drop for a purely orthographic reason.
    s2twp also rewrites vocabulary: 研究對象 -> 研究物件, 資料/程式/軟體/網路 etc.
          Phrase rewriting can corrupt medical terms and does not help a
          comparison metric; it also moves text away from what Groq said.
    s2tw  converts characters to Taiwan standard forms (臨床, 裡面) and does NOT
          touch vocabulary, so 疫苗/佐劑/抗體滴度/不良反應 etc. pass through intact.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from _common import atomic_write_json, fmt_hms, load_segments  # noqa: E402

try:  # machine-wide TLS interception: use the OS trust store, never verify=False
    import truststore  # type: ignore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ----------------------------------------------------------------------
# Thresholds
# ----------------------------------------------------------------------
GAP_RATIO = 0.20            # primary chars / groq chars below this ...
GAP_MIN_GROQ_CHARS = 40     # ... while groq has at least this many chars
LOOP_MIN_LINE_CHARS = 4     # ignore "對 對 對" style short fillers
LOOP_LINE_REPEATS = 3
LOOP_NGRAM = 8
LOOP_DIVERSITY = 0.5
LOOP_MIN_GRAMS = 30         # need enough 8-grams for diversity to mean anything
SIM_MIN_CHARS = 20          # both sides need this much text for a similarity
# Calibrated 2026-10-07 on 胡婉妍 (31 windows) + 黃立民 (41 windows), CPU medium
# vs Groq, 60 s windows, LOOP_GROQ windows excluded -> pooled n=71:
#   median 0.627, MAD 0.052 (robust 3-sigma floor = 0.397); lowest "ordinary"
#   windows 0.459 / 0.471 / 0.502; the only two genuinely divergent windows
#   (黃立民 05:00-07:00, Groq dropped a stretch of speech) sit at 0.137 / 0.222
#   (a 3rd fresh Groq run gave 0.132 / 0.384 -- Groq is not deterministic).
#   0.40 lies between the two groups and above the robust floor -> 2 flags / 72.
# The distribution of every run is printed in its asr_audit.md header; re-check
# it when the primary model or the window size changes (--window shifts Jaccard).
LOW_SIM_THRESHOLD = 0.40

PROMPT_ZH = "以下是台灣醫學演講的繁體中文逐字稿。"
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav")
BANNER = "Groq 為外部稽核，主稿不變；以下只是疑點"

# ----------------------------------------------------------------------
# Text normalisation
# ----------------------------------------------------------------------
_OPENCC = None
_OPENCC_NAME = "none"


def init_opencc() -> None:
    """Load OpenCC s2tw. Degrade LOUDLY (stderr + recorded in the report)."""
    global _OPENCC, _OPENCC_NAME
    try:
        from opencc import OpenCC  # type: ignore
        _OPENCC = OpenCC("s2tw")
        _OPENCC_NAME = "s2tw"
    except Exception as e:  # ImportError or config-load failure
        _OPENCC = None
        _OPENCC_NAME = "none"
        print("=" * 70, file=sys.stderr)
        print(f"WARNING: OpenCC unavailable ({type(e).__name__}: {e}).", file=sys.stderr)
        print("  Groq text will NOT be converted to Traditional Chinese; similarity,",
              file=sys.stderr)
        print("  LOW_SIM and NUMBER_MISMATCH will be inflated by script differences.",
              file=sys.stderr)
        print("  Fix: pip install opencc-python-reimplemented", file=sys.stderr)
        print("=" * 70, file=sys.stderr)


def to_trad(text: str) -> str:
    return _OPENCC.convert(text) if _OPENCC else text


_PUNCT_WS = re.compile(r"[\s　，。、；：！？「」『』（）()\[\]【】《》〈〉…—–\-~～·．,.;:!?\"'“”‘’/\\|]+")


def squash(text: str) -> str:
    """Comparison form: NFKC, lowercase, no whitespace/punctuation."""
    t = unicodedata.normalize("NFKC", text).lower()
    return _PUNCT_WS.sub("", t)


def bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float | None:
    sa, sb = squash(a), squash(b)
    if len(sa) < SIM_MIN_CHARS or len(sb) < SIM_MIN_CHARS:
        return None
    ba, bb = bigrams(sa), bigrams(sb)
    if not ba or not bb:
        return None
    return len(ba & bb) / len(ba | bb)


# ----------------------------------------------------------------------
# Loop detection
# ----------------------------------------------------------------------
_INLINE_REPEAT = re.compile(r"(.{3,14}?)\1{3,}")   # same unit >=4x back-to-back


def inline_repeat(lines: list[str]) -> tuple[str, int]:
    """Repetition INSIDE a segment ('淋巴結是淋巴結,淋巴結是淋巴結,...'): Groq's
    typical collapse. Whole-line and window-diversity checks miss it (measured:
    3 of 3 fresh Groq runs on 黃立民 had one at 21:24). Digit/percent lists such
    as '20% 20% 20%' are legitimate and ignored."""
    best = ("", 0)
    for l in lines:
        sq = squash(l)
        for m in _INLINE_REPEAT.finditer(sq):
            unit = m.group(1)
            if re.fullmatch(r"[\d%.]+", unit):
                continue
            n = len(m.group(0)) // len(unit)
            if n > best[1]:
                best = (unit, n)
    return best


def loop_info(lines: list[str]) -> dict:
    """Degenerate repetition: same line >=3x, same unit >=4x inside a line, or
    char-8-gram diversity < 0.5."""
    norm = [squash(l) for l in lines]
    norm = [n for n in norm if len(n) >= LOOP_MIN_LINE_CHARS]
    cnt = Counter(norm)
    top_line, top_n = (cnt.most_common(1)[0] if cnt else ("", 0))
    joined = "".join(squash(l) for l in lines)
    grams = [joined[i:i + LOOP_NGRAM] for i in range(len(joined) - LOOP_NGRAM + 1)]
    diversity = (len(set(grams)) / len(grams)) if len(grams) >= LOOP_MIN_GRAMS else None
    in_unit, in_n = inline_repeat(lines)
    is_loop = ((top_n >= LOOP_LINE_REPEATS) or in_n >= 4
               or (diversity is not None and diversity < LOOP_DIVERSITY))
    line_loop = top_n >= LOOP_LINE_REPEATS
    return {
        "loop": bool(is_loop),
        "max_line_repeats": top_n,
        "inline_repeat_count": in_n,
        "repeated_line": (top_line[:40] if line_loop else in_unit[:40] if in_n >= 4 else ""),
        "ngram_diversity": None if diversity is None else round(diversity, 3),
    }


# ----------------------------------------------------------------------
# Number extraction (normalised to digits)
# ----------------------------------------------------------------------
_CN_DIG = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_CHARS = "零〇一二兩三四五六七八九十百千萬億點"
_CN_SEQ = re.compile(f"[{_CN_CHARS}]+")
_SINGLE_OK_UNIT = "%倍歲年週天人例次劑成種個月日"
_ARABIC = re.compile(r"\d+(?:\.\d+)?")


def _fmt_num(x: float) -> str:
    if x == int(x):
        return str(int(x))
    return ("%.6f" % x).rstrip("0").rstrip(".")


def cn_to_num(s: str) -> str | None:
    """Chinese numeral string -> digit string, or None if not a clean numeral."""
    if "點" in s:
        head, _, tail = s.partition("點")
        if not head or not tail or any(c not in _CN_DIG for c in tail):
            return None
        h = cn_to_num(head)
        if h is None:
            return None
        return h + "." + "".join(str(_CN_DIG[c]) for c in tail)
    if all(c in _CN_DIG for c in s):  # positional: 二零二四 -> 2024
        return "".join(str(_CN_DIG[c]) for c in s)
    total, section, num = 0, 0, None
    prev_ascii = False
    for ch in s:
        if ch.isascii() and ch.isdigit():     # mixed form: 2千3百萬, 230萬
            num = (num * 10 + int(ch)) if (prev_ascii and num is not None) else int(ch)
            prev_ascii = True
            continue
        prev_ascii = False
        if ch in _CN_DIG:
            num = _CN_DIG[ch]
        elif ch in _CN_UNIT:
            section += (1 if num is None else num) * _CN_UNIT[ch]
            num = None
        elif ch == "萬":
            total += (section + (num or 0)) * 10_000
            section, num = 0, None
        elif ch == "億":
            total = (total + section + (num or 0)) * 100_000_000
            section, num = 0, None
        else:
            return None
    total += section + (num or 0)
    return str(total)


def extract_numbers(text: str) -> list[str]:
    """Numbers as normalised strings: 80%, 12.5, 30000 (3萬), 80% (百分之八十)."""
    t = unicodedata.normalize("NFKC", text)
    # thousands separators only (12,345,678); "1957,1968" is a LIST, not 19571968
    t = re.sub(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d])", lambda m: m.group(0).replace(",", ""), t)
    # "7、8、9" and "789" are the same month list: collapse single-digit lists
    t = re.sub(r"(?<![\d.])\d(?:\s?[、,，]\s?\d)+(?![\d.])",
               lambda m: re.sub(r"\D", "", m.group(0)), t)
    out: list[str] = []

    def conv_mixed(m: re.Match) -> str:
        run = m.group(0)
        if re.search(r"\d", run) and re.search(r"[十百千]", run):
            n = cn_to_num(run)
            if n is not None:
                return f" {n} "
        return run

    t = re.sub(r"[\d零〇一二兩三四五六七八九十百千萬億]+", conv_mixed, t)

    def conv_cn(m: re.Match) -> str:
        seq = m.group(0)
        start, end = m.span()
        prev = t[start - 1] if start > 0 else ""
        nxt = t[end] if end < len(t) else ""
        if prev == "第":                       # ordinals: 第一, 第二
            return seq
        if seq[0] in "百千萬億點":              # 萬一 / 千萬不要 ...
            return seq
        if len(seq) == 1:
            if seq in "二兩三四五六七八九十" and nxt and nxt in _SINGLE_OK_UNIT:
                n = cn_to_num(seq)
                return f" {n} " if n else seq
            return seq
        if "點" in seq and not (seq[0] in _CN_DIG or seq[0] == "十"):
            return seq
        n = cn_to_num(seq)
        return f" {n} " if n is not None else seq

    # 百分之X -> X%
    def conv_pct(m: re.Match) -> str:
        v = m.group(1)
        n = v if v[0].isdigit() else cn_to_num(v)
        return f" {n}% " if n is not None else m.group(0)

    t = re.sub(rf"百分之([{_CN_CHARS}]+|\d+(?:\.\d+)?)", conv_pct, t)
    t = _CN_SEQ.sub(conv_cn, t)
    # 3萬 / 1.5億 after digits
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(萬|億)",
               lambda m: " " + _fmt_num(float(m.group(1)) * (10_000 if m.group(2) == "萬" else 100_000_000)) + " ", t)
    for m in re.finditer(r"\d+(?:\.\d+)?\s?%?", t):
        tok = m.group(0).replace(" ", "")
        if "." in tok:  # 12.50 -> 12.5
            core = tok.rstrip("%")
            tok = _fmt_num(float(core)) + ("%" if tok.endswith("%") else "")
        out.append(tok)
    return out


# ----------------------------------------------------------------------
# Windowing + flagging
# ----------------------------------------------------------------------
def assign_windows(segs: list[dict], window: float, n_win: int) -> list[list[str]]:
    """Segment -> window by midpoint. Returns per-window list of segment texts."""
    wins: list[list[str]] = [[] for _ in range(n_win)]
    for s in segs:
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        a = float(s.get("start", 0.0))
        b = float(s.get("end", a))
        mid = (a + b) / 2.0
        idx = min(max(int(mid // window), 0), n_win - 1)
        wins[idx].append(txt)
    return wins


def analyze(primary: list[dict], groq: list[dict], window: float = 60.0,
            sim_threshold: float = LOW_SIM_THRESHOLD) -> list[dict]:
    """Core comparison. Groq text is converted to Traditional here."""
    ends = [float(s.get("end", s.get("start", 0.0))) for s in primary + groq]
    n_win = max(1, int(math.ceil((max(ends) if ends else 0.0) / window)))
    p_lines = assign_windows(primary, window, n_win)
    g_lines_raw = assign_windows(groq, window, n_win)
    g_lines = [[to_trad(l) for l in ls] for ls in g_lines_raw]

    p_nums = [Counter(extract_numbers("".join(ls))) for ls in p_lines]
    g_nums = [Counter(extract_numbers("".join(ls))) for ls in g_lines]

    results: list[dict] = []
    for i in range(n_win):
        p_text, g_text = "".join(p_lines[i]), "".join(g_lines[i])
        p_len, g_len = len(squash(p_text)), len(squash(g_text))
        sim = jaccard(p_text, g_text)
        lp, lg = loop_info(p_lines[i]), loop_info(g_lines[i])

        only_p = p_nums[i] - g_nums[i]
        only_g = g_nums[i] - p_nums[i]
        # Edge noise: a number that merely sits on a neighbouring window's side of
        # the segment boundary on the other side is not a content disagreement.
        excused: list[str] = []
        for key in list(only_p):
            nb = Counter()
            for j in (i - 1, i + 1):
                if 0 <= j < n_win:
                    nb += g_nums[j]
            if nb[key] > 0:
                excused.append(key)
                del only_p[key]
        for key in list(only_g):
            nb = Counter()
            for j in (i - 1, i + 1):
                if 0 <= j < n_win:
                    nb += p_nums[j]
            if nb[key] > 0:
                excused.append(key)
                del only_g[key]

        raw_flags: list[str] = []
        if lp["loop"]:
            raw_flags.append("LOOP_PRIMARY")
        if lg["loop"]:
            raw_flags.append("LOOP_GROQ")
        gap = (g_len >= GAP_MIN_GROQ_CHARS and p_len < GAP_RATIO * g_len)
        if gap:
            raw_flags.append("GAP_PRIMARY")
        # A lone extra single-digit on ONE side ("3個", "2") is list/formatting noise
        # (measured: 25 of 41 windows flagged without this rule); keep it in the JSON
        # but do not flag. Substitutions (each side has numbers the other lacks) and
        # any multi-digit / % / decimal difference are always flagged.
        def _sig(tok: str) -> bool:
            return len(re.sub(r"\D", "", tok)) >= 2 or "%" in tok or "." in tok
        num_significant = bool(only_p and only_g) or any(_sig(t) for t in list(only_p) + list(only_g))
        if p_len and g_len and (only_p or only_g) and num_significant:
            raw_flags.append("NUMBER_MISMATCH")
        if sim is not None and sim < sim_threshold and not gap:
            raw_flags.append("LOW_SIM")

        suppressed: list[str] = []
        flags = raw_flags
        if lg["loop"]:  # Groq's fault: comparative flags are not evidence about primary
            suppressed = [f for f in raw_flags if f in ("GAP_PRIMARY", "NUMBER_MISMATCH", "LOW_SIM")]
            flags = [f for f in raw_flags if f not in suppressed]

        results.append({
            "idx": i,
            "start": round(i * window, 2),
            "end": round((i + 1) * window, 2),
            "primary_chars": p_len,
            "groq_chars": g_len,
            "jaccard": None if sim is None else round(sim, 4),
            "loop_primary": lp,
            "loop_groq": lg,
            "number_mismatch_kind": ("substitution" if (only_p and only_g)
                                     else "primary_only" if only_p
                                     else "groq_only" if only_g else None),
            "number_diff_minor_only": bool((only_p or only_g) and not num_significant),
            "numbers_only_primary": sorted(only_p.elements()),
            "numbers_only_groq": sorted(only_g.elements()),
            "numbers_excused_by_neighbour_window": sorted(excused),
            "flags": flags,
            "suppressed_by_groq_loop": suppressed,
            "primary_text": p_text,
            "groq_text_trad": g_text,
        })
    return results


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def sim_distribution(wins: list[dict]) -> dict:
    vals = sorted(w["jaccard"] for w in wins
                  if w["jaccard"] is not None and not w["loop_groq"]["loop"])
    if not vals:
        return {}
    d = {"n": len(vals)}
    for q in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90):
        d[f"p{int(q * 100):02d}"] = round(percentile(vals, q), 3)
    d["min"], d["max"] = round(vals[0], 3), round(vals[-1], 3)
    return d


FLAG_ORDER = ["LOOP_GROQ", "GAP_PRIMARY", "LOOP_PRIMARY", "NUMBER_MISMATCH", "LOW_SIM"]
FLAG_NOTE = {
    "GAP_PRIMARY": "主稿漏段：Groq 有大量文字、主稿幾乎沒有",
    "LOOP_PRIMARY": "主稿重複崩壞",
    "LOOP_GROQ": "Groq 自己崩壞（Groq 的問題，非主稿的問題；本窗其他比對旗標已抑制）",
    "NUMBER_MISMATCH": "兩邊數字不同，需回查投影片或影片",
    "LOW_SIM": "兩邊文字相似度偏低",
}


def build_report(wins: list[dict], meta: dict) -> tuple[dict, str]:
    counts = {f: sum(1 for w in wins if f in w["flags"]) for f in FLAG_ORDER}
    primary_suspect = [w for w in wins if any(f in w["flags"] for f in
                       ("GAP_PRIMARY", "LOOP_PRIMARY", "NUMBER_MISMATCH", "LOW_SIM"))]
    groq_bad = [w for w in wins if "LOOP_GROQ" in w["flags"]]
    dist = sim_distribution(wins)
    summary = {"windows": len(wins), "flag_counts": counts,
               "primary_suspect_windows": len(primary_suspect),
               "groq_unreliable_windows": len(groq_bad),
               "jaccard_distribution": dist}
    doc = {"meta": meta, "summary": summary,
           "windows": [{k: v for k, v in w.items()} for w in wins]}

    L = [f"# ASR 稽核報告", "", f"> **{BANNER}**", "",
         f"- 主稿：`{meta['primary_path']}`（{meta['primary_segments']} 段）",
         f"- Groq：`{meta['groq_path']}`（{meta['groq_segments']} 段，{meta['groq_source']}"
         + (f"，Groq 耗時 {meta['groq_wall_seconds']} 秒" if meta.get("groq_wall_seconds") is not None else "") + "；"
         f"OpenCC {meta['opencc']} 轉繁體）",
         f"- 視窗：{meta['window_s']:g} 秒，共 {len(wins)} 窗；LOW_SIM 門檻 {meta['low_sim_threshold']}",
         f"- 產生時間：{meta['generated']}", "",
         "## 摘要", "", "| 旗標 | 視窗數 | 意義 |", "|---|---|---|"]
    for f in FLAG_ORDER:
        L.append(f"| {f} | {counts[f]} | {FLAG_NOTE[f]} |")
    L += ["", f"主稿有疑點的視窗：**{len(primary_suspect)} / {len(wins)}**；"
          f"Groq 不可靠的視窗：{len(groq_bad)}。", ""]
    if dist:
        L.append("Jaccard 分布（排除 LOOP_GROQ 視窗）："
                 + "，".join(f"{k}={v}" for k, v in dist.items()) + "。")
        L.append("")
    flagged = [w for w in wins if w["flags"]]
    L += ["## 有旗標的視窗（依時間排序）", ""]
    if not flagged:
        L.append("（無）")
    for w in flagged:
        stamp = f"[{fmt_hms(w['start'])}–{fmt_hms(w['end'])}]"
        L.append(f"### {stamp} {' '.join(w['flags'])}")
        L.append("")
        sim = "n/a" if w["jaccard"] is None else f"{w['jaccard']:.2f}"
        L.append(f"- Jaccard {sim}；字數 主稿 {w['primary_chars']} / Groq {w['groq_chars']}")
        for f in w["flags"]:
            L.append(f"- {f}：{FLAG_NOTE[f]}")
        if "LOOP_GROQ" in w["flags"]:
            lg = w["loop_groq"]
            L.append(f"  - Groq 重複 「{lg['repeated_line']}」（整行 x{lg['max_line_repeats']}／單行內連續 x{lg['inline_repeat_count']}）；"
                     f"8-gram 多樣性 {lg['ngram_diversity']}")
            if w["suppressed_by_groq_loop"]:
                L.append(f"  - 因 Groq 崩壞而抑制的旗標：{', '.join(w['suppressed_by_groq_loop'])}")
        if "LOOP_PRIMARY" in w["flags"]:
            lp = w["loop_primary"]
            L.append(f"  - 主稿重複 「{lp['repeated_line']}」（整行 x{lp['max_line_repeats']}／單行內連續 x{lp['inline_repeat_count']}）；"
                     f"8-gram 多樣性 {lp['ngram_diversity']}")
        if "NUMBER_MISMATCH" in w["flags"]:
            L.append(f"  - 只在主稿：{w['numbers_only_primary'] or '—'}　只在 Groq：{w['numbers_only_groq'] or '—'}"
                     f"（{ {'substitution': '兩邊各有對不上的數字，疑似數字被讀錯', 'primary_only': '只有主稿多出數字', 'groq_only': '只有 Groq 多出數字（可能主稿漏寫，或 Groq 幻覺）'}[w['number_mismatch_kind']] }）")
        L += ["", f"- 主稿：{w['primary_text'] or '（空）'}",
              f"- Groq（繁）：{w['groq_text_trad'] or '（空）'}", ""]
    return doc, "\n".join(L) + "\n"


# ----------------------------------------------------------------------
# Groq acquisition
# ----------------------------------------------------------------------
def _ensure_groq_key() -> None:
    """GROQ_API_KEY is a Windows *user* env var; a process started before it was
    set (or a sandboxed shell) may not carry it. Read the registry as fallback.
    The value is never printed."""
    if os.environ.get("GROQ_API_KEY", "").strip():
        return
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val, _ = winreg.QueryValueEx(k, "GROQ_API_KEY")
            if val:
                os.environ["GROQ_API_KEY"] = val
        except OSError:
            pass


def find_media(lecture_dir: str) -> str | None:
    found: list[str] = []
    for base in (lecture_dir, os.path.dirname(os.path.abspath(lecture_dir))):
        found = [p for p in (glob.glob(os.path.join(base, "*")))
                 if p.lower().endswith(VIDEO_EXT) and os.path.isfile(p)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            print(f"ERROR: {len(found)} media files in {base}; pass --media explicitly.",
                  file=sys.stderr)
            return None
    return None


def get_groq(lecture_dir: str, media: str | None, groq_json: str | None,
             lang: str) -> tuple[list[dict], str, str, float | None]:
    """Return (segments, path, source description, groq_wall_seconds|None)."""
    if groq_json:
        return load_segments(groq_json), os.path.abspath(groq_json), "given --groq-json", None
    cache = os.path.join(lecture_dir, "asr_groq", "transcript.json")
    if os.path.isfile(cache):
        wall0 = None
        try:
            with open(os.path.join(lecture_dir, "asr_groq", "groq_meta.json"),
                      encoding="utf-8") as fh:
                wall0 = json.load(fh).get("wall_seconds")
        except (OSError, ValueError):
            pass
        return load_segments(cache), cache, "cached asr_groq/transcript.json", wall0

    media = media or find_media(lecture_dir)
    if not media or not os.path.isfile(media):
        print("ERROR: no Groq transcript and no media to transcribe "
              "(use --media or --groq-json).", file=sys.stderr)
        sys.exit(1)
    _ensure_groq_key()
    import groq_asr  # local helper
    out_dir = os.path.join(lecture_dir, "asr_groq")
    tmp_dir = os.path.join(out_dir, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    t0 = time.time()
    try:
        segs = groq_asr.transcribe_groq(media, language=lang,
                                        prompt=PROMPT_ZH if lang in ("zh", "bilingual") else None,
                                        work_dir=tmp_dir)
    except Exception as e:
        print(f"ERROR: Groq transcription failed ({type(e).__name__}: {e}). "
              "Existing asr_audit.* left untouched.", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    wall = time.time() - t0
    if not segs or not any((s.get("text") or "").strip() for s in segs):
        print("ERROR: Groq returned no text. Existing asr_audit.* left untouched.",
              file=sys.stderr)
        sys.exit(1)
    cache = os.path.join(out_dir, "transcript.json")
    groq_asr.write_outputs(segs, cache, os.path.join(out_dir, "transcript.txt"),
                           timestamped_txt=True)
    with open(os.path.join(out_dir, "groq_meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"wall_seconds": round(wall, 1), "media": os.path.basename(media),
                   "model": groq_asr.GROQ_MODEL, "prompt": PROMPT_ZH, "lang": lang,
                   "segments": len(segs)}, fh, ensure_ascii=False, indent=2)
    print(f"[audit] Groq wall time {wall:.1f}s -> {cache}", file=sys.stderr)
    return segs, cache, f"fresh Groq call on {os.path.basename(media)} ({wall:.0f}s)", wall


# ----------------------------------------------------------------------
# Selftest
# ----------------------------------------------------------------------
def selftest() -> None:
    """Known-sample check of the comparison logic (no network, no files)."""
    def seg(a, b, t):
        return {"start": a, "end": b, "text": t}

    clean = "這個研究收錄了三百二十位住院病人，主要結果是死亡率在二十八天時下降。"
    primary = [
        # window 0 clean
        seg(2, 8, clean),
        seg(10, 16, "我們接著看第二個重點，疫苗的保護力隨著時間逐漸下降而且老年人特別明顯。"),
        # window 1: primary loop (same line 4x)
        seg(62, 66, "好我們繼續往下看"), seg(66, 70, "好我們繼續往下看"),
        seg(70, 74, "好我們繼續往下看"), seg(74, 78, "好我們繼續往下看"),
        # window 2: number mismatch (85% vs 58%)
        seg(122, 130, "這個藥物的有效率是百分之八十五，副作用大多是輕微的腸胃不適與頭痛。"),
        # window 3: primary gap (nearly empty)
        seg(182, 184, "嗯"),
        # window 4: clean
        seg(242, 250, "最後總結一下今天的內容，感謝各位的聆聽與討論，也歡迎大家提出問題。"),
        # window 5: Groq loop only
        seg(302, 312, "接下來是關於抗生素使用的建議，要依照當地的抗藥性資料來調整選擇。"),
        # window 6: Groq collapses INSIDE one segment; primary has a legit digit list
        seg(362, 372, "看這張圖各個年份的保護效果分別是 20% 20% 20% 然後第四年下降到很低的程度。"),
    ]
    groq = [
        seg(2, 8, "这个研究收录了320位住院病人，主要结果是死亡率在28天时下降。"),
        seg(10, 16, "我们接着看第二个重点，疫苗的保护力随着时间逐渐下降，而且老年人特别明显。"),
        seg(62, 70, "好，我们继续往下看，接下来是关于疫苗接种时程的重要说明和注意事项。"),
        seg(70, 78, "首先要确认病人没有急性发烧，其次要留意过去是否有严重的过敏反应纪录。"),
        seg(122, 130, "这个药物的有效率是百分之五十八，副作用大多是轻微的肠胃不适与头痛。"),
        seg(182, 192, "这一页讲的是主要的试验设计，包含纳入条件、排除条件以及追踪的时间长度，还有主要与次要的评估指标和统计分析的方法。"),
        seg(242, 250, "最后总结一下今天的内容，感谢各位的聆听与讨论，也欢迎大家提出问题。"),
        seg(302, 306, "我们来看下一页"), seg(306, 310, "我们来看下一页"),
        seg(310, 314, "我们来看下一页"), seg(314, 318, "我们来看下一页"),
        seg(362, 372, "看这张图各个年份的保护效果分别是20%、20%、20%，然后第四年下降到很低的程度，淋巴结是淋巴结，淋巴结是淋巴结，淋巴结是淋巴结，淋巴结是淋巴结，淋巴结是淋巴结。"),
    ]
    init_opencc()
    if _OPENCC is None:
        print("SELFTEST FAIL: OpenCC missing", file=sys.stderr)
        sys.exit(1)
    wins = analyze(primary, groq, window=60.0)
    got = {w["idx"]: set(w["flags"]) for w in wins}
    want = {0: set(), 1: {"LOOP_PRIMARY"}, 2: {"NUMBER_MISMATCH"},
            3: {"GAP_PRIMARY"}, 4: set(), 5: {"LOOP_GROQ"}, 6: {"LOOP_GROQ"}}
    # window 1: LOOP_PRIMARY must be present (LOW_SIM may legitimately co-occur)
    got[1] = got[1] - {"LOW_SIM"}
    bad = {i: (sorted(got.get(i, set())), sorted(want[i])) for i in want if got.get(i) != want[i]}
    # numbers: Chinese numerals must normalise to the Groq digits in window 0
    if extract_numbers("三百二十位") != ["320"] or extract_numbers("百分之八十五") != ["85%"] \
            or extract_numbers("3萬") != ["30000"] or extract_numbers("12.50%") != ["12.5%"]:
        bad["numbers"] = (extract_numbers("三百二十位"), extract_numbers("百分之八十五"))
    if bad:
        print(f"SELFTEST FAIL (got, want): {bad}", file=sys.stderr)
        sys.exit(1)
    print("SELFTEST OK: clean / LOOP_PRIMARY / NUMBER_MISMATCH / GAP_PRIMARY / LOOP_GROQ (whole-line and in-line) all as expected")


# ----------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lecture_dir", nargs="?")
    ap.add_argument("--media", default=None)
    ap.add_argument("--groq-json", default=None)
    ap.add_argument("--window", type=float, default=60.0)
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--low-sim", type=float, default=LOW_SIM_THRESHOLD,
                    help="LOW_SIM Jaccard threshold (default calibrated; see docstring)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if not args.lecture_dir or not os.path.isdir(args.lecture_dir):
        print("ERROR: lecture_dir is required and must exist.", file=sys.stderr)
        sys.exit(2)

    # Known-sample gate before anything touches an external API (house rule).
    selftest()

    d = args.lecture_dir
    primary_path = os.path.join(d, "transcript.json")
    primary = load_segments(primary_path)
    groq, groq_path, groq_src, wall = get_groq(d, args.media, args.groq_json, args.lang)

    wins = analyze(primary, groq, window=args.window, sim_threshold=args.low_sim)
    meta = {
        "banner": BANNER,
        "primary_path": os.path.abspath(primary_path), "primary_segments": len(primary),
        "groq_path": groq_path, "groq_segments": len(groq), "groq_source": groq_src,
        "groq_wall_seconds": None if wall is None else round(wall, 1),
        "opencc": _OPENCC_NAME, "window_s": args.window,
        "low_sim_threshold": args.low_sim,
        "thresholds": {"gap_ratio": GAP_RATIO, "gap_min_groq_chars": GAP_MIN_GROQ_CHARS,
                       "loop_line_repeats": LOOP_LINE_REPEATS, "loop_ngram": LOOP_NGRAM,
                       "loop_diversity": LOOP_DIVERSITY},
        "generated": time.strftime("%Y/%m/%d %H:%M"),
    }
    doc, md = build_report(wins, meta)

    # Write only now, atomically: a failure above never clobbers a previous audit.
    atomic_write_json(os.path.join(d, "asr_audit.json"), doc)
    md_path = os.path.join(d, "asr_audit.md")
    with open(md_path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(md)
    os.replace(md_path + ".tmp", md_path)

    s = doc["summary"]
    print(f"windows={s['windows']} counts={s['flag_counts']} "
          f"primary_suspect={s['primary_suspect_windows']} groq_unreliable={s['groq_unreliable_windows']}")
    print(f"jaccard={s['jaccard_distribution']}")
    print(f"-> {os.path.join(d, 'asr_audit.md')}")


if __name__ == "__main__":
    main()
