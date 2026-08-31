import unittest
from pathlib import Path

from layout_configurator.brief import parse_llm_mapping, parse_text_brief
from layout_configurator.norms import load_ruleset, retrieve_rule_citations
from layout_configurator.schema import validate_spec


class BriefTests(unittest.TestCase):
    def test_text_brief_becomes_canonical_schema_valid_layoutir_without_geometry(self):
        spec = parse_text_brief(Path("examples/brief.txt").read_text(encoding="utf-8"))

        self.assertEqual(spec.project_name, "Дом из текстового ТЗ")
        self.assertEqual(spec.entry_room, "hall")
        self.assertEqual(spec.room_by_id["living"].target_area_m2, 24)
        self.assertTrue(spec.room_by_id["kitchen"].needs_daylight)
        self.assertEqual(validate_spec(spec), ())
        self.assertFalse(hasattr(spec, "placements"))

    def test_future_llm_mapping_rejects_generated_coordinates_at_schema_boundary(self):
        canonical = parse_text_brief(Path("examples/brief.txt").read_text(encoding="utf-8")).to_dict()
        canonical["rooms"][0]["x"] = 0

        with self.assertRaises(ValueError):
            parse_llm_mapping(canonical)

    def test_rule_retrieval_returns_citations_without_running_layout_check(self):
        ruleset = load_ruleset("rules/kz_sn_3_02_02_2023_partial.yaml")

        citations = retrieve_rule_citations(ruleset, "естественное освещение кухни")

        self.assertTrue(citations)
        self.assertEqual(citations[0].id, "DAYLIGHT_OPENING")
        self.assertEqual(citations[0].clause, "7.8")
        self.assertIn("source_url", citations[0].to_dict())


if __name__ == "__main__":
    unittest.main()
