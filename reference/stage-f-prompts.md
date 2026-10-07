# Stage F worker prompts — the local default flow

Validated on the IDSA 20261004 run (2026-10-07): two talks, all four passes,
fresh-verifier findings fixed before delivery. These are fill-in templates for
the commander session; `<…>` slots are filled per lecture. Rules the templates
rely on live in `note-spec.md` — the templates point there instead of copying.

Order and tiers (cheapest sufficient, per the house dispatch rules):

| Pass | Model | Runs | Writes |
|---|---|---|---|
| 1 Write | Opus | always, after `--tier` | `note_draft.md` |
| 2 Verify | Sonnet, fresh context | always, after `--render` | report only |
| 3 Resource | Sonnet | ==only on request== | `# Resource` section only |
| 4 L3 / viewer | Sonnet | ==only on request== (then `--finish --viewer`) | `seg_plan.json`, `L3/*.md` |

Default (阿志 2026-10-07): references are NOT looked up — the writer lists only
sources the lecture named and marks incomplete ones `⚠️ 待補`; anything that
cannot be confirmed is marked, not researched. The web viewer is optional.

The commander applies pass-2 findings itself (they are few and need judgment),
then runs `run_lecture.py <dir> --finish`.

Every prompt starts with the worker clause:

> NO sub-workers, NO waiting/monitors; the deliverable must exist on disk before
> your turn ends, else the task is FAILED. Write in Traditional Chinese (Taiwan).

## Speaker identity — check before pass 1

Conference recordings often open with someone else (a chair's welcome, a
sponsor, the previous speaker's Q&A). Before filling `<speaker>`, read the
transcript around the chair's introduction and Read the main title slide a few
minutes in. ==Never take the speaker from the first frame.== (2026-10-07: the
first frame showed the opening speaker's title slide; the main speaker started
at 07:42.) A recording with two speakers gets both, with time ranges, in the
note header and frontmatter.

## Pass 1 — Write

```
You are the Stage F WRITE-PASS worker of the lecture-to-notes pipeline. <worker clause>

Goal + why: write `<dir>\note_draft.md` for the talk by <speaker, affiliation>,
「<title>」, <date>, <venue>, <duration>, <language>. <If two speakers: time ranges.>
The reader is a practicing physician who will read ONLY `# 總整理` later; it must
be self-contained and clinically actionable.

Read FIRST, fully: `reference/note-spec.md` (Definition of Done, Rules A–G,
structure template, "Write-pass prompt — what it must contain"). Follow it literally.

Inputs in `<dir>`:
- slides_final.json — FROZEN tier authority; do not recompute.
- transcript.txt — primary transcript (local CPU Whisper). <Note script: Simplified → write 繁體.>
- asr_suspects.txt — resolve each from context; unresolved → raw form + ⚠️.
- asr_audit.md — Groq second opinion: windows where the primary dropped speech,
  looped, or disagrees on numbers. For each flagged window, decide from slides +
  context which side is right; never copy Groq text blindly; numbers you cannot
  settle get ⚠️ with both values.
- source/youtube_subs.json if present — a third reference, lowest priority.
- slides\ frames — Read EVERY Tier-1/2 frame before captioning it (C3).
- Tier summary: Tier 1 = <ids>. Tier 2 = <ids>. Text-heavy evidence tables that
  are Tier 3 (<ids>) → reproduce as markdown tables; view the frame when OCR is garbled.

Hard requirements: template structure exactly; `[[EMBED sN: short caption]]`
placeholders only (no paths/widths); Tier 1 once in 總整理 + once in 逐投影片,
Tier 2 逐投影片 only; 🗣️ for speaker-only content; exact numbers, ==highlight==
cut-offs; nothing silently dropped (B4); Resource = only sources named; Taiwan
terminology; never invent names, venues or numbers.

Acceptance: required headings present; every Tier-1/2 id placed as specified,
zero Tier-3 ids; ≥ <8–10> 🗣️; tables have separator rows; payload ≥ 25% of
transcript chars.

Report (≤ 25 lines): file + line count; ids placed and viewed; suspects and audit
windows resolved vs left ⚠️; speech-vs-slide contradictions; close with
`NOTE DONE — 逐投影片 includes ALL Tier-1/2 slides: YES/NO`.
```

## Pass 2 — Verify (fresh context)

```
You are an INDEPENDENT VERIFIER (you did not write the note). <worker clause>
Your job sentence: "try to refute this note".

Goal: fact-check `<final note>` against transcript.txt, asr_audit.md and the
frames, so the commander knows whether it can go to a physician reader.

Do step 1 BEFORE opening the note:
1. Read transcript.txt fully; list the take-home points and every stated number.
2. Then compare: (a) question level of Pearls/總整理; (b) speaker attribution if
   more than one speaker; (c) every Pearls number + ≥20 body numbers →
   CONFIRMED / CORRECTED / NOT FOUND, reading frames for slide-only numbers;
   (d) omissions; (e) fabrication, Resource especially; (f) captions vs pixels
   for all Tier-1 + 3 others; (g) Simplified characters / mainland terms.

Report (≤ 40 lines, path:line or MM:SS for each claim, VERIFIED-by-<method> or
NOT-verified): verdict 可交付 / 需修正後交付 / 不可交付; each section; 2–3 things
done well.
```

## Pass 3 — Resource

```
Task: complete and verify every reference in `# Resource` of `<final note>`. <worker clause>
Rules (note-spec D1–D5): list ONLY sources the lecture named — never add, never
silently drop. Look each up with PubMed tools (ToolSearch "pubmed"),
ClinicalTrials.gov (ToolSearch "clinical trials"), WebSearch/WebFetch for
guidelines and government data; recover partial footnotes from the frames.
Format: `- 作者 et al. 題名. 期刊 年;卷(期):頁. PMID · [DOI](…)` + indented
`- 重點：…；與講者說法：一致／部分一致／不一致（…）`. Verified → remove `⚠️ 待補`;
not locatable → `【未能於原文定位，待確認】` + what was searched. Outside Resource,
only append ` ⚠️ 原文為 X（出處）` after a body number the paper contradicts.
Never state a PMID/DOI not returned by a tool this session. Re-run audit_note.py
until 0 FAIL.

Report (≤ 30 lines): verified / unresolvable / total; one line per entry; body
contradictions flagged; audit line.
```

## Pass 4 — Web viewer segments (L3)

```
Task: prepare the web viewer content for `<dir>`; video `<video path>`. <worker clause>
Do NOT run --export. Read pipeline.md "Source labels are always Vn" and
"Web viewer export — single talk", segmented-mode.md "File template" and
"Gotchas", SKILL.md Step 15. Content source = the verified final note (read-only;
another worker may be editing its Resource section).

1. seg_plan.json: <5–8> content segments, no gaps/overlaps, whole transcript;
   separate segments for other speakers / MC bridges; region names the speaker.
2. build_single_talk_web.py <dir> --plan … --video <video> --name "<講者｜題目>" --date <date>
3. Copy the note's attachments into figures\ (same names).
4. Write every missing L3: clinical synthesis per range, `(V1 MM:SS)` timecodes
   only, basename-only image embeds in col-0 `> [!figure]` callouts; overview =
   5–8 pearls with timecodes + one line per segment. Keep the note's ⚠️ on
   numbers that came only from speech (e.g. "講者口述，投影片未列").
5. audit_note.py <each L3> --mode lecture-seg → 0 FAIL; re-run step 2 without
   --force → no missing L3.

Report (≤ 20 lines): segment table; files + line counts; audit summary.
```
