import json
import tempfile
import unittest
from pathlib import Path

from layout_configurator.io import load_spec
from layout_configurator.schema import load_mapping, validate_mapping, validate_spec


class SchemaTests(unittest.TestCase):
    def test_normalized_sample_matches_layout_ir_schema(self):
        spec = load_spec("examples/basic.yaml")
        self.assertEqual(validate_spec(spec, "schemas/layout_ir.schema.json"), ())

    def test_raw_canonical_document_matches_schema(self):
        spec = load_spec("examples/basic.yaml")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout-ir.json"
            path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")
            self.assertEqual(validate_mapping(load_mapping(path), "schemas/layout_ir.schema.json"), ())

    def test_schema_reports_unknown_property_and_wrong_type(self):
        spec = load_spec("examples/basic.yaml")
        raw = spec.to_dict()
        raw["future_field"] = True
        raw["grid_mm"] = 100.5

        issues = validate_mapping(raw, "schemas/layout_ir.schema.json")
        messages = "\n".join(f"{issue.path}: {issue.message}" for issue in issues)
        self.assertIn("future_field", messages)
        self.assertIn("100.5", messages)


if __name__ == "__main__":
    unittest.main()
