"""Apply human-chosen slide_crop boxes; no automatic box detection.

sheet/verify accept the prepare output directory (containing lecture.json), or
its parent with an _out/lecture.json. Rules use the note's slide_id, not the
frame number or tier-renumbered attachment suffix. Without slides_final.json,
standalone attachments use their _sNN suffix as the slide number.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

import cv2
import numpy as np


FRAME_RE = re.compile(r"_s(\d+)\.jpg$", re.I)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lecture_dir(folder):
    for candidate in (folder, folder / "_out"):
        if (candidate / "lecture.json").is_file():
            return candidate
    raise ValueError(f"lecture.json not found in {folder} or its _out directory")


def load_rules(lec):
    raw = read_json(lec / "lecture.json").get("slide_crop", [])
    if not isinstance(raw, list):
        raise ValueError("slide_crop must be a list of rules (or [])")
    rules = []
    for i, rule in enumerate(raw, 1):
        label = f"slide_crop rule {i}"
        if not isinstance(rule, dict):
            raise ValueError(f"{label}: expected slides and box")
        match = re.fullmatch(r"([1-9]\d*)(?:-([1-9]\d*))?", str(rule.get("slides", "")))
        if not match:
            raise ValueError(f"{label}: slides must be a positive range like '1-100'")
        start, end = int(match[1]), int(match[2] or match[1])
        box = rule.get("box")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) is not int for v in box)):
            raise ValueError(f"{label}: box must be four integers [x, y, w, h]")
        x, y, w, h = box
        if start > end or x < 0 or y < 0 or w < 200 or h < 200:
            raise ValueError(f"{label}: ordered range, x/y >= 0 and w/h >= 200 required")
        if any(start <= b and a <= end for a, b, _ in rules):
            raise ValueError(f"{label}: overlapping slide ranges are ambiguous")
        rules.append((start, end, box))
    return rules


def box_for(sid, rules):
    return next((box for start, end, box in rules if start <= sid <= end), None)


def read_image(path):
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot decode JPEG: {path}")
    return img


def jpeg_bytes(img):
    ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return data.tobytes()


def check_box(img, box, label):
    x, y, w, h = box
    ih, iw = img.shape[:2]
    if x + w > iw or y + h > ih:
        raise ValueError(f"{label}: box {box} outside original image {iw}x{ih}")


def lecture_frames(lec):
    # Preserve the IDs used in note captions, including gaps after deduplication.
    for name in ("slides_final.json", "slides_grounded.json", "slides_raw.json",
                 "slides/timestamps.json"):
        path = lec / name
        if path.exists():
            rows = read_json(path)
            return sorted((int(r.get("slide_id", r.get("slide"))), lec / "slides" / r["filename"])
                          for r in rows if r.get("filename") and not r.get("missing_frame"))
    raise ValueError(f"No slide metadata in {lec}; run prepare first")


def sample(rows, n):
    if len(rows) <= n:
        return rows
    return [rows[round(i * (len(rows) - 1) / (n - 1))] for i in range(n)] if n > 1 else rows[:1]


def validate_frames(rows, rules):
    # Preflight ALL covered frames, including ones not selected for the preview.
    for sid, path in rows:
        box = box_for(sid, rules)
        if box is not None:
            check_box(read_image(path), box, f"s{sid:02d} ({path.name})")


def tile(img, label, grid=False, box=None):
    h, w = img.shape[:2]
    tw, th = 480, max(1, round(h * 480 / w))
    thumb = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
    canvas = np.full((th + 64, tw + 56, 3), 245, dtype=np.uint8)
    left, top = 48, 48
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(canvas, label, (8, 18), font, .48, (20, 20, 20), 1, cv2.LINE_AA)
    if grid:
        for x in range(0, w, 160):
            px = round(x * tw / w)
            cv2.line(thumb, (px, 0), (px, th - 1), (0, 190, 230), 1)
            if x % 320 == 0:
                cv2.putText(canvas, str(x), (left + px, top - 7), font, .36, (20, 20, 20), 1)
        for y in range(0, h, 160):
            py = round(y * th / h)
            cv2.line(thumb, (0, py), (tw - 1, py), (0, 190, 230), 1)
            if y % 320 == 0:
                cv2.putText(canvas, str(y), (3, top + py + 5), font, .36, (20, 20, 20), 1)
    if box is not None:
        x, y, bw, bh = box
        cv2.rectangle(thumb, (round(x * tw / w), round(y * th / h)),
                      (round((x + bw - 1) * tw / w), round((y + bh - 1) * th / h)),
                      (0, 0, 255), 2)
    canvas[top:top + th, left:left + tw] = thumb
    return canvas


def save_sheet(tiles, out, columns):
    if not tiles:
        raise ValueError("No frames to show")
    height = max(t.shape[0] for t in tiles)
    width = tiles[0].shape[1]
    sheet = np.full((((len(tiles) + columns - 1) // columns) * height,
                     columns * width, 3), 245, dtype=np.uint8)
    for i, t in enumerate(tiles):
        y, x = (i // columns) * height, (i % columns) * width
        sheet[y:y + t.shape[0], x:x + width] = t
    data = jpeg_bytes(sheet)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"Wrote {out}")


def sheet(lec, out, n):
    tiles = []
    for sid, path in sample(lecture_frames(lec), n):
        img = read_image(path)
        h, w = img.shape[:2]
        tiles.append(tile(img, f"s{sid:02d} | {w}x{h} | grid: original pixels", grid=True))
    save_sheet(tiles, out, 3)


def verify(lec, out):
    rules = load_rules(lec)
    rows = lecture_frames(lec)
    validate_frames(rows, rules)
    tiles = []
    for i, (start, end, box) in enumerate(rules, 1):
        members = sample([(sid, path) for sid, path in rows if start <= sid <= end], 4)
        if not members:
            raise ValueError(f"slide_crop rule {i}: no frames in range {start}-{end}")
        for sid, path in members:
            img = read_image(path)
            x, y, w, h = box
            label = f"R{i} ({start}-{end}) s{sid:02d}"
            tiles.append(tile(img, f"{label} box={x},{y},{w},{h}", box=box))
            tiles.append(tile(img[y:y + h, x:x + w], f"{label} cropped {w}x{h}"))
    if not rules:
        tiles.append(tile(np.full((270, 480, 3), 245, np.uint8), "No slide_crop rules: no cropping"))
    save_sheet(tiles, out, 2)


def frame_files(folder):
    return sorted(p for p in folder.glob("*") if p.is_file() and FRAME_RE.search(p.name))


def apply(folder, lec):
    rules = load_rules(lec)
    backup = folder / "_orig"
    files = {p.name: p for p in frame_files(folder)}
    files.update({p.name: p for p in frame_files(backup)})
    final = lec / "slides_final.json"
    mapping = ({r["attachment_name"]: int(r["slide_id"]) for r in read_json(final)
                if r.get("attachment_name")} if final.exists() else None)
    plan = []
    for name, src in sorted(files.items()):
        if rules and mapping is not None and name not in mapping:
            raise ValueError(f"No slide_id mapping for {name} in {final}")
        sid = mapping[name] if mapping is not None and name in mapping else int(FRAME_RE.search(name)[1])
        box = box_for(sid, rules)
        if box is not None:
            check_box(read_image(src), box, f"s{sid:02d} ({name})")
        plan.append((src, folder / name, box))
    # No writes (including backups/restores) until every rule and image passes.
    for src, dst, box in plan:
        if box is None:
            if src.parent == backup:
                shutil.copy2(src, dst)
            continue
        x, y, w, h = box
        data = jpeg_bytes(read_image(src)[y:y + h, x:x + w])
        backup.mkdir(exist_ok=True)
        original = backup / dst.name
        temp = dst.with_suffix(".crop-tmp")
        try:
            temp.write_bytes(data)
            if not original.exists():
                src.rename(original)
            temp.replace(dst)
        finally:
            temp.unlink(missing_ok=True)
    print(f"Applied {sum(box is not None for _, _, box in plan)} crops; other slides unchanged/restored")


def restore(folder):
    backup = folder / "_orig"
    originals = frame_files(backup)
    for src in originals:
        src.replace(folder / src.name)
    if backup.is_dir() and not any(backup.iterdir()):
        backup.rmdir()
    print(f"Restored {len(originals)} originals")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="command", required=True)
    for name in ("sheet", "verify", "apply", "restore"):
        command = commands.add_parser(name)
        command.add_argument("folder", type=Path)
        if name in ("sheet", "verify"):
            command.add_argument("--out", type=Path)
        if name == "sheet":
            command.add_argument("--n", type=int, default=24)
        if name == "apply":
            command.add_argument("--lecture", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if not args.folder.is_dir():
            raise ValueError(f"Directory not found: {args.folder}")
        if args.command == "restore":
            restore(args.folder)
        elif args.command == "apply":
            apply(args.folder, lecture_dir(args.lecture))
        else:
            lec = lecture_dir(args.folder)
            out = args.out or args.folder / "_out" / f"crop_{args.command}.jpg"
            if args.command == "sheet":
                if args.n < 1:
                    raise ValueError("--n must be positive")
                sheet(lec, out, args.n)
            else:
                verify(lec, out)
    except (ValueError, OSError, KeyError, TypeError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
