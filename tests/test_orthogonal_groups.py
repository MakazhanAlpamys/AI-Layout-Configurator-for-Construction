"""Declared groups retain direct identities without inventing engineering evidence."""

from dataclasses import replace
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

from layout_configurator.building import ZoneSpec
from layout_configurator.equipment import EquipmentLayoutResult
from layout_configurator.flows import FlowRoutingResult
from layout_configurator.models import WindowSpec
from test_orthogonal_pdf import _fixture


def group_fixture():
    building, layout, equipment, routing = _fixture()
    building = replace(building, zones=(
        ZoneSpec('production', 'process', ('main',), cleanroom_class='ISO 8', pressure_pa=15),
        ZoneSpec('packaging', 'clean', ('corner',), parent_zone_id='production', airlock=True),
        ZoneSpec('buffer', 'support', (), parent_zone_id='production')),
        flows=(replace(building.flows[0], id='production', type='material', to_ids=('packaging',), stage='packing'),))
    routing = FlowRoutingResult((replace(routing.routes[0], flow_id='production', to_id='packaging'),))
    return building, layout, equipment, routing


def source_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


class OrthogonalGroupsTests(unittest.TestCase):
    def setUp(self):
        self.building, self.layout, self.equipment, self.routing = group_fixture()

    def api(self):
        return importlib.import_module('layout_configurator.orthogonal_groups')

    def check(self, **options):
        equipment = options.pop('equipment', self.equipment)
        routing = options.pop('routing', self.routing)
        return self.api().validate_orthogonal_groups(self.building, self.layout, equipment, routing=routing, **options)

    def test_direct_hierarchy_and_supplied_flow_products_have_separate_identities(self):
        report = self.check()
        self.assertTrue(report.ok, report.to_dict())
        data = report.to_dict()
        groups = {(row['kind'], row['id']): row for row in data['groups']}
        self.assertEqual(len(groups), 4)
        self.assertEqual(groups['zone', 'production']['members'], [
            {'kind': 'room', 'id': 'main'}, {'kind': 'zone', 'id': 'buffer'},
            {'kind': 'zone', 'id': 'packaging'}])
        self.assertEqual(groups['zone', 'packaging']['members'], [{'kind': 'room', 'id': 'corner'}])
        self.assertEqual(groups['zone', 'buffer']['members'], [])
        route_id = '["production","source","packaging","main","corner"]'
        self.assertEqual(groups['flow-system', 'production']['members'], [
            {'kind': 'equipment', 'id': 'source'}, {'kind': 'route', 'id': route_id}])
        self.assertEqual(groups['zone', 'production']['declaration'], self.building.zones[0].to_dict())
        self.assertEqual(groups['flow-system', 'production']['declaration'], self.building.flows[0].to_dict())
        self.assertEqual(data['policy']['geometry_validation'], 'NOT_EVALUATED')
        self.assertEqual(data['full_facility_validation'], {'status': 'UNKNOWN', 'ok': False})
        self.assertEqual(data['engineering_acceptance'], 'PENDING')
        self.assertFalse(data['proven_building_infeasible'])
        data['groups'][0]['members'].append({'kind': 'service', 'id': 'invented'})
        self.assertNotEqual(data['groups'], report.to_dict()['groups'])

    def test_omitted_products_remain_declared_without_fabricated_members(self):
        for equipment, routing, expected_equipment, expected_routing in (
                (None, None, 'NOT_SUPPLIED', 'NOT_SUPPLIED'),
                (self.equipment, None, 'SUPPLIED', 'NOT_SUPPLIED'),
                (None, self.routing, 'NOT_SUPPLIED', 'SUPPLIED')):
            with self.subTest(equipment=expected_equipment, routing=expected_routing):
                data = self.check(equipment=equipment, routing=routing).to_dict()
                self.assertEqual(data['status'], 'PASS')
                self.assertEqual(data['states'], {'equipment': expected_equipment, 'routing': expected_routing})
                flow = data['groups'][-1]
                self.assertEqual([member['kind'] for member in flow['members']],
                                 (['equipment'] if equipment is not None else []) +
                                 (['route'] if routing is not None else []))
                self.assertEqual(flow['unprojected_equipment_ids'], ['source'] if equipment is None else [])
                self.assertEqual(data['groups'][1]['members'], [{'kind': 'room', 'id': 'corner'}])

    def test_exact_source_hashes_retain_integer_pressure_and_original_geometry(self):
        report = self.check().to_dict()
        self.assertEqual(report['source'], {
            'building_sha256': source_hash(self.building.to_dict()),
            'layout_sha256': source_hash(self.layout.to_dict()),
            'equipment_sha256': source_hash(self.equipment.to_dict(self.building)),
            'routing_sha256': source_hash(self.routing.to_dict())})
        old = report['source']['building_sha256']
        self.building = replace(self.building, zones=(replace(self.building.zones[0], pressure_pa=15.0),
                                                      *self.building.zones[1:]))
        current = self.check().to_dict()
        self.assertNotEqual(old, current['source']['building_sha256'])
        self.assertEqual(current['groups'][2]['declaration']['pressure_pa'], 15.0)

    def test_raw_types_invalid_references_and_duplicates_cannot_be_normalized_away(self):
        original = self.building
        variants = [replace(original, zones=(replace(original.zones[0], **field), *original.zones[1:]))
                    for field in ({'airlock': 'false'}, {'pressure_pa': True}, {'pressure_pa': 10**400},
                                  {'cleanroom_class': 8}, {'type': 4}, {'room_ids': ('main', 'main')},
                                  {'parent_zone_id': 'packaging'}, {'id': 'unknown\nzone'})]
        variants += [replace(original, flows=(replace(original.flows[0], **field),))
                     for field in ({'required': 1}, {'minimum_clear_width_mm': '800'},
                                   {'stage': 4}, {'from_ids': ('missing',)}, {'to_ids': ('source',)})]
        for number, building in enumerate(variants):
            with self.subTest(case=number):
                self.building = building
                report = self.check()
                self.assertFalse(report.ok)
                self.assertEqual(report.groups, ())
                self.assertEqual(report.status, 'FAIL')

    def test_original_counts_precede_serialization_and_source_bytes_are_bounded(self):
        module = self.api()
        self.building = replace(self.building, zones=tuple(ZoneSpec('z' + str(index), 'zone')
                                                          for index in range(65)))
        with patch.object(type(self.building), 'to_dict', side_effect=AssertionError('must count first')):
            report = self.check()
        self.assertEqual(report.status, 'FAIL')
        self.assertEqual(report.groups, ())
        self.building, self.layout, self.equipment, self.routing = group_fixture()
        with patch.object(module, 'MAX_GROUP_SOURCE_BYTES', 64):
            self.assertFalse(self.check().ok)
        with patch.object(module, 'MAX_GROUP_MEMBERS', 1):
            self.assertFalse(self.check().ok)

    def test_supplied_duplicate_unknown_or_reversed_routes_cannot_form_group_members(self):
        original = self.routing.routes[0]
        variants = (FlowRoutingResult((original, original)),
                    FlowRoutingResult((replace(original, flow_id='missing'),)),
                    FlowRoutingResult((replace(original, from_id='packaging', to_id='source'),)))
        for routing in variants:
            with self.subTest(routes=routing):
                self.assertFalse(self.check(routing=routing).ok)
        duplicate = EquipmentLayoutResult((self.equipment.placements[0], self.equipment.placements[0]))
        self.assertFalse(self.check(equipment=duplicate).ok)

    def test_no_declared_groups_and_comma_identities_are_explicit(self):
        self.building = replace(self.building, zones=(), flows=())
        data = self.check(routing=None).to_dict()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['groups'], [])
        self.building, self.layout, self.equipment, self.routing = group_fixture()
        self.building = replace(self.building, flows=(replace(self.building.flows[0], id='flow,packing'),))
        self.routing = FlowRoutingResult((replace(self.routing.routes[0], flow_id='flow,packing'),))
        data = self.check().to_dict()
        self.assertEqual(data['groups'][-1]['id'], 'flow,packing')
        self.assertEqual(json.loads(data['groups'][-1]['members'][1]['id'])[0], 'flow,packing')

    def test_original_truthy_rotation_flag_cannot_be_hidden_by_serialization(self):
        self.building = replace(self.building, layout=replace(self.building.layout,
            rooms=(replace(self.building.layout.rooms[0], rotatable='false'), self.building.layout.rooms[1])))
        self.assertFalse(self.check().ok)

    def test_optional_source_counts_precede_serialization(self):
        self.building = replace(self.building, layout=replace(self.building.layout,
            windows=tuple(WindowSpec('w' + str(number), 'main', 'bottom', 1000, 800) for number in range(129))))
        with patch.object(type(self.building.layout), 'to_dict', side_effect=AssertionError('must count first')):
            report = self.check()
        self.assertEqual(report.status, 'FAIL')


if __name__ == '__main__':
    unittest.main()
