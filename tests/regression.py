"""Regression check: re-run the deterministic stages on frozen real lectures and
compare against the recorded expectation, so a code change cannot silently move
slide scores or tiers.

    python tests/regression.py                    # run every fixture
    python tests/regression.py --update           # accept current output as expected
    python tests/regression.py --make-fixture NAME <lecture_dir>

What is checked per fixture (all 0 LLM calls, seconds per lecture):
  ground_slides.py   per-slide speaker_reference_density / skip / emphasis
  tier_pass.py       per-slide integer tier
  asr_audit.py       its built-in --selftest (once, not per fixture)

Fixtures live in tests/fixtures/<NAME>/ and are GITIGNORED: they hold real
lecture transcripts and OCR text, and this repo is public. Each holds the stage
inputs (slides_vlm.json, transcript.json, metadata.json) plus expected.json.
Exit 0 = all match; 1 = a difference (printed per slide); 2 = setup error.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
INPUTS = ("slides_vlm.json", "transcript.json", "metadata.json")
PY = sys.executable
TOL = 1e-3


def run(args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=cwd)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-2000:], sep="\n")
        raise SystemExit(f"setup error: {Path(args[1]).name} exit {p.returncode}")
    return p.stdout


def snapshot(fx: Path):
    """Run ground + tier on a temp copy; return {slide_id: signals}."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for f in INPUTS:
            if (fx / f).exists():
                shutil.copy2(fx / f, work / f)
        run([PY, SCRIPTS / "ground_slides.py", work])
        run([PY, SCRIPTS / "tier_pass.py", work, "--prefix", "fx"])
        final = json.loads((work / "slides_final.json").read_text(encoding="utf-8"))
    out = {}
    for s in final:
        ts = s.get("transcript_signals")
        if ts is None:  # not grounded (e.g. VLM failed) — tier is all we can pin
            out[str(s["slide_id"])] = {"tier": int(s["tier"])}
            continue
        out[str(s["slide_id"])] = {
            "ref": round(ts["speaker_reference_density"], 4),
            "skip": round(ts["speaker_skip_score"], 4),
            "emph": round(ts["speaker_emphasis_score"], 4),
            "tier": int(s["tier"]),
        }
    return out


def diff(expected, got):
    msgs = []
    for sid in sorted(set(expected) | set(got), key=lambda x: int(x)):
        e, g = expected.get(sid), got.get(sid)
        if e is None or g is None:
            msgs.append(f"  slide {sid}: {'missing' if g is None else 'new'}")
            continue
        for k in ("ref", "skip", "emph"):
            if (k in e) != (k in g):
                msgs.append(f"  slide {sid}: {k} {'gone' if k in e else 'appeared'}")
            elif k in e and abs(e[k] - g[k]) > TOL:
                msgs.append(f"  slide {sid}: {k} {e[k]} -> {g[k]}")
        if e["tier"] != g["tier"]:
            msgs.append(f"  slide {sid}: TIER {e['tier']} -> {g['tier']}")
    return msgs


def make_fixture(name, lec):
    lec = Path(lec)
    fx = FIXTURES / name
    fx.mkdir(parents=True, exist_ok=True)
    for f in INPUTS:
        if not (lec / f).exists() and f != "metadata.json":
            raise SystemExit(f"setup error: {lec / f} not found")
        if (lec / f).exists():
            shutil.copy2(lec / f, fx / f)
    print(f"fixture {name}: inputs copied; recording expectation")
    (fx / "expected.json").write_text(
        json.dumps(snapshot(fx), ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--make-fixture", nargs=2, metavar=("NAME", "LECTURE_DIR"))
    a = ap.parse_args()

    if a.make_fixture:
        make_fixture(*a.make_fixture)
        return 0

    run([PY, SCRIPTS / "asr_audit.py", "--selftest"])
    print("asr_audit selftest: OK")

    fixtures = sorted(p for p in FIXTURES.glob("*") if (p / "expected.json").exists())
    if not fixtures:
        print("no fixtures yet — create one with --make-fixture NAME <lecture_dir>")
        return 2
    failed = 0
    for fx in fixtures:
        got = snapshot(fx)
        if a.update:
            (fx / "expected.json").write_text(
                json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{fx.name}: expectation updated ({len(got)} slides)")
            continue
        expected = json.loads((fx / "expected.json").read_text(encoding="utf-8"))
        msgs = diff(expected, got)
        tiers = [v["tier"] for v in got.values()]
        summary = f"T1={tiers.count(1)} T2={tiers.count(2)} T3={tiers.count(3)}"
        if msgs:
            failed += 1
            print(f"{fx.name}: {len(msgs)} DIFFERENCE(S) ({summary})")
            print("\n".join(msgs[:40]))
        else:
            print(f"{fx.name}: OK ({len(got)} slides, {summary})")
    if failed:
        print(f"\n{failed} fixture(s) changed. If intended, re-run with --update.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
