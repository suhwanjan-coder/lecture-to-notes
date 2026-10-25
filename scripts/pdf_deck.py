"""Audio + PDF deck bridge (Path B) for the local flow.

    python pdf_deck.py render <pdf> <lecture_dir>
        PDF pages -> slides/page_NN.jpg + pdf_text.json, then the upstream
        build_slides_from_pdf.py makes slides_raw.json / slides_dedup.json.

    python pdf_deck.py align <lecture_dir>
        After transcription: give every page a real [timestamp_start, timestamp_end]
        by monotonic alignment of the transcript to the page text (the upstream
        bridge only spreads pages evenly over the audio, which breaks grounding).

Alignment: each transcript segment is scored against each page by idf-weighted
character-bigram / word overlap; a dynamic program picks a non-decreasing page for
every segment (the speaker may skip pages but rarely goes back). Pages no segment
lands on get a zero-length span at the previous boundary. Result is written to
slides_raw.json, slides_dedup.json and pdf_align.json (for inspection).
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import atomic_write_json, fmt_hms, load_segments  # noqa: E402

RENDER_WIDTH = 1600
JUMP_PENALTY = 0.15      # per page skipped forward in one step (column z-scores)
SMOOTH = 3               # segments each side averaged into a segment's score row


def render(pdf: Path, lec: Path):
    import pymupdf
    slides = lec / "slides"
    slides.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf))
    pages = []
    for i, page in enumerate(doc, 1):
        zoom = RENDER_WIDTH / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        fn = f"page_{i:02d}.jpg"
        pix.save(str(slides / fn), jpg_quality=88)
        pages.append({"page": i, "image_filename": fn, "slide_id": i,
                      "text": page.get_text("text").strip()})
    atomic_write_json(str(lec / "pdf_text.json"), pages)
    empty = sum(1 for p in pages if not p["text"])
    print(f"rendered {len(pages)} pages -> {slides} ({empty} without text layer)")
    subprocess.run([sys.executable, str(HERE / "build_slides_from_pdf.py"), str(lec)],
                   check=True)


# ---------------------------------------------------------------- alignment --

def _grams(text: str):
    text = text.lower()
    cjk = re.findall(r"[一-鿿]+", text)
    g = set()
    for run in cjk:
        g.update(run[i:i + 2] for i in range(len(run) - 1))
    g.update(w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", text))
    return g


def score_matrix(segments, pages):
    page_g = [_grams(p.get("ocr", {}).get("quick_text", "")) for p in pages]
    n = len(pages)
    df = {}
    for g in page_g:
        for x in g:
            df[x] = df.get(x, 0) + 1
    idf = {x: math.log(1 + n / c) for x, c in df.items()}
    rows = []
    for seg in segments:
        sg = _grams(seg.get("text", ""))
        row = []
        for g in page_g:
            hit = sg & g
            row.append(sum(idf[x] for x in hit) / (1.0 + len(sg) ** 0.5) if hit else 0.0)
        rows.append(row)
    # smooth over neighbouring segments: one sentence rarely names its slide
    m = len(rows)
    sm = []
    for i in range(m):
        lo, hi = max(0, i - SMOOTH), min(m, i + SMOOTH + 1)
        sm.append([sum(rows[k][j] for k in range(lo, hi)) / (hi - lo) for j in range(n)])
    # a text-heavy page matches everything; put every page on the same scale
    for j in range(n):
        col = [sm[i][j] for i in range(m)]
        mean = sum(col) / m
        sd = (sum((v - mean) ** 2 for v in col) / m) ** 0.5 or 1.0
        for i in range(m):
            sm[i][j] = (sm[i][j] - mean) / sd
    return sm


def monotone_path(S):
    m, n = len(S), len(S[0])
    # centre each row so silence / chatter does not pull toward any page
    S = [[v - sum(r) / n for v in r] for r in S]
    NEG = -1e18
    best = [[NEG] * n for _ in range(m)]
    back = [[0] * n for _ in range(m)]
    for j in range(n):
        best[0][j] = S[0][j] - JUMP_PENALTY * j * 0.2
    for i in range(1, m):
        # best predecessor k <= j with penalty for forward jumps
        run_best, run_k = NEG, 0
        for j in range(n):
            # candidate: stay or come from any k <= j; penalise (j-k) beyond 0
            cand_k, cand_v = j, best[i - 1][j]
            for k in range(max(0, j - 12), j):
                v = best[i - 1][k] - JUMP_PENALTY * (j - k)
                if v > cand_v:
                    cand_k, cand_v = k, v
            best[i][j] = cand_v + S[i][j]
            back[i][j] = cand_k
    j = max(range(n), key=lambda x: best[m - 1][x])
    path = [0] * m
    for i in range(m - 1, -1, -1):
        path[i] = j
        j = back[i][j]
    return path


def align(lec: Path):
    segs = load_segments(str(lec / "transcript.json"))
    if not segs:
        sys.exit("ERROR: empty transcript")
    pages = json.loads((lec / "slides_dedup.json").read_text(encoding="utf-8"))
    path = monotone_path(score_matrix(segs, pages))

    first = {}
    for i, j in enumerate(path):
        first.setdefault(j, i)
    end_all = float(segs[-1].get("end", segs[-1].get("start", 0)))
    # boundaries for pages that received segments, in page order
    owned = sorted(first)
    bounds = {}
    for idx, j in enumerate(owned):
        start = float(segs[first[j]].get("start", 0))
        nxt = float(segs[first[owned[idx + 1]]].get("start", 0)) if idx + 1 < len(owned) else end_all
        bounds[j] = (start, nxt)
    prev_end = 0.0
    spans = []
    for j in range(len(pages)):
        if j in bounds:
            s, e = bounds[j]
            prev_end = e
        else:
            s = e = prev_end
        spans.append((s, e))

    for fname in ("slides_raw.json", "slides_dedup.json"):
        p = lec / fname
        data = json.loads(p.read_text(encoding="utf-8"))
        for s, (a, b) in zip(data, spans):
            s["timestamp_start"], s["timestamp_end"] = int(a), int(b)
            s["time_str"] = f"{fmt_hms(int(a))}-{fmt_hms(int(b))}"
            s["no_audio"] = False
        atomic_write_json(str(p), data)
    atomic_write_json(str(lec / "pdf_align.json"), [
        {"page": i + 1, "start": a, "end": b, "segments": sum(1 for x in path if x == i)}
        for i, (a, b) in enumerate(spans)])
    covered = sum(1 for a, b in spans if b > a)
    print(f"aligned {len(pages)} pages to {len(segs)} segments; "
          f"{covered} pages carry speech, {len(pages) - covered} skipped/silent")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("pdf")
    r.add_argument("lecture_dir")
    a_ = sub.add_parser("align")
    a_.add_argument("lecture_dir")
    a = ap.parse_args()
    if a.cmd == "render":
        render(Path(a.pdf), Path(a.lecture_dir))
    else:
        align(Path(a.lecture_dir))


if __name__ == "__main__":
    main()
