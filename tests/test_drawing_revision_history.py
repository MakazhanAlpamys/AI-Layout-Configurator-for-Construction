"""Supplied drawing history must be backed by real prior canonical results."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from layout_configurator.equipment import validate_equipment_layout
from layout_configurator.facility import default_facility_profile, validate_building
from layout_configurator.flows import route_flows, validate_flow_routes
from layout_configurator.io import write_building_result
from test_drawing_sheets import example

_UNSET = object()

def prior_record(revision="P00", previous=None, date="2026-09-01"):
    building, result, equipment, _ = example()
    profile = default_facility_profile()
    profile = replace(profile, drawing=replace(profile.drawing, revision=revision))
    routes = route_flows(building, result, equipment)
    er = validate_equipment_layout(building, result, equipment)
    fr = validate_flow_routes(building, result, routes, equipment)
    br = validate_building(building, result, equipment, routes, profile=profile,
                           equipment_report=er, flow_report=fr)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "prior.json"
        write_building_result(path, building, result, equipment, routes, fr,
                              equipment_report=er, facility_report=br)
        # Whitespace is part of the supplied source, never canonicalized away.
        source = path.read_bytes().decode("utf-8") + "\n  \n"
    return {"revision": revision, "previous_revision": previous, "date": date,
            "author": "Инженер А", "description": "Проверка размещения",
            "source_json": source, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()}


def supplied_history():
    return {"current_revision": "P02", "previous_revision": "P01",
            "records": [prior_record(), prior_record("P01", "P00", "2026-09-03")]}


class SuppliedDrawingHistoryTests(unittest.TestCase):
    def setUp(self):
        self.building, _, _, _ = example()
        self.drawing = replace(default_facility_profile().drawing, revision="P02")
        self.raw = supplied_history()

    def parse(self, raw=_UNSET):
        self.assertIsNotNone(importlib.util.find_spec("layout_configurator.drawing_revision_history"),
                             "Source-backed revision history is not implemented")
        from layout_configurator.drawing_revision_history import parse_revision_history
        return parse_revision_history(self.raw if raw is _UNSET else raw,
                                      building=self.building, drawing=self.drawing)

    def mutate_source(self, transform, record=0):
        row = self.raw["records"][record]
        payload = json.loads(row["source_json"])
        transform(payload)
        row["source_json"] = json.dumps(payload, ensure_ascii=False)
        row["source_sha256"] = hashlib.sha256(row["source_json"].encode("utf-8")).hexdigest()

    def test_actual_prior_revision_is_derived_and_exact_source_bytes_preserved(self):
        history = self.parse()
        self.assertEqual(history.current_revision, "P02")
        self.assertEqual(history.previous_revision, "P01")
        self.assertEqual(history.records[0].source_bytes, self.raw["records"][0]["source_json"].encode("utf-8"))
        identity = history.records[0].source_identity
        self.assertEqual(identity["project_name"], self.building.layout.project_name)
        self.assertEqual(identity["variant"], 7)
        self.assertEqual(identity["revision"], "P00")
        self.assertEqual(identity["building_version"], "0.2")
        self.assertEqual(identity["layout_version"], "0.1")
        self.assertEqual(history.to_input(), self.raw)
        index = history.to_index()
        self.assertNotIn("source_json", index["records"][0])
        self.assertEqual(index["records"][0]["source_sha256"], self.raw["records"][0]["source_sha256"])
        self.assertEqual(index["records"][0]["author"], "Инженер А")

    def test_empty_or_malformed_explicit_history_is_rejected(self):
        for raw in (None, [], {}, {**self.raw, "records": []}, {**self.raw, "records": "P00"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.parse(raw)

    def test_predecessor_chain_is_explicit_contiguous_and_acyclic(self):
        mutations = (
            lambda value: value.update(current_revision="P99"),
            lambda value: value.update(previous_revision="P00"),
            lambda value: value["records"][0].update(previous_revision="P01"),
            lambda value: value["records"][1].update(previous_revision=None),
            lambda value: value["records"][1].update(previous_revision="unknown"),
            lambda value: value["records"][1].update(revision="P00"),
            lambda value: value["records"][1].update(revision="P02"),
            lambda value: value["records"].reverse(),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                raw = deepcopy(self.raw)
                mutation(raw)
                self.parse(raw)

    def test_dates_and_authors_are_supplied_strict_metadata(self):
        for field, value in (("date", "2026-02-30"), ("date", "09/01/2026"), ("date", True),
                             ("author", "   "), ("author", False), ("description", ""),
                             ("revision", True), ("source_sha256", True)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                raw = deepcopy(self.raw)
                raw["records"][0][field] = value
                self.parse(raw)
        self.raw["records"][1]["date"] = "2026-08-30"
        with self.assertRaises(ValueError):
            self.parse()

    def test_a_hash_or_revision_claim_cannot_replace_the_real_source(self):
        self.raw["records"][0]["source_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.parse()
        self.raw = supplied_history()
        self.mutate_source(lambda value: value["facility_validation"]["profile"]["drawing"].update(revision="P99"))
        with self.assertRaises(ValueError):
            self.parse()

    def test_an_unrelated_project_and_a_source_without_revision_evidence_are_rejected(self):
        self.mutate_source(lambda value: value["spec"]["layout"].update(project_name="Unrelated project"))
        with self.assertRaises(ValueError):
            self.parse()
        self.raw = supplied_history()
        self.mutate_source(lambda value: value.pop("facility_validation"))
        with self.assertRaises(ValueError):
            self.parse()

    def test_prior_contract_is_read_as_a_real_building_result(self):
        mutations = (
            lambda value: value["layout"]["rooms"].pop("process"),
            lambda value: value["layout"]["rooms"]["process"].update(width=-1),
            lambda value: value["equipment"][0].update(rotated="false"),
            lambda value: value["equipment"][0].update(room_id="missing"),
            lambda value: value["equipment"][0].update(equipment_id="missing"),
            lambda value: value["equipment"].append(deepcopy(value["equipment"][0])),
            lambda value: value["spec"]["layout"]["rooms"][0].update(min_area=True),
            lambda value: value["spec"]["layout"].update(version=1),
            lambda value: value["spec"].update(version=False),
            lambda value: value["facility_validation"]["profile"]["drawing"].update(revision=1),
            lambda value: value["flow_routes"]["routes"][0].update(flow_id="missing"),
            lambda value: value["flow_routes"]["routes"][0]["points"][0].update(x="0"),
            lambda value: value["equipment"][0]["clearance"].update(width=True),
            lambda value: value["spec"].update(project_name="Contradictory project identity"),
            lambda value: value["spec"]["layout"]["rooms"][0].update(id=" lobby "),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.raw = supplied_history()
                self.mutate_source(mutation)
                self.parse()

    def test_duplicate_json_fields_and_nonfinite_source_metadata_are_rejected(self):
        for source in ('{"spec":{},"spec":{},"layout":{}}', '{"spec":{},"layout":{},"unexpected":NaN}'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                row = self.raw["records"][0]
                row["source_json"] = source
                row["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
                self.parse()

    def test_existing_prior_history_cannot_be_replaced_by_a_different_supplied_chain(self):
        from layout_configurator.drawing_revision_history import parse_revision_history
        earlier = parse_revision_history({"current_revision": "P01", "previous_revision": "P00",
                                           "records": self.raw["records"][:1]},
                                          building=self.building, drawing=replace(self.drawing, revision="P01"))
        self.mutate_source(lambda value: value.update(drawing_revision_history=earlier.to_index()), record=1)
        self.parse()  # Existing native history and the supplied chain agree.
        self.raw["records"][0]["author"] = "A replacement attribution"
        with self.assertRaises(ValueError):
            self.parse()

    def test_source_copy_is_exact_and_never_uses_author_or_revision_as_a_path(self):
        history = self.parse()
        from layout_configurator.drawing_revision_history import write_revision_sources
        with tempfile.TemporaryDirectory() as directory:
            write_revision_sources(directory, history)
            for row, supplied in zip(history.to_index()["records"], self.raw["records"]):
                path = Path(directory) / row["source_file"]
                self.assertEqual(path.read_bytes(), supplied["source_json"].encode("utf-8"))
                self.assertEqual(path.name, supplied["source_sha256"] + ".json")


if __name__ == "__main__":
    unittest.main()
