import json
import tempfile
import unittest
from pathlib import Path

from layout_configurator.cli import main
from layout_configurator.io import load_spec
from layout_configurator.schema import load_canonical_spec, load_mapping, normalize_mapping, validate_mapping, validate_spec


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

    def test_strict_normalizer_accepts_canonical_spec_and_rejects_generated_data(self):
        spec = load_spec("examples/basic.yaml")
        canonical = normalize_mapping(spec.to_dict(), "schemas/layout_ir.schema.json")
        self.assertEqual(canonical.to_dict(), spec.to_dict())

        generated_data = spec.to_dict()
        generated_data["placements"] = {"hall": {"x": 0, "y": 0}}
        with self.assertRaisesRegex(ValueError, "does not match schema"):
            normalize_mapping(generated_data, "schemas/layout_ir.schema.json")

    def test_canonical_example_is_accepted_by_strict_normalizer(self):
        spec = normalize_mapping(load_mapping("examples/basic_canonical.json"), "schemas/layout_ir.schema.json")
        self.assertEqual(spec.project_name, "Demo house")
        self.assertEqual(len(spec.rooms), 4)

    def test_normalize_cli_round_trip_and_rejects_shorthand(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "canonical.json"
            self.assertEqual(
                main(["normalize", "examples/basic_canonical.json", "--output", str(output)]),
                0,
            )
            self.assertEqual(validate_mapping(load_mapping(output), "schemas/layout_ir.schema.json"), ())
            self.assertEqual(
                main(["normalize", "examples/basic.yaml", "--output", str(output)]),
                2,
            )

    def test_strict_generate_uses_canonical_boundary_before_solver(self):
        self.assertEqual(load_canonical_spec("examples/basic_canonical.json").project_name, "Demo house")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated"
            self.assertEqual(
                main(
                    [
                        "generate",
                        "examples/basic_canonical.json",
                        "--strict-input",
                        "--output",
                        str(output),
                        "--variants",
                        "1",
                        "--time-limit",
                        "10",
                    ]
                ),
                0,
            )
            self.assertTrue((output / "layout_01.ifc").exists())
            self.assertEqual(
                main(
                    [
                        "generate",
                        "examples/basic.yaml",
                        "--strict-input",
                        "--output",
                        str(output),
                        "--variants",
                        "1",
                        "--time-limit",
                        "10",
                    ]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
