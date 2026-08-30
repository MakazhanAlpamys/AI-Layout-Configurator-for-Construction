import unittest

from layout_configurator.io import load_spec
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.norms import RuleSet, check_layout, load_ruleset
from layout_configurator.solver import solve_layouts


class NormsTests(unittest.TestCase):
    def test_baseline_ruleset_passes_sample_and_keeps_evidence(self):
        spec = load_spec("examples/basic.yaml")
        result = solve_layouts(spec, variants=1, time_limit_seconds=10)[0]
        report = check_layout(spec, result, load_ruleset("rules/baseline.yaml"))

        self.assertTrue(report.ok)
        self.assertEqual(len(report.results), 6)
        self.assertTrue(all(item.status == "PASS" for item in report.results))
        self.assertIn("room-center proxy", report.results[-1].evidence[0])
        self.assertEqual(report.to_dict()["ok"], True)

    def test_corridor_width_failure_is_reported_with_rule_evidence(self):
        spec = LayoutIR.from_mapping(
            {
                "project_name": "narrow corridor",
                "boundary": {"width": 4000, "height": 3000},
                "entry_room": "corridor",
                "rooms": [
                    {
                        "id": "corridor",
                        "type": "corridor",
                        "target_area": 2,
                        "min_width": 1000,
                        "min_depth": 1000,
                    }
                ],
            }
        )
        result = LayoutResult(variant=1, placements={"corridor": Rect(0, 0, 1000, 2000)})
        report = check_layout(spec, result, load_ruleset("rules/baseline.yaml"))

        corridor_rule = next(item for item in report.results if item.id == "MIN_CORRIDOR_WIDTH")
        self.assertFalse(report.ok)
        self.assertEqual(corridor_rule.status, "FAIL")
        self.assertIn("1000 mm", corridor_rule.evidence[0])
        self.assertIn("1200 mm", corridor_rule.evidence[0])

    def test_ruleset_can_limit_egress_distance(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 6000, "height": 2000},
                "entry_room": "entry",
                "rooms": [
                    {"id": "entry", "target_area": 2, "min_width": 1000, "min_depth": 1000},
                    {"id": "far", "target_area": 2, "min_width": 1000, "min_depth": 1000},
                ],
            }
        )
        result = LayoutResult(
            variant=1,
            placements={
                "entry": Rect(0, 0, 1000, 2000),
                "far": Rect(1000, 0, 1000, 2000),
            },
        )
        ruleset = RuleSet.from_mapping(
            {
                "name": "distance test",
                "version": "1",
                "jurisdiction": "test",
                "rules": [
                    {
                        "id": "MAX_EGRESS_DISTANCE",
                        "title": "Short route",
                        "source": "test",
                        "clause": "test.distance",
                        "params": {"max_travel_distance_mm": 999},
                    }
                ],
            }
        )
        report = check_layout(spec, result, ruleset)

        self.assertFalse(report.ok)
        self.assertEqual(report.results[0].status, "FAIL")
        self.assertIn("entry -> far", report.results[0].evidence[0])


if __name__ == "__main__":
    unittest.main()
