"""Actual multi-candidate CP results remain canonical integer-valued IR."""

import unittest

from layout_configurator.orthogonal_solver import solve_orthogonal_candidates
from layout_configurator.result_geometry import parse_layout_result
from test_orthogonal_solver import _program


class OrthogonalSolverObjectiveTests(unittest.TestCase):
    def test_real_third_candidate_float_residue_does_not_break_canonical_result_schema(self):
        try:
            result = solve_orthogonal_candidates(_program(), variants=4, seed=1, deterministic_units=.05)
        except ValueError as exc:
            self.fail(f"Native integer-objective candidates must survive canonical serialization: {exc}")
        self.assertEqual(result.status, "DRAFT", result.to_dict())
        self.assertEqual(len(result.candidates), 4)
        for candidate in result.candidates:
            with self.subTest(variant=candidate.result.variant):
                self.assertIs(type(candidate.result.objective_value), int)
                parsed = parse_layout_result(candidate.result.to_dict())
                self.assertEqual(parsed.to_dict(), candidate.result.to_dict())
                self.assertTrue(candidate.room_report.ok)
