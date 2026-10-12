import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import ifcopenshell
import ifcopenshell.util.element

from layout_configurator.cli import main
from layout_configurator.facility_floors import FacilityFloorsError, load_facility_floors
from layout_configurator.facility_floors_qa import validate_facility_floors_bundle


class FacilityServiceLinkTests(unittest.TestCase):
    def setUp(self):
        self.working = tempfile.TemporaryDirectory()
        self.addCleanup(self.working.cleanup)
        self.root = Path(self.working.name)
        rooms = [{"id": name, "area": 9, "min_area": 9, "max_area": 9,
                  "min_width": 3000, "min_depth": 3000} for name in ("production", "utility", "shaft")]
        rooms[1]["required_adjacency"] = ["production", "shaft"]
        floor = {"layout": {"project_name": "Service coordination", "boundary": {"width": 9000, "height": 3000},
                            "grid_mm": 1000, "entry_room": "production", "rooms": rooms},
                 "zones": [{"id": "process", "room_ids": ["production"]},
                           {"id": "technical", "type": "service", "room_ids": ["utility"]}]}
        (self.root / "floor.json").write_text(json.dumps(floor))
        self.raw = {"facility_multi_floor": {"project_name": "Linked service example",
            "structural_grid": {"axes_x_mm": [0, 3000, 6000, 9000], "axes_y_mm": [0, 3000]},
            "floors": [{"level": 0, "program": "floor.json"}, {"level": 1, "program": "floor.json"}],
            "vertical_cores": [{"id": "services", "kind": "service_shaft", "rooms": {0: "shaft", 1: "shaft"}}],
            "facility_blocks": [{"id": "production_block", "zones": {0: ["process", "technical"], 1: ["process", "technical"]}}],
            "service_zone_links": [{"id": "utilities", "from": {"level": 0, "zone_id": "technical"},
                                    "to": {"level": 1, "zone_id": "technical"}, "via": "services",
                                    "minimum_clear_width_mm": 1000, "required": True}]}}
        self.source = self.root / "stack.json"

    def load(self):
        self.source.write_text(json.dumps(self.raw))
        return load_facility_floors(self.source)

    def test_service_zone_links_compile_into_typed_ordinary_floor_legs(self):
        spec = self.load()
        self.assertTrue(hasattr(spec, "blocks"), "Multi-storey facility block declarations are missing")
        self.assertTrue(hasattr(spec, "service_links"), "Typed service-zone link declarations are missing")
        self.assertEqual(spec.blocks[0].zones[1], ("process", "technical"))
        self.assertEqual(spec.service_links[0].id, "utilities")
        self.assertEqual(spec.cross_flows[0].id, "service-link:utilities")
        self.assertEqual(spec.core_for(spec.cross_flows[0]).kind, "service_shaft")
        programs = spec.floor_programs()
        self.assertEqual(programs[0].flows[0].type, "service")
        self.assertEqual(programs[0].flows[0].from_ids, ("technical",))
        self.assertEqual(programs[0].flows[0].to_ids, ("shaft",))
        self.assertEqual(programs[1].flows[0].from_ids, ("shaft",))
        self.assertEqual(programs[1].flows[0].to_ids, ("technical",))

    def test_invalid_service_links_are_rejected_before_any_solve(self):
        mutations = (
            lambda p: p["service_zone_links"][0]["from"].update(zone_id="missing"),
            lambda p: p["service_zone_links"][0]["to"].update(level=0),
            lambda p: p["service_zone_links"][0].update(via="missing"),
            lambda p: p["vertical_cores"][0].update(kind="stair"),
            lambda p: p["service_zone_links"][0].update(required="false"),
            lambda p: p["service_zone_links"][0].update(minimum_clear_width_mm=float("nan")),
            lambda p: p["service_zone_links"].append(dict(p["service_zone_links"][0])),
            lambda p: p["facility_blocks"][0]["zones"].update({"1": ["missing"]}),
            lambda p: p["facility_blocks"][0]["zones"].update({"1": ["technical", "technical"]}),
            lambda p: p["facility_blocks"][0].update(zones={0: ["process"]}),
        )
        original = json.loads(json.dumps(self.raw))
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.raw = json.loads(json.dumps(original))
                mutate(self.raw["facility_multi_floor"])
                with self.assertRaises(FacilityFloorsError):
                    self.load()

    def test_compiled_leg_identity_cannot_replace_an_existing_local_flow(self):
        path = self.root / "floor.json"
        floor = json.loads(path.read_text())
        floor["flows"] = [{"id": "service-link:utilities@L0", "type": "service", "from": ["utility"], "to": ["shaft"]}]
        path.write_text(json.dumps(floor))
        with self.assertRaisesRegex(FacilityFloorsError, "collides"):
            self.load()

    def test_real_bundle_groups_links_and_rechecks_native_memberships_independently(self):
        spec = self.load()
        output = self.root / "bundle"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["generate-facility-floors", str(self.source), "--output", str(output), "--variants", "1",
                         "--max-attempts", "2", "--time-limit", "2", "--seed", "1", "--unified-ifc"])
        self.assertEqual(code, 0)
        report = json.loads((output / "multi_floor_report.json").read_text())
        self.assertIn("facility_blocks", report)
        self.assertIn("service_zone_links", report)
        self.assertTrue(validate_facility_floors_bundle(spec, output).ok)
        path = output / "facility_stack_01.ifc"
        model = ifcopenshell.open(path)
        props = lambda entity: ifcopenshell.util.element.get_psets(entity)
        groups = {props(group).get("Pset_FacilityIdentity", {}).get("SemanticId"): group
                  for group in model.by_type("IfcGroup")}
        self.assertIn("facility-block/production_block", groups)
        block = groups["facility-block/production_block"]
        members = {props(member)["Pset_FacilityIdentity"]["SemanticId"]
                   for relation in block.IsGroupedBy for member in relation.RelatedObjects}
        self.assertEqual(members, {"L0/zone/process", "L0/zone/technical", "L1/zone/process", "L1/zone/technical"})
        self.assertIn("service-zone-link/utilities", groups)
        link = groups["service-zone-link/utilities"]
        self.assertTrue(link.is_a("IfcSystem"))
        members = {props(member)["Pset_FacilityIdentity"]["SemanticId"]
                   for relation in link.IsGroupedBy for member in relation.RelatedObjects}
        self.assertEqual(members, {"L0/zone/technical", "L1/zone/technical", "vertical-core/services",
                                  "cross-floor-flow/service-link:utilities"})
        model.remove(link.IsGroupedBy[0])
        model.write(str(path))
        import hashlib
        report["stacks"][0]["unified_ifc"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (output / "multi_floor_report.json").write_text(json.dumps(report))
        changed = validate_facility_floors_bundle(spec, output)
        self.assertFalse(changed.ok)
        self.assertTrue(any(check.id == "IFC_SEMANTICS" and not check.ok for check in changed.checks))


if __name__ == "__main__":
    unittest.main()
