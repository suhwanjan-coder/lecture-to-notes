"""Synthetic crop lifecycle, preview, pipeline ordering and HTML checks."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import crop_slide_region as crop
import note_to_html as html
import run_lecture


class CropSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.lec = self.root / "講座" / "_out"
        self.folder = self.lec / "附件"
        self.folder.mkdir(parents=True)
        (self.lec / "slides").mkdir()
        self.rules = [{"slides": "1-2", "box": [40, 50, 600, 340]},
                      {"slides": "5-9", "box": [100, 80, 400, 300]}]
        self.set_rules(self.rules)
        rng = np.random.default_rng(7)
        self.rows = []
        # Neither raw frame numbers nor tier-renumbered suffixes equal note IDs.
        for sid, suffix in zip((1, 2, 5, 6, 7, 8, 9, 10), (5, 1, 2, 3, 4, 6, 7, 8)):
            name = f"講座_s{suffix:02d}.jpg"
            frame = f"frame_{sid * 11:04d}.jpg"
            data = crop.jpeg_bytes(rng.integers(0, 256, (450, 800, 3), dtype=np.uint8))
            (self.folder / name).write_bytes(data)
            (self.lec / "slides" / frame).write_bytes(data)
            self.rows.append({"slide_id": sid, "filename": frame, "attachment_name": name})
        self.write_json("slides_final.json", self.rows)
        self.original = self.contents(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, name, data):
        (self.lec / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def set_rules(self, rules):
        self.write_json("lecture.json", {"slide_crop": rules})

    def contents(self, folder):
        return {p.name: p.read_bytes() for p in crop.frame_files(folder)}

    def cli(self, *args, expected=0):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = crop.main([str(a) for a in args])
        self.assertEqual(code, expected, output.getvalue())
        return output.getvalue()

    def test_two_rules_use_note_ids_and_leave_uncovered_untouched(self):
        self.cli("apply", self.folder, "--lecture", self.lec)
        for row in self.rows:
            name, sid = row["attachment_name"], row["slide_id"]
            if sid == 10:
                self.assertEqual((self.folder / name).read_bytes(), self.original[name])
                self.assertFalse((self.folder / "_orig" / name).exists())
                continue
            x, y, w, h = self.rules[0 if sid <= 2 else 1]["box"]
            original = crop.read_image(self.folder / "_orig" / name)
            self.assertEqual((self.folder / name).read_bytes(),
                             crop.jpeg_bytes(original[y:y + h, x:x + w]))
            self.assertEqual(crop.read_image(self.folder / name).shape[:2], (h, w))

    def test_idempotence_changed_box_and_backup_preservation(self):
        self.cli("apply", self.folder, "--lecture", self.lec)
        first, backups = self.contents(self.folder), self.contents(self.folder / "_orig")
        self.cli("apply", self.folder, "--lecture", self.lec)
        self.assertEqual(first, self.contents(self.folder))
        self.rules[0]["box"] = [0, 0, 800, 450]
        self.set_rules(self.rules)
        self.cli("apply", self.folder, "--lecture", self.lec)
        self.assertEqual(crop.read_image(self.folder / self.rows[0]["attachment_name"]).shape[:2],
                         (450, 800))  # must validate/crop from _orig, not the smaller crop
        self.assertEqual(backups, self.contents(self.folder / "_orig"))
        self.assertTrue(all(data == self.original[name] for name, data in backups.items()))

    def test_removed_rule_restores_uncovered_and_restore_moves_back(self):
        self.cli("apply", self.folder, "--lecture", self.lec)
        self.set_rules(self.rules[1:])
        self.cli("apply", self.folder, "--lecture", self.lec)
        for row in self.rows[:2]:
            name = row["attachment_name"]
            self.assertEqual((self.folder / name).read_bytes(), self.original[name])
        # Recover even when a destination was removed after a previous crop.
        (self.folder / self.rows[2]["attachment_name"]).unlink()
        self.cli("restore", self.folder)
        self.assertEqual(self.original, self.contents(self.folder))
        self.assertFalse((self.folder / "_orig").exists())
        self.cli("restore", self.folder)

    def test_absent_and_empty_rules_restore_without_creating_backups(self):
        for info in ({}, {"slide_crop": []}):
            self.write_json("lecture.json", info)
            self.cli("apply", self.folder, "--lecture", self.lec)
            self.assertEqual(self.original, self.contents(self.folder))
            self.assertFalse((self.folder / "_orig").exists())
        self.set_rules(self.rules)
        self.cli("apply", self.folder, "--lecture", self.lec)
        self.set_rules([])
        self.cli("apply", self.folder, "--lecture", self.lec)
        self.assertEqual(self.original, self.contents(self.folder))

    def test_invalid_rules_exit_one_and_write_nothing(self):
        invalid = ([700, 50, 200, 300], [0, 0, 199, 300], [-1, 0, 200, 200],
                   [0, 0, 200, 199], [0, 0, 200.5, 200], [0, 0, True, 200])
        for box in invalid:
            with self.subTest(box=box):
                self.set_rules([self.rules[0], {"slides": "5-9", "box": box}])
                output = self.cli("apply", self.folder, "--lecture", self.lec, expected=1)
                self.assertIn("ERROR:", output)
                self.assertEqual(self.original, self.contents(self.folder))
                self.assertFalse((self.folder / "_orig").exists())
                self.assertEqual(list(self.folder.glob("*.crop-tmp")), [])
        self.set_rules([{"slides": "5-1", "box": [0, 0, 200, 200]}])
        result = subprocess.run([sys.executable, str(ROOT / "scripts/crop_slide_region.py"),
                                 "apply", str(self.folder), "--lecture", str(self.lec)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR:", result.stderr)
        self.assertEqual(self.original, self.contents(self.folder))

    def test_invalid_change_does_not_crop_or_restore_anything(self):
        self.cli("apply", self.folder, "--lecture", self.lec)
        before, backups = self.contents(self.folder), self.contents(self.folder / "_orig")
        self.set_rules([{"slides": "5-9", "box": [100, 80, 800, 300]}])
        self.cli("apply", self.folder, "--lecture", self.lec, expected=1)
        self.assertEqual(before, self.contents(self.folder))
        self.assertEqual(backups, self.contents(self.folder / "_orig"))

    def test_later_image_size_is_validated_before_any_writes(self):
        path = self.folder / self.rows[-2]["attachment_name"]
        path.write_bytes(crop.jpeg_bytes(np.zeros((210, 210, 3), np.uint8)))
        before = self.contents(self.folder)
        self.cli("apply", self.folder, "--lecture", self.lec, expected=1)
        self.assertEqual(before, self.contents(self.folder))
        self.assertFalse((self.folder / "_orig").exists())

    def test_sheet_prepare_ids_sampling_and_output_override(self):
        (self.lec / "slides_final.json").unlink()
        self.write_json("slides_grounded.json", self.rows)
        with patch.object(crop, "tile", wraps=crop.tile) as draw:
            self.cli("sheet", self.lec.parent, "--n", 3)
        self.assertEqual([call.args[1].split(" | ")[0] for call in draw.call_args_list],
                         ["s01", "s07", "s10"])
        self.assertTrue(all(call.kwargs["grid"] for call in draw.call_args_list))
        self.assertTrue((self.lec / "crop_sheet.jpg").is_file())
        out = self.root / "自訂.jpg"
        self.cli("sheet", self.lec, "--n", 24, "--out", out)
        self.assertGreater(crop.read_image(out).shape[1], 480)
        self.assertEqual(self.original, self.contents(self.folder))
        self.assertFalse((self.folder / "_orig").exists())

    def test_verify_four_pairs_per_range_and_invalid_unsampled_frame(self):
        with patch.object(crop, "tile", wraps=crop.tile) as draw:
            self.cli("verify", self.lec.parent)
        self.assertEqual(len(draw.call_args_list), 12)  # 2 + 4 samples, before/after pairs
        self.assertEqual(sum("cropped" in call.args[1] for call in draw.call_args_list), 6)
        self.assertTrue((self.lec / "crop_verify.jpg").is_file())
        # s07 is not among the four samples of the five-frame second range.
        row = self.rows[4]
        (self.lec / "slides" / row["filename"]).write_bytes(
            crop.jpeg_bytes(np.zeros((210, 210, 3), np.uint8)))
        out = self.root / "invalid.jpg"
        self.cli("verify", self.lec, "--out", out, expected=1)
        self.assertFalse(out.exists())
        self.assertEqual(self.original, self.contents(self.folder))

    def test_standalone_attachment_suffix_without_final_metadata(self):
        (self.lec / "slides_final.json").unlink()
        self.set_rules([{"slides": "1-1", "box": [10, 10, 200, 200]}])
        self.cli("apply", self.folder, "--lecture", self.lec)
        self.assertEqual(crop.read_image(self.folder / "講座_s01.jpg").shape[:2], (200, 200))
        self.assertEqual((self.folder / "講座_s05.jpg").read_bytes(), self.original["講座_s05.jpg"])


class Recorder:
    def __init__(self):
        self.commands = []

    def say(self, message):
        pass

    def sh(self, name, args, **kwargs):
        self.commands.append((name, args))
        return ""


class HtmlAndPipeline(unittest.TestCase):
    def test_widths_and_missing_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            path = Path(td) / "image.jpg"
            path.write_bytes(crop.jpeg_bytes(np.zeros((20, 30, 3), np.uint8)))
            text, count, missing = html.convert(
                "\n".join(f"![[image.jpg{suffix}]]" for suffix in ("|200", "|400", "|401", "|700", ""))
                + "\n![[missing.jpg]]", [td])
            self.assertEqual(count, 5)
            self.assertEqual(missing, ["missing.jpg"])
            self.assertIn('style="width:200px;', text)
            self.assertIn('style="width:400px;', text)
            self.assertEqual(text.count('style="width:100%;'), 3)
            self.assertEqual(text.count('class="slide-image"'), 5)
            self.assertEqual(text.count('tabindex="0"'), 5)
            self.assertIn("#image-overlay img{display:block;max-width:none", html.CSS)
            self.assertIn("full.src = img.src", html.JS)
            self.assertIn("overlay.addEventListener('click', close)", html.JS)
            self.assertIn("e.key === 'Escape'", html.JS)

    def test_hooks_require_rules_and_run_before_html(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            lec = Path(td)
            vault = lec / "vault"
            vault.mkdir()
            (lec / "slides_final.json").write_text("[]", encoding="utf-8")
            (lec / "note.md").write_text("note", encoding="utf-8")
            cfg = {"crop_slides": False, "paths": {"vault_root": str(vault)}}
            for extra, expected in (({}, 0), ({"slide_crop": []}, 0),
                                    ({"slide_crop": [{"slides": "1-2", "box": [0, 0, 200, 200]}]}, 2)):
                info = dict(speaker="test", topic="test", date="20261017", slug="test",
                            note_name="note.md", **extra)
                (lec / "lecture.json").write_text(json.dumps(info), encoding="utf-8")
                recorder = Recorder()
                args = SimpleNamespace(input=str(lec), no_vault=False, force=False, viewer=False)
                with patch.object(run_lecture, "load_config", return_value=cfg), \
                        patch.object(run_lecture, "Run", return_value=recorder):
                    run_lecture.finish(args)
                names = [name for name, _ in recorder.commands]
                calls = [(i, args) for i, (name, args) in enumerate(recorder.commands)
                         if name.startswith("crop_slides")]
                self.assertEqual(len(calls), expected)
                for i, command in calls:
                    self.assertEqual(command[2], "apply")
                    self.assertEqual(command[-2:], ["--lecture", lec])
                    self.assertLess(i, names.index("note_to_html"))
                if expected:
                    self.assertEqual(calls[0][1][3], lec / "99Attachment/lecture_test")
                    self.assertEqual(calls[1][1][3], vault / "99Attachment/lecture_test")
                    self.assertGreater(calls[1][0], names.index("finalize_to_vault"))


if __name__ == "__main__":
    unittest.main()
