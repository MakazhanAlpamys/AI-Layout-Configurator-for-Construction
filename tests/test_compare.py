import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from layout_configurator.cli import main
from layout_configurator.compare import DISCLAIMER, compare_bundle, variant_metrics, write_comparison


class CompareVariantsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls._tmp.name) / "bundle"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "generate-building", "examples/pharma_cleanroom_pilot.yaml",
                "--profile", "rules/pharma_cleanroom_pilot.yaml",
                "--output", str(cls.bundle), "--variants", "3", "--max-attempts", "3",
                "--time-limit", "20", "--seed", "1",
            ])
        assert code == 0

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_metrics_trace_back_to_the_saved_variant(self):
        metrics = variant_metrics(self.bundle / "building_01.json")
        data = json.loads((self.bundle / "building_01.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics.rooms, len(data["layout"]["rooms"]))
        self.assertTrue(metrics.facility_ok)
        self.assertEqual(metrics.failed_checks, ())
        self.assertGreater(metrics.route_length_m, 0)
        self.assertAlmostEqual(sum(metrics.route_length_by_type_m.values()), metrics.route_length_m, places=6)
        self.assertEqual(metrics.door_crossings, sum(len(r["room_path"]) - 1 for r in data["flow_routes"]["routes"]))

    def test_ranking_follows_the_published_key(self):
        ranked = compare_bundle(self.bundle)
        self.assertEqual([m.rank for m in ranked], [1, 2, 3])
        keys = [(len(m.failed_checks), m.open_issues, round(m.route_length_m, 1), round(m.area_deviation_pct, 2)) for m in ranked]
        self.assertEqual(keys, sorted(keys))
        self.assertIn("passes every facility gate", ranked[0].reasons)

    def test_outputs_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = main(["compare-variants", str(self.bundle), "--output", directory])
            self.assertEqual(code, 0)
            self.assertIn("#1 variant", out.getvalue())
            payload = json.loads((Path(directory) / "comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["disclaimer"], DISCLAIMER)
            self.assertEqual(len(payload["variants"]), 3)
            with (Path(directory) / "comparison.csv").open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["rank"] for row in rows], ["1", "2", "3"])
            self.assertTrue((Path(directory) / "comparison.pdf").read_bytes().startswith(b"%PDF"))

    def test_polished_variants_stay_close_to_the_program(self):
        # The hierarchy polishes single-room areas after the first placement.
        for metrics in compare_bundle(self.bundle):
            self.assertLess(metrics.area_deviation_pct, 5.0)

    def test_variants_are_different_options(self):
        placements = [
            json.loads((self.bundle / f"building_0{n}.json").read_text(encoding="utf-8"))["layout"]["rooms"]
            for n in (1, 2, 3)
        ]
        for first, second in ((0, 1), (0, 2), (1, 2)):
            moved = [
                room for room in placements[first]
                if abs(placements[first][room]["x"] - placements[second][room]["x"]) >= 3000
                or abs(placements[first][room]["y"] - placements[second][room]["y"]) >= 3000
            ]
            self.assertGreaterEqual(len(moved), 3, f"variants {first + 1} and {second + 1} barely differ")

    def test_missing_bundle_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                write_comparison(directory)


if __name__ == "__main__":
    unittest.main()
