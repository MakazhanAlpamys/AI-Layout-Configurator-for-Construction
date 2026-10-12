"""The optional shape cache reuses validated CP-SAT geometry, within one call."""

import unittest
from dataclasses import replace

from layout_configurator.hierarchy import solve_layouts_auto, solve_layouts_hierarchical
from layout_configurator.models import BoundarySpec, ExternalEntrySpec, LayoutIR, Rect, RoomSpec
from layout_configurator.solver import InfeasibleLayout
from layout_configurator.validation import validate_layout


def twin_clusters():
    rooms = []
    for prefix in ("a", "b"):
        rooms.append({
            "id": f"{prefix}_hub", "type": "corridor", "target_area": 8,
            "min_area": 8, "max_area": 8, "min_width": 2000, "min_depth": 4000,
            "required_adjacency": ["b_hub"] if prefix == "a" else [],
        })
        for index in range(2):
            rooms.append({
                "id": f"{prefix}_leaf_{index}", "type": "room", "target_area": 4,
                "min_area": 4, "max_area": 6, "min_width": 2000, "min_depth": 2000,
                "required_adjacency": [f"{prefix}_hub"],
            })
    return LayoutIR.from_mapping({
        "project_name": "Repeated cluster shapes", "grid_mm": 100,
        "boundary": {"width": 16000, "height": 12000},
        "entry_room": "a_hub", "rooms": rooms,
    })


