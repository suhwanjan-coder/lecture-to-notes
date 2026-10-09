"""One-command runner for the local default flow (docs/MY_PIPELINE.md).

Three modes, each resumable (a stage whose output already exists is skipped
unless --force):

    # 1. prepare: media -> everything Stage F needs, then stop
    python run_lecture.py <video|audio|youtube-url> --out <lecture_dir> --lang zh
        [--no-groq] [--no-vlm] [--vlm-model minicpm-v:8b] [--whisper-model medium]

    # (Claude Stage F pass 1 writes note_draft.md — reference/stage-f-prompts.md)

    # 2. render: note_draft.md -> <final>.md with figures, audited
    python run_lecture.py <lecture_dir> --render

    # (Claude passes 2-4: verify, Resource, L3 segments)

    # 3. finish: final note -> vault + single-file HTML
    python run_lecture.py <lecture_dir> --finish [--viewer]

The web viewer (video + transcript + L3 segments) is OPTIONAL — only on request:
`--finish --viewer` after Stage F pass 4 has written seg_plan.json + L3/. It
encodes H.264, which every browser plays (H.265 needs hardware/OS support).

Lecture identity (speaker / topic / date) lives in <lecture_dir>/lecture.json.
`prepare` creates it with blanks; the Stage F commander fills it once the
speaker is confirmed from the chair's introduction (never from frame 1).
--render and --finish refuse while it is incomplete. Its optional "layout" picks
the note layout before --tier: "all-slides" (slide-deck talks: every slide goes
into 總整理, no importance filter) or "tiered" (default; screen-share demos).

Every child process is logged as UTF-8 to <lecture_dir>/logs/run_lecture.log
(PowerShell Tee-Object writes UTF-16, which made earlier logs unreadable).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import load_config, parse_tier  # noqa: E402

PY = sys.executable
URL_RE = re.compile(r"^https?://", re.I)


# ----------------------------------------------------------------- plumbing --

class Run:
    def __init__(self, lec: Path, force: bool):
        self.lec, self.force = lec, force
        (lec / "logs").mkdir(parents=True, exist_ok=True)
        self.log = lec / "logs" / "run_lecture.log"

    def say(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def sh(self, name, args, ok_codes=(0,)):
        """Run a pipeline script with UTF-8 IO; stream + log its output."""
        self.say(f"--- {name}: {' '.join(str(a) for a in args)}")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        t0 = time.time()
        p = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env,
                             encoding="utf-8", errors="replace")
        out = []
        with open(self.log, "a", encoding="utf-8") as fh:
            for line in p.stdout:
                sys.stdout.write(line)
                fh.write(line)
                out.append(line)
        p.wait()
        self.say(f"--- {name}: exit={p.returncode} ({time.time() - t0:.0f}s)")
        if p.returncode not in ok_codes:
            raise SystemExit(f"STOP: {name} failed (exit {p.returncode}); see {self.log}")
        return "".join(out)

    def done(self, *rel):
        """True when every output exists and --force is off."""
        return not self.force and all((self.lec / r).exists() for r in rel)


def script(name):
    return HERE / name


def lecture_json(lec: Path):
    p = lec / "lecture.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_lecture_json(lec: Path, data):
    (lec / "lecture.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def require_identity(lec: Path):
    info = lecture_json(lec)
    missing = [k for k in ("speaker", "topic", "date") if not info.get(k)]
    if missing:
        raise SystemExit(
            f"STOP: lecture.json is missing {missing}. Confirm the speaker from the "
            "chair's introduction / main title slide, then fill speaker, topic "
            "(short, filename-safe) and date (YYYYMMDD) in "
            f"{lec / 'lecture.json'}")
    info.setdefault("slug", f"{info['date']}_{info['speaker']}_{info['topic']}")
    info.setdefault("note_name", f"{info['date']}_{info['speaker']}_{info['topic']}.md")
    info.setdefault("prefix", f"{info['speaker']}_{info['topic']}")
    return info


def vault_root(cfg):
    p = ((cfg.get("paths") or {}).get("vault_root") or "").strip() \
        or os.environ.get("CLAUDE_VAULT_ROOT", "")
    return Path(p) if p else None


# ------------------------------------------------------------------ prepare --

def prepare(a):
    lec = Path(a.out).resolve()
    lec.mkdir(parents=True, exist_ok=True)
    r = Run(lec, a.force)
    info = lecture_json(lec)

    # 0. media
    media = info.get("media")
    if not media or a.force:
        fargs = [PY, script("fetch_media.py"), a.input, "--out", lec]
        if a.section:
            fargs += ["--section", a.section]
        out = r.sh("fetch_media", fargs)
        last = [l for l in out.strip().splitlines() if l.startswith("{")]
        if not last:
            raise SystemExit("STOP: fetch_media printed no result line")
        res = json.loads(last[-1])
        if not res.get("media"):
            raise SystemExit("STOP: no single media file — pick one and pass it directly")
        media = res["media"]
        info.update({"media": media, "subs": res.get("subs"), "meta": res.get("meta"),
                     "source": a.input, "lang": a.lang,
                     "speaker": info.get("speaker", ""), "topic": info.get("topic", ""),
                     "date": info.get("date", ""), "layout": info.get("layout", "")})
        save_lecture_json(lec, info)
    r.say(f"media: {media}")

    # 1. primary transcript — local CPU, data never leaves the machine
    if not r.done("transcript.json"):
        r.sh("transcribe (CPU, primary)",
             [PY, script("transcribe_video.py"), media, "--output-dir", lec,
              "--lang", a.lang, "--device", "cpu", "--model", a.whisper_model])

    # 2. external audit — Groq second opinion, never replaces the primary
    if a.no_groq:
        r.say("asr_audit: skipped (--no-groq) — audio not uploaded")
    elif not r.done("asr_audit.md"):
        # asr_audit reads GROQ_API_KEY from the process or the Windows user env
        # itself; a failed audit is a missing second opinion, not a failed lecture
        r.sh("asr_audit (Groq)", [PY, script("asr_audit.py"), lec, "--media", media,
                                  "--lang", a.lang], ok_codes=(0, 1))
        if not (lec / "asr_audit.md").exists():
            r.say("asr_audit: WARNING — no audit produced; the writer works from the "
                  "primary transcript alone")

    # 3. slides: Stage A-E
    if not r.done("slides_raw.json"):
        r.sh("extract_slides", [PY, script("extract_slides.py"), media, "--output-dir", lec])
        r.sh("quick_ocr", [PY, script("quick_ocr.py"), lec])
    if not r.done("slides_dedup.json"):
        r.sh("dedup", [PY, script("dedup_semantic.py"), lec])
    if not r.done("slides_ocr.json"):
        r.sh("ocr_surya (falls back to RapidOCR)", [PY, script("ocr_surya.py"), lec, "--resume"])
    if a.no_vlm:
        r.say("vlm: skipped (--no-vlm) — tiers will fall back to conservative Tier 3")
    elif not r.done("slides_vlm.json"):
        r.sh("vlm_signals", [PY, script("vlm_signals.py"), lec, "--model", a.vlm_model,
                             "--inference-timeout", "600", "--resume"])
    if not r.done("slides_grounded.json"):
        r.sh("ground_slides", [PY, script("ground_slides.py"), lec])
    if not r.done("asr_suspects.txt"):
        r.sh("flag_asr_suspects", [PY, script("flag_asr_suspects.py"), "--dir", lec])

    r.say("READY FOR STAGE F")
    r.say("  1. Confirm the speaker (chair's introduction + main title slide), fill "
          f"speaker/topic/date in {lec / 'lecture.json'}")
    r.say(f"  2. python {script('run_lecture.py')} {lec} --tier")
    r.say("  3. Stage F pass 1 (Write) — reference/stage-f-prompts.md")
    r.say(f"  4. python {script('run_lecture.py')} {lec} --render")


# --------------------------------------------------------------------- tier --

def tier(a):
    lec = Path(a.input).resolve()
    r = Run(lec, True)
    info = require_identity(lec)
    r.sh("tier_pass", [PY, script("tier_pass.py"), lec, "--prefix", info["prefix"]])
    if info.get("layout") == "all-slides":
        open_all_slides(lec, r)


def open_all_slides(lec: Path, r):
    """layout "all-slides" (lecture.json): no importance filter — every frame that
    exists and is not suppressed becomes embeddable (tier <= 2); the writer places
    each slide in 總整理 and decides what is minor. The scored tier is kept in
    `tier_scored` so the regression fixtures and later reviews can still see it."""
    p = lec / "slides_final.json"
    slides = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for s in slides:
        s["tier_scored"] = s["tier"]
        if not s.get("missing_frame") and not s.get("embed_suppressed_reason"):
            if parse_tier(s.get("tier")) > 2:
                s["tier"] = 2
            n += 1
    p.write_text(json.dumps(slides, ensure_ascii=False, indent=1), encoding="utf-8")
    r.say(f"layout all-slides: {n}/{len(slides)} slides embeddable (scored tier kept in tier_scored)")


# ------------------------------------------------------------------- render --

def render(a):
    lec = Path(a.input).resolve()
    r = Run(lec, True)
    info = require_identity(lec)
    if not (lec / "note_draft.md").exists():
        raise SystemExit("STOP: note_draft.md not found — run Stage F pass 1 first")
    r.sh("render_embeds", [PY, script("render_embeds.py"), lec, "--note", "note_draft.md",
                           "--slug", info["slug"], "--no-tier-tag"])
    final = lec / info["note_name"]
    if final.exists() and not a.force:
        raise SystemExit(f"STOP: {final.name} exists (it may carry review fixes). "
                         "Pass --force to regenerate it from note_draft.md")
    shutil.copy2(lec / "note_draft.rendered.md", final)

    # cited frames -> figures/ (web viewer, basename embeds) and a local mirror of
    # the vault attachment path, so audit_note / note_to_html resolve the note's
    # `99-Attachment/lecture_<slug>/…` refs before anything is copied into the vault
    copy_cited_frames(lec, info, r)
    r.sh("audit", [PY, script("audit_note.py"), final, "--mode", "lecture",
                   "--grounding", lec, "--vault", lec], ok_codes=(0, 1))
    r.say(f"FINAL NOTE: {final}")
    r.say("next: Stage F pass 2 (Verify) on this file, apply fixes, then: "
          f"python {script('run_lecture.py')} {lec} --finish")


def attach_rel(info):
    pat = ((load_config().get("paths") or {}).get("vault_attach_pattern") or "").strip()         or "99Attachment/lecture_{slug}"
    return pat.replace("{slug}", info["slug"])


def copy_cited_frames(lec: Path, info, r):
    dests = [lec / "figures", lec / Path(attach_rel(info))]
    for d in dests:
        d.mkdir(parents=True, exist_ok=True)
    n = 0
    for s in json.loads((lec / "slides_final.json").read_text(encoding="utf-8")):
        if parse_tier(s.get("tier")) <= 2 and not s.get("embed_suppressed_reason"):
            for d in dests:
                shutil.copy2(lec / "slides" / s["filename"], d / s["attachment_name"])
            n += 1
    r.say(f"copied {n} cited frames -> {', '.join(str(d) for d in dests)}")


# ------------------------------------------------------------------- finish --

def finish(a):
    lec = Path(a.input).resolve()
    r = Run(lec, True)
    info = require_identity(lec)
    cfg = load_config()
    final = lec / info["note_name"]
    if not final.exists():
        raise SystemExit(f"STOP: {final} not found — run --render first")

    copy_cited_frames(lec, info, r)
    r.sh("audit (final)", [PY, script("audit_note.py"), final, "--mode", "lecture",
                           "--grounding", lec, "--vault", lec], ok_codes=(0, 1))

    vault = vault_root(cfg)
    if a.no_vault:
        r.say("vault: skipped (--no-vault)")
    elif not vault or not vault.is_dir():
        r.say("vault: SKIPPED — set paths.vault_root in config.yaml or CLAUDE_VAULT_ROOT")
        vault = None
    else:
        args = [PY, script("finalize_to_vault.py"), lec, "--vault-root", vault,
                "--note", final.name, "--note-name", info["note_name"], "--slug", info["slug"]]
        if a.force:
            args.append("--force")
        r.sh("finalize_to_vault", args)

    html_args = [PY, script("note_to_html.py"), final, "--out", final.with_suffix(".html"),
                 "--attach-base", lec]
    if vault:
        html_args += ["--vault", vault]
    r.sh("note_to_html", html_args)

    if a.viewer:
        plan = lec / "seg_plan.json"
        l3_dir = lec / "L3"
        if not (plan.exists() and l3_dir.is_dir() and any(l3_dir.glob("L3_seg*.md"))):
            raise SystemExit("STOP: --viewer needs seg_plan.json + L3/ (Stage F pass 4)")
        r.sh("export_web", [PY, script("export_web.py"), lec,
                            "--name", f"{info['speaker']}｜{info.get('title') or info['topic']}",
                            "--date", info["date"], "--codec", "h264"])
    r.say("FINISHED")


# --------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="media path / URL (prepare) or lecture dir (other modes)")
    ap.add_argument("--out", help="lecture dir to create (prepare mode)")
    ap.add_argument("--lang", default="zh", choices=["zh", "en", "bilingual"])
    ap.add_argument("--whisper-model", default="medium")
    ap.add_argument("--section", default=None,
                    help="URL only: download just START-END (e.g. 600-720) — for tests")
    ap.add_argument("--vlm-model", default="minicpm-v:8b")
    ap.add_argument("--no-groq", action="store_true",
                    help="skip the Groq audit (required for patient data / internal meetings)")
    ap.add_argument("--no-vlm", action="store_true")
    ap.add_argument("--no-vault", action="store_true")
    ap.add_argument("--viewer", action="store_true",
                    help="finish: also export the web viewer (optional; needs Stage F pass 4)")
    ap.add_argument("--force", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--tier", action="store_true", help="run the deterministic Tier-pass")
    mode.add_argument("--render", action="store_true")
    mode.add_argument("--finish", action="store_true")
    a = ap.parse_args()

    if a.tier:
        tier(a)
    elif a.render:
        render(a)
    elif a.finish:
        finish(a)
    else:
        if not a.out:
            ap.error("prepare mode needs --out <lecture_dir>")
        if not URL_RE.match(a.input) and not Path(a.input).exists():
            ap.error(f"not found: {a.input}")
        prepare(a)


if __name__ == "__main__":
    main()
