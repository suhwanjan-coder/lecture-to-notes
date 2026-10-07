"""Stage F tier-pass, deterministic part: implements reference/note-spec.md#tier-scoring
(Steps 1-7: weights, content-type bonuses, penalties, thresholds, hard overrides
incl. the P1 text-redundancy override and the IMG floor, width table, debug trail)
for one lecture dir. Weights / bonuses / penalties / thresholds / overrides all
come from config.yaml `scoring`; nothing is hard-coded here except the spec's
chart/imaging type sets and the width table.

Reads   <lecture_dir>/slides_grounded.json  (+ transcript.json)
Writes  <lecture_dir>/slides_final.json     (superset of slides_grounded.json;
                                             every entry has `filename` AND
                                             `attachment_name`, integer `tier`)
        <lecture_dir>/transcript.txt        (only if missing)

The P1 *synthesis-time* markdown-overlap check is NOT done here: it needs the
bullets the writer just wrote, so it stays with the note writer
(`embed_suppressed_reason = "markdown_overlap"`).

Usage:
    python tier_pass.py <lecture_dir> --prefix 胡婉妍_加強型流感疫苗 [--dry-run]
      --prefix   attachment prefix; the n-th cited (Tier 1/2) slide is named
                 {prefix}_s{n:02d}.jpg, Tier 3 slides continue the numbering.
      --dry-run  print the per-slide lines and the summary, write nothing.
"""
import argparse, json, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _common import atomic_write_json, load_config, write_transcript_lines  # noqa: E402

CHART = {"flowchart", "chart", "kaplan_meier", "scatter_plot"}
IMG = {"ultrasound", "xray", "mri"}
FLOOR_TYPES = IMG | {"anatomy"} | CHART

# Latin-script screen-share chrome that the (CJK) config tokens do not cover.
_LATIN_CHROME = re.compile(r"^\W*(zoom|rec|orec|workplace|admin.*|webex|teams|meet)\W*$", re.I)
_CJK_CHROME = re.compile(r"登[入人出]|[螢莹]幕|的幕|正[在任]發言|正[在任]发言|音訊設定|問與答|離開")
_PURE_NUMBER = re.compile(r"^[\d\s.,%:;/\\\-–—+()（）\[\]#*]+$")
_MEANINGFUL = re.compile(r"[A-Za-z一-鿿]")

FORCE1_FN = set()  # set from config in main(); used by embed_width


def ui_tokens(cfg):
    toks = (cfg.get("dedup") or {}).get("ui_chrome_tokens")
    if isinstance(toks, list) and toks:
        return [str(t) for t in toks]
    try:  # same fallback list the dedup stage uses
        from dedup_semantic import _DEFAULT_UI_CHROME_TOKENS
        return list(_DEFAULT_UI_CHROME_TOKENS)
    except Exception:
        return ["聊天", "人員", "舉手", "檢視", "照相機", "視訊", "麥克風", "共享", "邀請", "錄製"]


def good_line(line, tokens):
    """True if an OCR line can serve as a title: not blank, not a pure number,
    not Zoom chrome, and carries at least 2 letters/CJK characters."""
    ln = (line or "").strip()
    if len(_MEANINGFUL.findall(ln)) < 2:
        return False
    if _PURE_NUMBER.match(ln) or _LATIN_CHROME.match(ln) or _CJK_CHROME.search(ln):
        return False
    return not any(tok in ln for tok in tokens)


def clean_title(s, tokens):
    """Title guess first, then quick_text lines, then VLM visible_labels; the
    first line passing good_line wins. '' if none."""
    ocr = s.get("ocr") or {}
    cands = [ocr.get("quick_title_guess") or ""]
    cands += (ocr.get("quick_text") or "").splitlines()
    cands += (s.get("vlm_signals") or {}).get("visible_labels") or []
    for c in cands:
        if good_line(c, tokens):
            return re.sub(r"\s+", " ", c.strip())
    return ""


def embed_width(v):
    """Width table (note-spec#widths)."""
    ct = set(v.get("content_type") or [])
    fn = set(v.get("apparent_educational_function") or [])
    if ct & IMG or v.get("contains_clinical_imaging"):
        return 600
    if "anatomy" in ct:
        return 500
    if "flowchart" in ct or v.get("contains_algorithm") or fn & FORCE1_FN:
        return 500
    if ct & {"chart", "kaplan_meier", "scatter_plot"}:
        return 500
    if (ct - {"title"}) == {"table"}:
        return 400
    return 500


