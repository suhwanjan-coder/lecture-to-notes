"""Stage E: transcript ↔ slide semantic grounding (0 LLM tokens).

Replaces naive "align by timestamp proximity". For each canonical slide,
computes positive signals (reference_density, emphasis_score, time_spent),
negative signals (skip_score, confusion_score), retrieval keywords, and
clinical_domain mapping by walking the transcript with a ±20s window.

Keyword classification (solves abbreviation false-positive problem):
  - abbreviation (≤4 chars ALL_CAPS or in whitelist) → exact word-boundary
  - drug (-mab/-inib/-statin/...)                     → fuzzy ≥90
  - medical_term (-itis/-oma/-pathy/...)             → fuzzy ≥85
  - general (≥5 chars)                                → fuzzy ≥88
  - cjk (a run of ≥2 Han chars, one keyword per run)  → 2-3 chars: exact;
        ≥4 chars: HIT when ≥ keyword_grounding.cjk_bigram_hit_ratio (default 0.4)
        of the run's character bigrams occur anywhere in the window text
        (coverage test, not similarity — speech never repeats a slide line
        verbatim, so fuzzy-matching a whole OCR line against one segment ≈ 0).

CJK text on BOTH sides (OCR/VLM text and transcript) is normalized to Traditional
with OpenCC s2t first (RapidOCR and Groq ASR emit Simplified; slides may be
Traditional). OCR lines containing a `dedup.ui_chrome_tokens` entry, or Zoom/Teams
chrome words (Admin, REC, Workplace, zoom), are dropped before keyword extraction.
Latin keyword logic is unchanged, except: Latin tokens >18 chars without a hyphen are
dropped (OCR word-joins); chrome token lines are dropped only when <=8 chars (UI
labels are short; longer lines keep their text); and keywords present on more than
keyword_grounding.boilerplate_slide_ratio (default 0.4) of the deck's canonical
slides (footers, sponsor names, banners) are dropped before scoring — skipped for
decks of <5 canonical slides.

Usage:
    python ground_slides.py <out_dir> [--config <path>] [--window-seconds 20]

Inputs:
    <out_dir>/slides_vlm.json
    <out_dir>/transcript.json  (authoritative; transcript_clean.json = legacy fallback)
    <skill_root>/config.yaml         (auto-detected sibling of scripts/)

Output:
    <out_dir>/slides_grounded.json   (pipeline_stage="grounded")
"""
import argparse
import json
import os
import re
import sys
import time
from functools import lru_cache

from _common import atomic_write_json, load_segments
from dedup_semantic import _DEFAULT_UI_CHROME_TOKENS


# --------------------------- optional deps ------------------------------------

def try_import_rapidfuzz():
    try:
        from rapidfuzz import fuzz
        return fuzz
    except ImportError:
        print("WARNING: rapidfuzz not installed; fuzzy keyword match disabled "
              "(falls back to substring match).", file=sys.stderr)
        return None


def try_import_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        print("WARNING: pyyaml not installed; using built-in default config.",
              file=sys.stderr)
        return None


_OPENCC = None  # None = not tried yet; False = unavailable (warned once)


@lru_cache(maxsize=None)
def _to_trad(text):
    """Normalize CJK text to Traditional (OpenCC s2t). Degrades loudly, not silently."""
    global _OPENCC
    if _OPENCC is None:
        try:
            from opencc import OpenCC
            _OPENCC = OpenCC("s2t")
        except ImportError:
            print("WARNING: opencc not installed (pip install "
                  "opencc-python-reimplemented); Simplified/Traditional text is NOT "
                  "normalized, CJK keyword hits will be under-counted.",
                  file=sys.stderr)
            _OPENCC = False
    return _OPENCC.convert(text) if _OPENCC else text


# --------------------------- config defaults ----------------------------------

