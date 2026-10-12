import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image


TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
TSV = TSV_HEADER + (
    "1\t1\t0\t0\t0\t0\t0\t0\t80\t40\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t4\t6\t20\t8\t96.25\tROOM\n"
    "5\t1\t1\t1\t1\t2\t30\t6\t15\t8\t42\t9000\n"
)


class RasterPlanImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "plan.png"
        Image.new("RGB", (80, 40), "white").save(self.source)
        self.tsv = self.root / "labels.tsv"
        self.tsv.write_bytes(TSV.encode("utf-8"))
        self.path = self.root / "draft.json"

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.raster_plan_import"),
                             "Bounded raster/OCR source draft is missing")
        from layout_configurator import raster_plan_import
        return raster_plan_import

    def test_image_source_uses_exact_file_and_framed_decoded_pixel_identity_without_mm(self):
        api = self.api()
        draft = api.import_raster_plan(self.source)
        raw = draft.to_dict()
        self.assertEqual(raw["state"], "DRAFT")
        self.assertEqual(raw["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(raw["source"]["format"], "PNG")
        self.assertEqual(raw["image"]["raw_width_px"], 80)
        self.assertEqual(raw["image"]["width_px"], 80)
        self.assertEqual(raw["image"]["height_px"], 40)
        self.assertEqual(raw["image"]["exif_orientation"], 1)
        self.assertEqual(raw["image"]["decoded_sha256"], hashlib.sha256(
            b"FLC-raster-RGB-v1\x0080x40\x00" + b"\xff\xff\xff" * (80 * 40)).hexdigest())
        self.assertEqual(raw["coordinates"], {"units": "px", "origin": "top-left", "x": "right", "y": "down", "orientation": "upright-after-exif"})
        self.assertEqual(raw["ocr"]["status"], "UNKNOWN")
        self.assertEqual(raw["ocr"]["words"], [])
        self.assertNotIn("rooms", raw)
        self.assertNotIn("calibration", raw)
        self.assertTrue(any("no scale" in value.lower() for value in raw["assumptions"]))

    def test_exif_rotation_is_explicit_and_ocr_coordinates_use_upright_pixels(self):
        api = self.api()
        source = self.root / "rotated.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (40, 80), "white").save(source, exif=exif)
        raw = api.import_raster_plan(source, ocr_tsv=self.tsv).to_dict()
        self.assertEqual(raw["source"]["format"], "JPEG")
        self.assertEqual((raw["image"]["raw_width_px"], raw["image"]["raw_height_px"]), (40, 80))
        self.assertEqual((raw["image"]["width_px"], raw["image"]["height_px"]), (80, 40))
        self.assertEqual(raw["image"]["exif_orientation"], 6)
        self.assertEqual(raw["ocr"]["words"][0]["pixel_box"], {"left": 4, "top": 6, "width": 20, "height": 8})

    def test_hashed_external_tsv_preserves_words_and_confidence_as_unverified(self):
        api = self.api()
        raw = api.import_raster_plan(self.source, ocr_tsv=self.tsv).to_dict()
        ocr = raw["ocr"]
        self.assertEqual(ocr["method"], "external_tsv")
        self.assertEqual(ocr["status"], "UNVERIFIED")
        self.assertEqual(ocr["tsv_sha256"], hashlib.sha256(self.tsv.read_bytes()).hexdigest())
        self.assertEqual(ocr["tsv_utf8"], TSV)
        self.assertEqual(ocr["source_path"], str(self.tsv.resolve()))
        self.assertEqual([word["text"] for word in ocr["words"]], ["ROOM", "9000"])
        self.assertEqual([word["confidence"] for word in ocr["words"]], [96.25, 42.0])
        self.assertEqual([word["id"] for word in ocr["words"]], ["raster:word:3", "raster:word:4"])
        self.assertEqual(ocr["words"][0]["source_row"], 3)
        self.assertEqual(ocr["words"][0]["page_num"], 1)
        self.assertNotIn("scale", raw)

    def test_missing_local_engine_is_an_explicit_unknown_draft(self):
        api = self.api()
        raw = api.import_raster_plan(self.source, ocr_tesseract=True,
              tesseract_path=self.root / "missing-engine.exe").to_dict()
        self.assertEqual(raw["ocr"]["method"], "tesseract")
        self.assertEqual(raw["ocr"]["status"], "UNKNOWN")
        self.assertEqual(raw["ocr"]["words"], [])
        self.assertIsNone(raw["ocr"]["tsv_sha256"])
        self.assertTrue(any("ocr_unavailable" in value for value in raw["issues"]))

    def test_roundtrip_rechecks_original_image_and_external_ocr_bytes(self):
        api = self.api()
        draft = api.import_raster_plan(self.source, ocr_tsv=self.tsv)
        api.write_raster_plan_draft(self.path, draft)
        self.assertEqual(api.read_raster_plan_draft(self.path).to_dict(), draft.to_dict())
        self.tsv.write_bytes(self.tsv.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "OCR source.*changed"):
            api.read_raster_plan_draft(self.path)
        self.tsv.write_bytes(TSV.encode("utf-8"))
        Image.new("RGB", (80, 40), "black").save(self.source)
        with self.assertRaisesRegex(ValueError, "image source.*changed"):
            api.read_raster_plan_draft(self.path)

    def test_tampered_words_dimensions_identity_and_unknown_fields_are_rejected(self):
        api = self.api()
        original = api.import_raster_plan(self.source, ocr_tsv=self.tsv).to_dict()
        for label, mutate in (
            ("text", lambda raw: raw["ocr"]["words"][0].update(text="EXIT")),
            ("box", lambda raw: raw["ocr"]["words"][0]["pixel_box"].update(left=5)),
            ("duplicate", lambda raw: raw["ocr"]["words"][1].update(id="raster:word:3")),
            ("confidence", lambda raw: raw["ocr"]["words"][0].update(confidence=100)),
            ("width", lambda raw: raw["image"].update(width_px=81)),
            ("orientation", lambda raw: raw["image"].update(exif_orientation=True)),
            ("decoded", lambda raw: raw["image"].update(decoded_sha256="0" * 64)),
            ("scale", lambda raw: raw.update(calibration={"unit_scale_mm": 10})),
            ("approved", lambda raw: raw.update(approved=True)),
            ("ocr hash", lambda raw: raw["ocr"].update(tsv_sha256="0" * 64)),
        ):
            with self.subTest(label=label):
                altered = copy.deepcopy(original)
                mutate(altered)
                self.path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaises(ValueError):
                    api.read_raster_plan_draft(self.path)

    def test_recomputed_ocr_hash_does_not_replace_the_original_external_evidence(self):
        api = self.api()
        raw = api.import_raster_plan(self.source, ocr_tsv=self.tsv).to_dict()
        raw["ocr"]["tsv_utf8"] = raw["ocr"]["tsv_utf8"].replace("ROOM", "EXIT")
        raw["ocr"]["tsv_sha256"] = hashlib.sha256(raw["ocr"]["tsv_utf8"].encode()).hexdigest()
        raw["ocr"]["words"][0]["text"] = "EXIT"
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "OCR source.*changed"):
            api.read_raster_plan_draft(self.path)

    def test_tsv_bounds_types_confidence_and_row_limits_are_not_coerced(self):
        api = self.api()
        for bad in (
            TSV.replace("\t4\t6\t20\t8\t96.25", "\t-1\t6\t20\t8\t96.25"),
            TSV.replace("\t4\t6\t20\t8\t96.25", "\t70\t6\t20\t8\t96.25"),
            TSV.replace("\t96.25\tROOM", "\tNaN\tROOM"),
            TSV.replace("\t96.25\tROOM", "\t100.01\tROOM"),
            TSV.replace("\t96.25\tROOM", "\t-1\tROOM"),
            TSV.replace("\t4\t6\t20\t8\t96.25", "\ttrue\t6\t20\t8\t96.25"),
            TSV.replace("level\tpage_num", "level\tpage"),
            TSV.replace("5\t1\t1\t1\t1\t1", "5\t2\t1\t1\t1\t1"),
            TSV_HEADER + "".join(f"5\t1\t1\t1\t1\t{index}\t4\t6\t20\t8\t50\tROOM\n" for index in range(1, 5002)),
        ):
            with self.subTest(bad=bad[:100]):
                self.tsv.write_text(bad, encoding="utf-8")
                with self.assertRaises(ValueError):
                    api.import_raster_plan(self.source, ocr_tsv=self.tsv)

    def test_unsupported_malformed_multiframe_and_oversized_images_fail_without_decode(self):
        api = self.api()
        oversized = self.root / "oversized.png"
        Image.new("L", (8193, 1)).save(oversized)
        animated = self.root / "animated.png"
        Image.new("RGB", (2, 2), "white").save(animated, save_all=True,
            append_images=[Image.new("RGB", (2, 2), "black")])
        wrong = self.root / "wrong.png"
        Image.new("RGB", (2, 2)).save(wrong, format="GIF")
        corrupt = self.root / "corrupt.jpg"
        corrupt.write_bytes(b"not an image")
        invalid_exif = self.root / "bad-orientation.jpg"
        exif = Image.Exif()
        exif[274] = 9
        Image.new("RGB", (2, 2)).save(invalid_exif, exif=exif)
        for path in (oversized, animated, wrong, corrupt, invalid_exif):
            with self.subTest(path=path.name):
                with self.assertRaises(ValueError):
                    api.import_raster_plan(path)

    def test_conflicting_ocr_modes_and_invalid_process_parameters_fail_before_execution(self):
        api = self.api()
        for options in ({"ocr_tsv": self.tsv, "ocr_tesseract": True}, {"ocr_tesseract": "yes"},
                        {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": 61},
                        {"timeout_seconds": float("nan")}, {"language": "eng; echo bad"},
                        {"language": "--version"}, {"tesseract_path": self.root / "engine.exe"}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    api.import_raster_plan(self.source, **options)

    def test_named_review_records_only_explicit_word_ids_and_source_identity(self):
        api = self.api()
        api.write_raster_plan_draft(self.path, api.import_raster_plan(self.source, ocr_tsv=self.tsv))
        evidence_path = self.root / "review.json"
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        api.record_raster_review(self.path, evidence_path, selected_word_ids=["raster:word:3"],
                                  reviewer="Reviewer A", source_sha256=source_hash)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["format"], "FLC-raster-review-evidence")
        self.assertEqual(evidence["reviewer"], "Reviewer A")
        self.assertEqual(evidence["selected_word_ids"], ["raster:word:3"])
        self.assertEqual(evidence["source_sha256"], source_hash)
        self.assertEqual(evidence["draft_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIn("not engineering", evidence["scope"])
        self.assertNotIn("room_mapping", evidence)
        self.assertNotIn("approved", evidence)
        self.assertEqual(json.loads(self.path.read_text())["state"], "DRAFT")

    def test_review_rejects_missing_reviewer_unknown_duplicate_or_untyped_selection_and_hash(self):
        api = self.api()
        api.write_raster_plan_draft(self.path, api.import_raster_plan(self.source, ocr_tsv=self.tsv))
        base = {"selected_word_ids": ["raster:word:3"], "reviewer": "A",
                "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}
        for patch in ({"reviewer": True}, {"reviewer": " "}, {"selected_word_ids": []},
                      {"selected_word_ids": "raster:word:3"}, {"selected_word_ids": [True]},
                      {"selected_word_ids": ["raster:word:99"]},
                      {"selected_word_ids": ["raster:word:3", "raster:word:3"]},
                      {"source_sha256": "0" * 64}):
            with self.subTest(patch=patch):
                evidence_path = self.root / "rejected.json"
                with self.assertRaises(ValueError):
                    api.record_raster_review(self.path, evidence_path, **{**base, **patch})
                self.assertFalse(evidence_path.exists())

    def test_draft_json_duplicate_keys_and_nonfinite_values_are_rejected(self):
        api = self.api()
        draft = api.import_raster_plan(self.source, ocr_tsv=self.tsv).to_dict()
        for encoded in (json.dumps(draft).replace('"state": "DRAFT"', '"state": "DRAFT", "state": "DRAFT"'),
                        json.dumps(draft).replace('96.25', 'NaN')):
            self.path.write_text(encoded, encoding="utf-8")
            with self.assertRaises(ValueError):
                api.read_raster_plan_draft(self.path)

    def test_duplicate_ocr_hierarchy_cannot_claim_two_words_with_one_source_identity(self):
        api = self.api()
        self.tsv.write_text(TSV.replace("5\t1\t1\t1\t1\t2", "5\t1\t1\t1\t1\t1"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate.*hierarchy"):
            api.import_raster_plan(self.source, ocr_tsv=self.tsv)

    def test_byte_pixel_and_nonword_row_limits_reject_before_draft_creation(self):
        api = self.api()
        oversized_bytes = self.root / "large-file.png"
        with oversized_bytes.open("wb") as stream:
            stream.seek(20 * 1024 * 1024)
            stream.write(b"x")
        oversized_pixels = self.root / "large-pixels.png"
        Image.new("L", (4096, 4096)).save(oversized_pixels)
        for source in (oversized_bytes, oversized_pixels):
            with self.subTest(source=source.name):
                with self.assertRaisesRegex(ValueError, "limit"):
                    api.import_raster_plan(source)
        for data in (b"x" * (2 * 1024 * 1024 + 1),
                     (TSV_HEADER + "1\t1\t0\t0\t0\t0\t0\t0\t80\t40\t-1\t\n" * 10001).encode()):
            with self.subTest(size=len(data)):
                self.tsv.write_bytes(data)
                with self.assertRaises(ValueError):
                    api.import_raster_plan(self.source, ocr_tsv=self.tsv)

    def test_output_never_overwrites_an_input_image_ocr_source_or_reviewed_draft(self):
        api = self.api()
        draft = api.import_raster_plan(self.source, ocr_tsv=self.tsv)
        originals = {self.source: self.source.read_bytes(), self.tsv: self.tsv.read_bytes()}
        for path in originals:
            with self.subTest(path=path.name):
                with self.assertRaises(ValueError):
                    api.write_raster_plan_draft(path, draft)
                self.assertEqual(path.read_bytes(), originals[path])
        api.write_raster_plan_draft(self.path, draft)
        for path in (*originals, self.path):
            with self.subTest(path=path.name):
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    api.record_raster_review(self.path, path, selected_word_ids=["raster:word:3"],
                        reviewer="A", source_sha256=draft.source["sha256"])
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