def score_slide(s, cfg):
    """Return (combined_score, tier, reason) per note-spec Steps 1-5."""
    W, P, B = cfg["weights"], cfg["penalties"], cfg["content_type_bonuses"]
    T1, T2 = cfg["tier_thresholds"]["tier_1"], cfg["tier_thresholds"]["tier_2"]
    force1 = set(cfg["hard_overrides"]["force_tier_1_if_function_includes"])
    force3 = [set(x) for x in cfg["hard_overrides"]["force_tier_3_if_content_type_only_set"]]
    v, t = s.get("vlm_signals"), s.get("transcript_signals") or {}
    if not v or s.get("vlm_error"):
        return 0.0, 3, "vlm_failed_conservative"
    ct = set(v.get("content_type") or [])
    fn = set(v.get("apparent_educational_function") or [])
    red = v.get("text_redundancy") or 0.0

    def g(k):
        return t.get(k) or 0.0

    # Step 1 weights
    score = (W["visual_complexity"] * (v.get("visual_complexity") or 0)
             + W["non_textual_information_density"] * (v.get("non_textual_information_density") or 0)
             + W["speaker_reference_density"] * g("speaker_reference_density")
             + W["speaker_emphasis_score"] * g("speaker_emphasis_score")
             + W["time_spent_normalized"] * min(g("time_spent_seconds") / 120, 1.0))
    # Step 2 bonuses (membership, never equality)
    for typ, b in B.items():
        if typ in ct:
            score += b
    # Step 3 penalties
    score += P["speaker_skip_score"] * g("speaker_skip_score")
    score += P["speaker_confusion_score"] * g("speaker_confusion_score")
    if red > P["text_redundancy_threshold"]:
        score += P["text_redundancy_penalty"]
    # Step 4 clip + thresholds
    score = max(0.0, min(1.0, score))
    tier = 1 if score >= T1 else 2 if score >= T2 else 3
    reason, forced3 = None, False
    # Step 5 hard overrides, in spec order
    hit = fn & force1
    if hit:
        tier, reason = 1, f"function includes {sorted(hit)[0]}"
    elif any(ct == f for f in force3):
        tier, reason, forced3 = 3, "content_type only title/decorative", True
    elif (red >= 0.7 and ct <= {"text", "title", "decorative"}
          and not v.get("contains_clinical_imaging") and not v.get("contains_algorithm")
          and not (ct & CHART)):  # P1 text-redundancy override
        tier, reason, forced3 = 3, "text-redundant, no visual signal", True
    # IMG floor: a distinct imaging/chart frame is never silently dropped
    if (not forced3 and tier == 3 and (s.get("dedup") or {}).get("is_canonical")
            and (v.get("contains_clinical_imaging") or (ct & FLOOR_TYPES))):
        typ = sorted(ct & FLOOR_TYPES)[0] if ct & FLOOR_TYPES else "imaging"
        tier, reason = 2, f"imaging floor (canonical {typ})"
    return score, tier, reason


def main():
    global FORCE1_FN
    ap = argparse.ArgumentParser(description="Deterministic Tier-pass (note-spec Steps 1-7)")
    ap.add_argument("lecture_dir")
    ap.add_argument("--prefix", required=True,
                    help="attachment prefix, e.g. 胡婉妍_加強型流感疫苗 -> {prefix}_s01.jpg")
    ap.add_argument("--dry-run", action="store_true", help="print only, write nothing")
    args = ap.parse_args()

    full_cfg = load_config()
    cfg = full_cfg.get("scoring")
    if not cfg:
        print("ERROR: config.yaml has no `scoring` section", file=sys.stderr)
        sys.exit(2)
    FORCE1_FN = set(cfg["hard_overrides"]["force_tier_1_if_function_includes"])
    tokens = ui_tokens(full_cfg)

    lec = os.path.abspath(args.lecture_dir)
    gp = os.path.join(lec, "slides_grounded.json")
    if not os.path.isfile(gp):
        print(f"ERROR: {gp} not found", file=sys.stderr)
        sys.exit(2)
    with open(gp, encoding="utf-8") as fh:
        slides = json.load(fh)

    out = []
    for s in slides:
        row = dict(s)  # superset of slides_grounded
        row["pipeline_stage"] = "final"
        score, tier, reason = score_slide(s, cfg)
        row["combined_score"] = round(score, 3)
        row["tier"] = int(tier)
        row["tier_override_reason"] = reason
        row["embed_width"] = embed_width(s.get("vlm_signals") or {}) if tier in (1, 2) else None
        row["embed_suppressed_reason"] = None
        title = clean_title(s, tokens) or f"slide {s['slide_id']}"
        row["retrieval"] = dict(s.get("retrieval") or {})
        row["retrieval"]["summary_sentence"] = title[:25]
        row["section_suggestion"] = title[:40]
        out.append(row)

    # attachment names: sequential among cited (Tier 1/2); Tier 3 continues
    n = 0
    for tiers in ((1, 2), (3,)):
        for r in out:
            if r["tier"] in tiers:
                n += 1
                r["attachment_name"] = f"{args.prefix}_s{n:02d}.jpg"
    for r in out:  # `filename` = the extracted frame; keep if present
        if not r.get("filename"):
            r["filename"] = r["attachment_name"]

    c = {k: sum(1 for r in out if r["tier"] == k) for k in (1, 2, 3)}
    for r in out:
        print(f"s{int(r['slide_id']):02d} T{r['tier']} {r['combined_score']:.2f} "
              f"w={r['embed_width']} {r['attachment_name']} | "
              f"{r['tier_override_reason'] or ''} | {r['section_suggestion'][:30]}")

    if args.dry_run:
        print("[dry-run] nothing written")
    else:
        tp = os.path.join(lec, "transcript.txt")
        trj = os.path.join(lec, "transcript.json")
        if not os.path.isfile(tp) and os.path.isfile(trj):
            with open(trj, encoding="utf-8") as fh:
                write_transcript_lines(json.load(fh), tp)
            print(f"wrote {tp}")
        atomic_write_json(os.path.join(lec, "slides_final.json"), out)
        print(f"wrote {os.path.join(lec, 'slides_final.json')}")
    print(f"TIER DONE: T1={c[1]} T2={c[2]} T3={c[3]} total={len(out)}")


if __name__ == "__main__":
    main()