DEFAULT_CONFIG = {
    "keyword_grounding": {
        "abbreviation_whitelist": [
            "ALS", "ADHD", "CPET", "RTC", "ROM", "DTR", "ABI", "CMAP",
            "NCS", "EMG", "MRI", "CT", "US", "PT", "OT", "ST",
            "ESWT", "TENS", "NMES", "FES", "FIM", "MMT", "COPD", "ILD",
            "HFrEF", "HFpEF", "VO2", "VE", "AT", "TNM", "DVT", "PE",
            "SCI", "TBI", "CP", "DMD",
        ],
        "drug_suffixes": [
            "mab", "inib", "statin", "pril", "prazole", "olol",
            "sartan", "pine", "gliflozin", "parin",
        ],
        "medical_term_suffixes": [
            "itis", "oma", "pathy", "osis", "ectomy", "plasty",
            "algia", "emia", "rhea", "centesis",
        ],
        "clinical_domain_map": {},
        # CJK keyword (run of >=4 Han chars) hits when this fraction of its
        # character bigrams occurs in the window text. 2-3 char runs: exact.
        "cjk_bigram_hit_ratio": 0.4,
        # Keywords found on more than this fraction of the deck's canonical slides
        # (footers, sponsor names, banners) are ignored. Skipped for decks <5 slides.
        "boilerplate_slide_ratio": 0.4,
        "emphasis_cues_zh": ["重要", "最重要", "核心", "記住", "注意",
                              "千萬", "關鍵", "重點", "一定要"],
        "emphasis_cues_en": ["key point", "important", "remember", "critical",
                              "essential", "must", "absolutely", "takeaway",
                              "highlight", "decision making"],
        "confusion_cues": ["跳過", "skip", "let's move on", "不重要",
                            "next slide", "等一下回來", "back to",
                            "我們先看", "abandon", "忽略"],
    }
}


def deep_merge(base, override):
    """Recursive dict merge; override wins on leaves.

    An explicit ``null`` in YAML means "leave the default alone", not "set this
    key to None" — a commented-out-by-nulling config key used to replace a list
    with None and crash the consumer several stages later.
    """
    out = dict(base)
    for k, v in (override or {}).items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(config_path):
    yaml = try_import_yaml()
    cfg = DEFAULT_CONFIG
    if yaml is None or not config_path or not os.path.isfile(config_path):
        return cfg
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        return deep_merge(cfg, user_cfg)
    except Exception as e:
        print(f"WARNING: failed to load config {config_path}: {e}; using defaults",
              file=sys.stderr)
        return cfg


# --------------------------- keyword extraction -------------------------------

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}|[一-鿿]{2,}")


_CJK_RUN_RE = re.compile(r"[一-鿿]+")
_CJK_TOKEN_RE = re.compile(r"^[一-鿿]{2,}$")
# Latin-script screen-share chrome (Zoom/Teams); CJK chrome comes from config.
# Lookarounds, not \b: OCR glues it to Han chars ("Admin的幕") and \w includes Han;
# "o?rec" also catches the "OREC" garble of the REC badge.
_LATIN_CHROME_RE = re.compile(
    r"(?<![A-Za-z])(?:admin|o?rec|workplace|zoom)(?![A-Za-z])", re.IGNORECASE)


def _chrome_tokens(cfg):
    toks = (cfg.get("dedup") or {}).get("ui_chrome_tokens")
    if not (isinstance(toks, list) and toks):
        toks = _DEFAULT_UI_CHROME_TOKENS
    # Normalized too: the lines they are tested against are already Traditional.
    return [_to_trad(str(t)) for t in toks]


# CJK chrome tokens only drop SHORT lines: UI labels are short, slide sentences that
# merely contain e.g. 人員 / 共享 must keep their text. 登入 / 登人 (Zoom login label,
# and its OCR garble) ride on the same rule without touching dedup.ui_chrome_tokens.
_CHROME_MAX_LINE_LEN = 8
_EXTRA_SHORT_CHROME = ("登入", "登人")
# OCR-glued Latin words ("figureadaptedfromsantoroa") never match speech.
_LATIN_GLUE_MIN_LEN = 19


