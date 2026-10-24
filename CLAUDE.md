# lecture-to-notes — local instructions

This fork carries 阿志's local default flow. When asked to process a lecture
(「跑演講筆記」, a video path, a folder or a YouTube URL):

1. Read `docs/MY_PIPELINE.md` (the flow and its defaults) and
   `reference/stage-f-prompts.md` (the four Claude passes and their worker prompts).
2. Run `scripts/run_lecture.py` in its four modes, in order: prepare → `--tier`
   → `--render` → `--finish`. Stage F passes 1 (write) and 2 (verify) run between
   them. Pass 3 (reference lookup) and pass 4 + `--viewer` (web viewer) run ONLY
   when 阿志 asks; by default unconfirmable items are marked, not researched.
3. Defaults: CPU Whisper is the primary transcript; Groq is an external auditor
   only (`asr_audit.md`). Pass `--no-groq` for patient data or internal meetings.
4. Confirm the speaker from the chair's introduction / main title slide before
   filling `lecture.json` — never from the first frame. In the same step set
   `"layout"`: `"all-slides"` for slide decks, `"tiered"` for screen-share demos
   (`reference/stage-f-prompts.md` "Layout").
   Also run `scripts/crop_slide_region.py sheet <lecture_dir>`, inspect it, write `slide_crop` in `lecture.json` using note slide numbers and one box per layout (split ranges to skip full-screen video/other layouts), then run `verify <lecture_dir>` and inspect the crops (see `docs/MY_PIPELINE.md`).
5. Run every script with `.venv\Scripts\python.exe` (system Python lacks OCR).
   After `--finish`, tidy `D:\lectures\<lecture>\`: root keeps what 阿志 opens
   (video, HTML, Word, viewer), everything else goes to `_archive\`.
6. Deliverables are always TWO files (阿志 2026-10-25): the single-file HTML and a
   Word `.docx`. `--finish` makes both (pandoc converts the HTML); if it reports
   `docx: SKIPPED`, fix pandoc and convert before calling the lecture done. When
   阿志 names a destination folder (e.g. a course week's `1_課堂內容\`), put both
   files there under the same name.

`SKILL.md` and `reference/` remain the upstream, tool-agnostic spec; local
changes extend them and never contradict them.

## After changing any scoring / tier / grounding code

Run `python tests/regression.py` (seconds, 0 LLM calls). A difference means
slide scores or tiers moved; if intended, re-run with `--update`. Fixtures in
`tests/fixtures/` hold real lecture text and are gitignored (public repo) — add
a new one with `--make-fixture NAME <lecture_dir>` after each real lecture.