class HierarchyShapeCacheTests(unittest.TestCase):
    def test_equivalent_clusters_reuse_shapes_and_validate_after_id_remapping(self):
        spec = twin_clusters()
        evidence = {}
        result = solve_layouts_hierarchical(
            spec, 1, 1, 1, cache_cluster_shapes=True, evidence=evidence,
        )[0]
        self.assertTrue(validate_layout(spec, result).ok)
        self.assertEqual(set(result.placements), {room.id for room in spec.rooms})
        report = evidence["cluster_shape_cache"]
        self.assertEqual(report["scope"], "single_hierarchical_invocation")
        self.assertEqual((report["misses"], report["hits"], report["rejected_hits"]), (1, 1, 0))
        self.assertEqual(report["events"][1]["source_room_ids"], ["a_hub", "a_leaf_0", "a_leaf_1"])
        self.assertEqual(report["events"][1]["room_ids"], ["b_hub", "b_leaf_0", "b_leaf_1"])

    def test_changed_equipment_fit_is_not_reused_and_its_actual_geometry_fits(self):
        spec = twin_clusters()
        evidence = {}
        result = solve_layouts_hierarchical(
            spec, 1, 1, 1, cache_cluster_shapes=True, evidence=evidence,
            equipment_fit_options={"b_leaf_0": (("wide_machine", ((3000, 2000),)),)},
        )[0]
        self.assertTrue(validate_layout(spec, result).ok)
        machine_room = result.placements["b_leaf_0"]
        self.assertGreaterEqual(machine_room.width, 3000)
        self.assertGreaterEqual(machine_room.height, 2000)
        self.assertEqual(evidence["cluster_shape_cache"]["hits"], 0)
        self.assertEqual(evidence["cluster_shape_cache"]["misses"], 2)

    def test_each_invocation_starts_empty_and_dispatcher_reports_the_opt_in(self):
        spec = twin_clusters()
        for _ in range(2):
            evidence = {}
            result = solve_layouts_auto(
                spec, 1, 1, 1, strategy="hierarchical", evidence=evidence,
                cache_cluster_shapes=True,
            )[0]
            self.assertTrue(validate_layout(spec, result).ok)
            self.assertEqual(evidence["room_solver_used"], "hierarchical")
            self.assertEqual(evidence["cluster_shape_cache"]["misses"], 1)

    def test_disabled_cache_does_not_add_evidence_or_change_deterministic_results(self):
        spec = twin_clusters()
        baseline = solve_layouts_auto(spec, 1, 1, 1, strategy="hierarchical", deterministic_units=0.1)
        evidence = {"caller": "preserved"}
        disabled = solve_layouts_auto(
            spec, 1, 1, 1, strategy="hierarchical", evidence=evidence,
            deterministic_units=0.1, cache_cluster_shapes=False,
        )
        self.assertEqual(baseline, disabled)
        self.assertEqual(evidence, {"caller": "preserved", "room_solver_used": "hierarchical"})

    def test_cached_shapes_keep_global_entry_daylight_cutout_pin_and_axis_constraints(self):
        spec = twin_clusters()
        office = RoomSpec(
            "office", "office", 4, 4, 4, 2000, 2000,
            required_adjacency=("a_hub", "b_hub"), needs_daylight=True,
        )
        spec = replace(
            spec, rooms=(*spec.rooms, office), entry_room="office",
            boundary=BoundarySpec(16000, 12000, (Rect(0, 0, 2000, 2000),)),
            external_entry=ExternalEntrySpec("main", "office", "left", 1000, 900),
        )
        pinned = Rect(0, 2000, 2000, 2000)
        evidence = {}
        result = solve_layouts_hierarchical(
            spec, 1, 1, 1, cache_cluster_shapes=True, evidence=evidence,
            fixed_rects={"office": pinned}, axis_aligned_room_ids=("office",),
            structural_axes_x_mm=(0, 2000), structural_axes_y_mm=(2000, 4000),
        )[0]
        report = validate_layout(spec, result)
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(result.placements["office"], pinned)
        self.assertEqual(evidence["cluster_shape_cache"]["hits"], 1)

    def test_global_failure_records_actual_model_status_with_bounded_shape_scope(self):
        evidence = {}
        with self.assertRaises(InfeasibleLayout):
            solve_layouts_hierarchical(
                twin_clusters(), 1, 1, 1, cache_cluster_shapes=True, evidence=evidence,
                fixed_rects={"a_leaf_0": Rect(0, 0, 2000, 2000), "b_leaf_0": Rect(0, 0, 2000, 2000)},
            )
        stage = evidence["cluster_shape_cache"]["global_search"][-1]
        self.assertEqual(stage["model_status"], "INFEASIBLE")
        self.assertEqual(stage["proof_scope"], "selected_cluster_shapes")
        self.assertFalse(stage["original_program_infeasibility_proven"])

    def test_auto_monolithic_fallback_does_not_receive_the_hierarchy_cache_flag(self):
        spec = twin_clusters()
        evidence = {}
        result = solve_layouts_auto(
            spec, 1, 1, 1, cache_cluster_shapes=True, evidence=evidence,
            first_solution_only=True,
        )[0]
        self.assertTrue(validate_layout(spec, result).ok)
        self.assertEqual(evidence["room_solver_used"], "monolithic")
        self.assertIn("first_solution_only", evidence["hierarchy_fallback_reason"])