def _drop_chrome_lines(text, chrome_tokens):
    """Remove OCR lines that are screen-share UI chrome (text is already Traditional)."""
    keep = []
    for ln in (text or "").splitlines():
        if _LATIN_CHROME_RE.search(ln):
            continue
        if len(ln.strip()) <= _CHROME_MAX_LINE_LEN and any(
                tok in ln for tok in tuple(chrome_tokens) + _EXTRA_SHORT_CHROME):
            continue
        keep.append(ln)
    return "\n".join(keep)


def classify_keyword(token, cfg):
    g = cfg["keyword_grounding"]
    if not token:
        return None
    t = token.strip()
    if _CJK_TOKEN_RE.match(t):
        return "cjk"
    if len(t) >= _LATIN_GLUE_MIN_LEN and "-" not in t:
        return None
    # Abbreviation: ≤4 chars and uppercase, OR in whitelist
    if (len(t) <= 4 and t.isupper() and t.isalpha()) or t.upper() in {
            x.upper() for x in g.get("abbreviation_whitelist", [])}:
        return "abbreviation"
    tl = t.lower()
    for suf in g.get("drug_suffixes", []):
        if tl.endswith(suf.lower()) and len(tl) > len(suf) + 2:
            return "drug"
    for suf in g.get("medical_term_suffixes", []):
        if tl.endswith(suf.lower()) and len(tl) > len(suf) + 2:
            return "medical_term"
    if len(t) >= 5:
        return "general"
    return None


def extract_keywords(slide, cfg):
    """Return list of (token, type) tuples deduplicated, lowercased except abbr."""
    ocr = slide.get("ocr") or {}
    vlm = slide.get("vlm_signals") or {}
    chrome = _chrome_tokens(cfg)
    # OCR engines see the Zoom/Teams chrome; vlm_text / visible_labels do not.
    texts = [
        # Stage B2 (Surya) high-quality OCR, preferred
        _drop_chrome_lines(_to_trad(ocr.get("clean_text") or ""), chrome),
        _to_trad(ocr.get("vlm_text") or ""),
        _drop_chrome_lines(_to_trad(ocr.get("quick_text") or ""), chrome),
        _drop_chrome_lines(_to_trad(ocr.get("quick_title_guess") or ""), chrome),
    ]
    for lab in (vlm.get("visible_labels") or []):
        if isinstance(lab, str):
            texts.append(_to_trad(lab))
    blob = "\n".join(texts)

    seen = {}
    for m in TOKEN_RE.findall(blob):
        kt = classify_keyword(m, cfg)
        if kt is None:
            continue
        key = m if kt == "abbreviation" else m.lower()
        if key in seen:
            continue
        seen[key] = kt
    return list(seen.items())


# --------------------------- transcript matching ------------------------------

def window_segments(segments, start_sec, end_sec):
    """Return segments overlapping [start_sec, end_sec]; list of dicts + idx."""
    out = []
    for i, seg in enumerate(segments):
        s, e = seg.get("start", 0), seg.get("end", 0)
        if e < start_sec or s > end_sec:
            continue
        out.append((i, seg))
    return out


# Keywords at or below this length are matched EXACTLY, never fuzzily.
#
# partial_ratio scores the best-aligned substring of the haystack, and for a
# short needle a single lucky alignment anywhere in a ±20s window is enough.
# Measured 2026-08-02 on synthetic windows: the effect is NOT the blanket
# "everything scores >=88" the audit assumed (rapidfuzz normalizes by needle
# length, so an absent 4-6 char keyword scores ~50-75), but two real false
# positives remain and both are fixed here — a keyword found INSIDE a longer
# unrelated word ('motion' in 'emotional' scored 100), and an alignment that
# spans the join between two unrelated segments. One edit in a 6-char needle
# also lands at 83, uncomfortably close to the 88 line.
_EXACT_MATCH_MAX_LEN = 7

_LATIN_KW_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*$")


