import unittest

from layout_configurator.commands import AddDoor, AddWindow, EditError, MoveRoom, RemoveDoor, RemoveExternalEntry, RemoveWindow, ResizeRoom, SetExternalEntry
from layout_configurator.editor import EditorState
from layout_configurator.models import LayoutIR, LayoutResult, Rect
from layout_configurator.walls import build_wall_plan


class EditorTests(unittest.TestCase):
    def test_move_and_resize_are_revalidated(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 8000, "height": 6000},
                "entry_room": "room",
                "rooms": [{"id": "room", "target_area": 12, "min_area": 10, "max_area": 15}],
            }
        )
        result = LayoutResult(variant=1, placements={"room": Rect(1000, 1000, 3000, 4000)})
        state = EditorState.from_layout(spec, result)

        moved = state.apply(MoveRoom("room", 500, 0))
        resized = moved.apply(ResizeRoom("room", 3000, 3500, anchor="bottom_left"))

        self.assertTrue(resized.report.ok)
        self.assertEqual(resized.result.placements["room"], Rect(1500, 1000, 3000, 3500))
        self.assertEqual(resized.history, ("MoveRoom", "ResizeRoom"))

    def test_invalid_move_does_not_produce_state(self):
        spec = LayoutIR.from_mapping(
            {"boundary": {"width": 5000, "height": 5000}, "rooms": [{"id": "room", "target_area": 4}]}
        )
        result = LayoutResult(variant=1, placements={"room": Rect(0, 0, 2000, 2000)})
        state = EditorState.from_layout(spec, result)
        with self.assertRaises(EditError):
            state.apply(MoveRoom("room", 4000, 0))
        self.assertEqual(state.result.placements["room"], Rect(0, 0, 2000, 2000))

    def test_add_door_turns_existing_contact_into_required_adjacency(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 6000, "height": 4000},
                "entry_room": "a",
                "rooms": [{"id": "a", "target_area": 12}, {"id": "b", "target_area": 12}],
            }
        )
        result = LayoutResult(
            variant=1,
            placements={"a": Rect(0, 0, 3000, 4000), "b": Rect(3000, 0, 3000, 4000)},
        )
        state = EditorState.from_layout(spec, result).apply(AddDoor("a", "b"))
        self.assertTrue(state.report.ok)
        self.assertIn("b", state.spec.room_by_id["a"].required_adjacency)

    def test_manual_door_replaces_centered_door_and_round_trips(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 6000, "height": 4000},
                "entry_room": "a",
                "rooms": [{"id": "a", "target_area": 12}, {"id": "b", "target_area": 12}],
            }
        )
        result = LayoutResult(
            variant=1,
            placements={"a": Rect(0, 0, 3000, 4000), "b": Rect(3000, 0, 3000, 4000)},
        )
        state = EditorState.from_layout(spec, result)

        added = state.apply(AddDoor("a", "b", 1000, 800))
        openings = build_wall_plan(added.spec, added.result).openings
        self.assertEqual(len(openings), 1)
        self.assertEqual(openings[0].id, "door_a_b_1")
        self.assertEqual(openings[0].center, (3000, 1000))
        self.assertEqual(openings[0].width, 800)

        restored = added.apply(RemoveDoor("door_a_b_1"))
        automatic = build_wall_plan(restored.spec, restored.result).openings
        self.assertEqual(len(automatic), 1)
        self.assertEqual(automatic[0].id, "auto-a-b")
        self.assertEqual(automatic[0].width, 900)

    def test_manual_window_replaces_auto_window_and_round_trips(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 3000, "height": 4000},
                "entry_room": "room",
                "rooms": [{"id": "room", "target_area": 12, "needs_daylight": True}],
            }
        )
        result = LayoutResult(variant=1, placements={"room": Rect(0, 0, 3000, 4000)})
        state = EditorState.from_layout(spec, result)

        added = state.apply(AddWindow("room", "right", 1000, 1000))
        windows = build_wall_plan(added.spec, added.result).windows
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].id, "window_room_1")
        self.assertEqual(windows[0].fixed, 3000)
        self.assertEqual(windows[0].width, 1000)

        restored = added.apply(RemoveWindow("window_room_1"))
        automatic = build_wall_plan(restored.spec, restored.result).windows
        self.assertEqual(len(automatic), 1)
        self.assertEqual(automatic[0].id, "auto-room")

    def test_invalid_manual_window_is_rejected_without_mutating_state(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 6000, "height": 5000},
                "entry_room": "room",
                "rooms": [{"id": "room", "target_area": 6}],
            }
        )
        result = LayoutResult(variant=1, placements={"room": Rect(1000, 1000, 2000, 3000)})
        state = EditorState.from_layout(spec, result)

        with self.assertRaises(EditError):
            state.apply(AddWindow("room", "left", 1500, 1000))
        self.assertEqual(state.spec.windows, ())

    def test_external_entry_commands_update_wall_plan_atomically(self):
        spec = LayoutIR.from_mapping(
            {
                "boundary": {"width": 5000, "height": 4000},
                "entry_room": "room",
                "rooms": [{"id": "room", "type": "vestibule", "target_area": 6}],
            }
        )
        result = LayoutResult(variant=1, placements={"room": Rect(0, 0, 3000, 2000)})
        state = EditorState.from_layout(spec, result)

        added = state.apply(SetExternalEntry("room", "bottom", 1000, 900, "front-door"))
        opening = build_wall_plan(added.spec, added.result).openings[0]
        self.assertEqual(added.spec.external_entry.id, "front-door")
        self.assertTrue(opening.external)
        self.assertEqual(opening.width, 900)

        removed = added.apply(RemoveExternalEntry())
        self.assertIsNone(removed.spec.external_entry)
        self.assertFalse(build_wall_plan(removed.spec, removed.result).openings)


if __name__ == "__main__":
    unittest.main()