class ShapeCacheSafetyTests(unittest.TestCase):
    def request(self, spec=None, **changes):
        from layout_configurator.shape_cache import cluster_shape_request
        spec = spec or twin_clusters()
        arguments = dict(
            side_contacts={"a_hub": 1}, fit_options={}, adjacency_mm=900,
            time_limit_seconds=1, seed=1, deterministic_units=None,
            aspects=(1, 2), fills=(0.8, 0.7), max_shapes=4,
        )
        arguments.update(changes)
        return cluster_shape_request(spec, ("a_hub", "a_leaf_0", "a_leaf_1"), **arguments)

    def source_shapes(self):
        return [{
            "a_hub": Rect(0, 0, 2000, 4000),
            "a_leaf_0": Rect(2000, 0, 2000, 2000),
            "a_leaf_1": Rect(2000, 2000, 2000, 2000),
        }]

    def test_solver_settings_relations_dimensions_and_equipment_invalidate_reuse(self):
        from layout_configurator.shape_cache import ClusterShapeCache
        spec = twin_clusters()
        cache = ClusterShapeCache()
        request = self.request(spec)
        cache.store(request, self.source_shapes())
        changed_specs = [
            replace(spec, grid_mm=200),
            replace(spec, door_width_mm=1000),
            replace(spec, window_width_mm=1500),
            replace(spec, boundary=replace(spec.boundary, width_mm=18000)),
        ]
        for attribute, value in (
            ("target_area_m2", 5), ("max_area_m2", 8), ("min_width_mm", 3000),
            ("rotatable", True), ("preferred_adjacency", ("a_leaf_1",)),
            ("forbidden_adjacency", ("a_leaf_1",)),
            ("required_adjacency", ("a_hub", "a_leaf_1")),
        ):
            changed_specs.append(replace(spec, rooms=tuple(
                replace(room, **{attribute: value}) if room.id == "a_leaf_0" else room
                for room in spec.rooms
            )))
        for changed in changed_specs:
            with self.subTest(changed=changed):
                self.assertIsNone(cache.get(self.request(changed)))
        for changes in (
            {"seed": 2}, {"time_limit_seconds": 0.5}, {"deterministic_units": 0.1},
            {"adjacency_mm": 1300}, {"side_contacts": {"a_hub": 3}},
            {"fit_options": {"a_leaf_0": (("machine", ((3000, 2000),)),)}},
            {"aspects": (1, 3)}, {"fills": (0.9,)}, {"max_shapes": 2},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(cache.get(self.request(**changes)))

    def test_returned_mapping_mutation_does_not_change_cached_coordinates(self):
        from layout_configurator.shape_cache import ClusterShapeCache
        cache = ClusterShapeCache()
        request = self.request()
        source = self.source_shapes()
        cache.store(request, source)
        source[0]["a_hub"] = Rect(0, 0, 1, 1)
        returned = cache.get(request)
        returned[0]["a_hub"] = Rect(0, 0, 1, 1)
        self.assertEqual(cache.get(request)[0]["a_hub"], Rect(0, 0, 2000, 4000))

    def test_forged_checksum_still_cannot_make_invalid_geometry_a_hit(self):
        from layout_configurator.shape_cache import ClusterShapeCache, _payload_digest
        cache = ClusterShapeCache()
        request = self.request()
        cache.store(request, self.source_shapes())
        entry = cache._entries[request.key]
        invalid = (((0, 0, 1000, 4000), (2000, 0, 2000, 2000), (2000, 2000, 2000, 2000)),)
        cache._entries[request.key] = replace(entry, payload=invalid, checksum=_payload_digest(invalid))
        self.assertIsNone(cache.get(request))
        self.assertEqual(cache.evidence["rejected_hits"], 1)
        self.assertEqual(cache.evidence["events"][-1]["reason"], "invalid_geometry")

    def test_corrupted_payload_is_rejected_before_coordinate_decoding(self):
        from layout_configurator.shape_cache import ClusterShapeCache
        cache = ClusterShapeCache()
        request = self.request()
        cache.store(request, self.source_shapes())
        entry = cache._entries[request.key]
        cache._entries[request.key] = replace(entry, payload=(("broken",),))
        self.assertIsNone(cache.get(request))
        self.assertEqual(cache.evidence["events"][-1]["reason"], "payload_checksum")

    def test_mutable_coordinates_with_a_matching_checksum_are_rejected(self):
        from layout_configurator.shape_cache import ClusterShapeCache, _payload_digest
        cache = ClusterShapeCache()
        request = self.request()
        cache.store(request, self.source_shapes())
        entry = cache._entries[request.key]
        mutable = (([0, 0, 2000, 4000], (2000, 0, 2000, 2000), (2000, 2000, 2000, 2000)),)
        cache._entries[request.key] = replace(entry, payload=mutable, checksum=_payload_digest(mutable))
        self.assertIsNone(cache.get(request))
        self.assertEqual(cache.evidence["rejected_hits"], 1)


if __name__ == "__main__":
    unittest.main()