def _exact_hit(text_lower, kw_lower):
    """Substring match, anchored to a word START for latin keywords.

    CJK has no word boundaries, so plain substring is right there. For latin the
    match must begin a word — 'motion' should not be found inside 'emotional' —
    but the END is deliberately left free so ordinary inflection still counts
    ('tendon' matches 'tendons', 'atrophy' matches 'atrophied').
    """
    if _LATIN_KW_RE.match(kw_lower):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(kw_lower)}", text_lower
        ) is not None
    return kw_lower in text_lower


def keyword_hit(seg_texts_lower, kw, kw_type, fuzz_lib):
    """Returns True iff this kw hits any ONE transcript segment per its type's rule.

    Two changes from the original whole-window scoring, both aimed at the same
    failure (every keyword hitting, so reference_density ~1.0 everywhere):

    1. Scoring is per SEGMENT (roughly a sentence) and OR-ed, not against the
       whole concatenated ±20s window. A fuzzy score against a 400-char blob is
       a "does this appear anywhere" test, not a similarity test.
    2. Keywords shorter than _EXACT_MATCH_MAX_LEN+1 chars must match exactly.

    Abbreviations are handled separately via abbreviation_hit (original-case
    word-boundary). This function only handles the fuzzy types.
    """
    kwl = kw.lower()
    if len(kwl) <= _EXACT_MATCH_MAX_LEN:
        return any(_exact_hit(t, kwl) for t in seg_texts_lower)
    if fuzz_lib is None:
        return any(kwl in t for t in seg_texts_lower)
    threshold = {"drug": 90, "medical_term": 85, "general": 88}[kw_type]
    return any(fuzz_lib.partial_ratio(kwl, t) >= threshold for t in seg_texts_lower)


def _cjk_runs(texts):
    """Han-only runs per transcript segment (whitespace between Han chars removed,
    since ASR segments sometimes carry stray spaces inside a word)."""
    runs = []
    for t in texts:
        t = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", t or "")
        runs.extend(_CJK_RUN_RE.findall(t))
    return runs


def cjk_hit(window_runs, window_bigrams, kw, ratio):
    """CJK keyword (one run of >=2 Han chars) hit test over the window text.

    2-3 chars: the run must occur verbatim. >=4 chars: at least `ratio` of the
    run's character bigrams must occur somewhere in the window — a coverage test,
    so a slide line paraphrased or split across sentences still counts.
    """
    if len(kw) < 4:
        return any(kw in r for r in window_runs)
    bigrams = {kw[i:i + 2] for i in range(len(kw) - 1)}
    return len(bigrams & window_bigrams) / len(bigrams) >= ratio


def abbreviation_hit(orig_text, kw):
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z0-9])")
    return pattern.search(orig_text) is not None


def cue_density(seg_texts, cues):
    """Fraction of segments containing any cue (case-insensitive substring)."""
    if not seg_texts:
        return 0.0
    cues_lc = [c.lower() for c in cues]
    hits = 0
    for t in seg_texts:
        tl = (t or "").lower()
        if any(c in tl for c in cues_lc):
            hits += 1
    return hits / len(seg_texts)


# --------------------------- per-slide grounding ------------------------------

def deck_boilerplate(slides, cfg):
    """Keywords present on > boilerplate_slide_ratio of the canonical slides.

    Footers, sponsor names and conference banners repeat on every slide but are
    never spoken, so they only dilute reference_density. Computed once per run;
    returns an empty set for decks of <5 canonical slides (too few to call
    anything "repeated").
    """
    ratio = float(cfg["keyword_grounding"].get("boilerplate_slide_ratio", 0.4))
    canon = [s for s in slides if s.get("dedup", {}).get("is_canonical")]
    if len(canon) < 5:
        return frozenset()
    df = {}
    for s in canon:
        for kw, _ in extract_keywords(s, cfg):
            df[kw] = df.get(kw, 0) + 1
    return frozenset(kw for kw, n in df.items() if n / len(canon) > ratio)


