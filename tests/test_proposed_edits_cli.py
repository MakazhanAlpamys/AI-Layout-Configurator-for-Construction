import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from layout_configurator.building import BuildingIR
from layout_configurator.io import load_building_result
from layout_configurator.qa import validate_building_bundle


class ProposedEditsCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        building = BuildingIR.from_mapping({
            "version": "0.2", "layout": {
                "project_name": "Typed change", "boundary": {"width": 10000, "height": 10000},
                "rooms": [{"id": "work", "area": 9, "min_area": 9, "max_area": 9}],
            },
        })
        self.base = self.root / "base.json"
        self.base.write_text(json.dumps(building.to_dict()), encoding="utf-8")
        self.changes = self.root / "changes.json"
        self.changes.write_text(json.dumps({"edits": [{"operation": "set_room", "target_id": "work",
            "changes": {key: {"value": 16, "unit": "m2"} for key in ("target_area", "min_area", "max_area")}}]}), encoding="utf-8")
        self.source = self.root / "request.txt"
        self.source.write_text("Set the work room area to 16 m2.", encoding="utf-8")
        self.draft = self.root / "proposal.json"

    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "layout_configurator.cli", *map(str, args)],
                              capture_output=True, text=True, timeout=25,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _propose(self):
        result = self._run("propose-edits", self.base, self.changes, "--source-text", self.source,
                           "--output", self.draft, "--producer", "fixture-assistant", "--source-kind", "ai_proposal")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_confirmed_typed_change_produces_new_canonical_input_and_rechecked_bundle(self):
        self._propose()
        draft = json.loads(self.draft.read_text())
        self.assertEqual(draft["state"], "DRAFT")
        self.assertEqual(draft["source"]["kind"], "ai_proposal")
        updated = self.root / "updated.json"
        result = self._run("apply-edits", self.base, self.draft, "--output", updated,
                           "--confirm", "--reviewer", "Program reviewer")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(updated.with_suffix(".application.json").read_text())
        self.assertEqual(evidence["validation_status"], "PENDING_SOLVER_AND_CHECKS")
        self.assertTrue(evidence["derived_artifacts_invalidated"])
        generated = self.root / "generated"
        result = self._run("generate-building", updated, "--output", generated,
                           "--time-limit", "2", "--max-attempts", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        _, placement, _ = load_building_result(generated / "building_01.json")
        self.assertEqual(placement.placements["work"].area_m2, 16)
        self.assertTrue(validate_building_bundle(generated / "building_01.json").ok)

    def test_unconfirmed_proposal_cannot_overwrite_the_canonical_input(self):
        self._propose()
        original = self.base.read_bytes()
        result = self._run("apply-edits", self.base, self.draft, "--output", self.base)
        self.assertEqual(result.returncode, 2)
        self.assertIn("confirmation", result.stderr)
        self.assertEqual(self.base.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