def compute_signals(slide, segments, cfg, fuzz_lib, window_seconds,
                    boilerplate=frozenset()):
    g = cfg["keyword_grounding"]

    ts_start = slide.get("timestamp_start", 0) or 0
    ts_end = slide.get("timestamp_end", ts_start) or ts_start
    w_start = max(0, ts_start - window_seconds)
    w_end = ts_end + window_seconds

    win = window_segments(segments, w_start, w_end)
    seg_texts = [_to_trad(s.get("text", "") or "") for _, s in win]
    seg_ids = [i for i, _ in win]
    combined = " ".join(seg_texts)
    seg_texts_lower = [t.lower() for t in seg_texts]
    word_count = len(re.findall(r"\S+", combined))
    window_runs = _cjk_runs(seg_texts)
    window_bigrams = {r[i:i + 2] for r in window_runs for i in range(len(r) - 1)}
    cjk_ratio = float(g.get("cjk_bigram_hit_ratio", 0.4))

    keywords = [(k, t) for k, t in extract_keywords(slide, cfg)
                if k not in boilerplate]
    if not keywords:
        speaker_reference_density = 0.0
        hit_keywords = []
    else:
        hits = []
        for kw, kt in keywords:
            if kt == "abbreviation":
                if abbreviation_hit(combined, kw):
                    hits.append(kw)
            elif kt == "cjk":
                if cjk_hit(window_runs, window_bigrams, kw, cjk_ratio):
                    hits.append(kw)
            else:
                if keyword_hit(seg_texts_lower, kw, kt, fuzz_lib):
                    hits.append(kw)
        speaker_reference_density = len(hits) / len(keywords)
        hit_keywords = hits

    emphasis_cues = g.get("emphasis_cues_zh", []) + g.get("emphasis_cues_en", [])
    speaker_emphasis_score = cue_density(seg_texts, emphasis_cues)
    speaker_confusion_score = cue_density(seg_texts, g.get("confusion_cues", []))

    time_spent = max(0, ts_end - ts_start)
    if time_spent < 10 and word_count < 30:
        speaker_skip_score = 0.9
    else:
        speaker_skip_score = max(0.0, min(1.0, 1.0 - speaker_reference_density))

    # clinical_domain mapping
    domain_map = g.get("clinical_domain_map", {}) or {}
    domains = []
    full_blob = (
        " ".join([(slide.get("ocr") or {}).get(k, "") or ""
                  for k in ("clean_text", "vlm_text", "quick_text", "quick_title_guess")])
        + " " + combined
    ).lower()
    for domain, hints in domain_map.items():
        # A single-hint domain is naturally written `msk: shoulder` in YAML,
        # which arrives as a str — iterating it would test each CHARACTER and
        # match nearly everything.
        if isinstance(hints, str):
            hints = [hints]
        elif not isinstance(hints, (list, tuple)):
            print(f"WARNING: clinical_domain_map['{domain}'] is "
                  f"{type(hints).__name__}, expected a list of strings; skipping",
                  file=sys.stderr)
            continue
        for h in hints:
            if not isinstance(h, str) or not h:
                continue
            if h.lower() in full_blob:
                domains.append(domain)
                break

    # Retrieval keywords: top-5 by hit, then any keyword if none hit
    retrieval = list(dict.fromkeys(hit_keywords))[:5]
    if len(retrieval) < 5:
        for kw, _ in keywords:
            if kw not in retrieval:
                retrieval.append(kw)
            if len(retrieval) >= 5:
                break

    return {
        "transcript_signals": {
            "speaker_reference_density": round(speaker_reference_density, 3),
            "speaker_emphasis_score": round(speaker_emphasis_score, 3),
            "speaker_skip_score": round(speaker_skip_score, 3),
            "speaker_confusion_score": round(speaker_confusion_score, 3),
            "time_spent_seconds": time_spent,
            "transcript_segment_ids": seg_ids,
        },
        "retrieval": {
            "retrieval_keywords": retrieval,
            "summary_sentence": None,
            "clinical_domain": list(dict.fromkeys(domains)),
        },
        "grounding_support": round(speaker_reference_density, 3),
    }


# --------------------------- main ---------------------------------------------

def load_transcript(out_dir):
    """Load the lecture transcript, or exit 2 if there isn't one.

    transcript.json is authoritative (2026-07-26): the auto-cleanup pass that
    produced transcript_clean.json was retired for rewriting garbles into
    confident wrong terms — see HARD RULE #5 in SKILL.md. The clean file is
    still accepted as a fallback so pre-2026-07-26 lecture dirs keep working.

    A missing transcript is fatal, not a warning: every transcript_signal would
    be 0, which downstream tiering reads as "the speaker skipped this slide" —
    a confident wrong answer indistinguishable from a real one.
    """
    plain = os.path.join(out_dir, "transcript.json")
    clean = os.path.join(out_dir, "transcript_clean.json")
    path = plain if os.path.isfile(plain) else clean
    if not os.path.isfile(path):
        print(f"ERROR: no transcript found in {out_dir} (looked for "
              "transcript.json, then transcript_clean.json). Grounding without a "
              "transcript would mark every slide as skipped. Run "
              "transcribe_video.py for this lecture first.", file=sys.stderr)
        sys.exit(2)
    return load_segments(path)


def main():
    parser = argparse.ArgumentParser(description="Stage E: transcript grounding")
    parser.add_argument("out_dir", help="Lecture output directory")
    parser.add_argument("--config", default=None,
                        help="Path to config.yaml (default: skill_root/config.yaml)")
    parser.add_argument("--window-seconds", type=int, default=20,
                        help="Transcript window padding around slide timestamp (default 20s)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite output (default behavior)")
    args = parser.parse_args()

    vlm_path = os.path.join(args.out_dir, "slides_vlm.json")
    out_path = os.path.join(args.out_dir, "slides_grounded.json")

    if not os.path.isfile(vlm_path):
        print(f"ERROR: {vlm_path} not found. Run ocr_slides.py first.", file=sys.stderr)
        sys.exit(2)

    if args.config:
        config_path = args.config
    else:
        # skill_root = parent of scripts/
        config_path = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml"
        ))

    cfg = load_config(config_path)
    fuzz_lib = try_import_rapidfuzz()

    with open(vlm_path, "r", encoding="utf-8") as f:
        slides = json.load(f)
    segments = load_transcript(args.out_dir)

    boilerplate = deck_boilerplate(slides, cfg)
    if boilerplate:
        print(f"  Deck boilerplate keywords dropped: {len(boilerplate)}")

    start = time.time()
    grounded = []
    for s in slides:
        if not s.get("dedup", {}).get("is_canonical"):
            s["pipeline_stage"] = "grounded"
            grounded.append(s)
            continue
        sig = compute_signals(s, segments, cfg, fuzz_lib, args.window_seconds,
                              boilerplate)
        s["transcript_signals"] = sig["transcript_signals"]
        s["retrieval"] = sig["retrieval"]
        s.setdefault("ocr", {})["grounding_support"] = sig["grounding_support"]
        s["pipeline_stage"] = "grounded"
        grounded.append(s)

        ts = sig["transcript_signals"]
        print(f"  Slide {s['slide_id']:3d}: "
              f"ref={ts['speaker_reference_density']:.2f} "
              f"emph={ts['speaker_emphasis_score']:.2f} "
              f"skip={ts['speaker_skip_score']:.2f} "
              f"conf={ts['speaker_confusion_score']:.2f} "
              f"time={ts['time_spent_seconds']}s "
              f"dom={sig['retrieval']['clinical_domain']}")

    atomic_write_json(out_path, grounded)

    elapsed = time.time() - start
    canonical_count = sum(1 for s in grounded if s.get("dedup", {}).get("is_canonical"))
    print(f"\nDone: {canonical_count} canonical slides grounded in {elapsed:.1f}s "
          f"→ {out_path}")


if __name__ == "__main__":
    main()
