"""障碍库编辑 ViewModel 回归测试。"""

from __future__ import annotations

import unittest

from src.runner.obstacle_editor_application import GeoPointInput, ObstacleInput
from src.ui.gui.obstacle_editor_view_model import ObstacleEditorViewModel


class ObstacleEditorViewModelTests(unittest.TestCase):
    """覆盖障碍草稿的增删复制、编号和整体验证。"""

    def test_add_circle_uses_route_origin_and_next_available_id(self) -> None:
        vm = ObstacleEditorViewModel(
            [
                ObstacleInput(
                    obstacle_id="C1",
                    kind="circle",
                    center=GeoPointInput(31.0, 121.0),
                    radius_m=100.0,
                )
            ],
            origin=GeoPointInput(31.2, 121.4),
        )

        added = vm.add_circle()

        self.assertEqual(added.obstacle_id, "C2")
        self.assertEqual(added.center, GeoPointInput(31.2, 121.4))
        self.assertEqual(added.radius_m, 100.0)
        self.assertTrue(vm.dirty)

    def test_add_quadrilateral_builds_small_valid_box_around_origin(self) -> None:
        vm = ObstacleEditorViewModel([], origin=GeoPointInput(31.0, 121.0))

        added = vm.add_quadrilateral()

        self.assertEqual(added.obstacle_id, "R1")
        self.assertEqual(added.kind, "rect")
        self.assertEqual(len(added.points), 4)
        self.assertEqual(vm.validate_all(), {})

    def test_duplicate_allocates_new_id_and_delete_updates_selection(self) -> None:
        vm = ObstacleEditorViewModel(
            [
                ObstacleInput(
                    obstacle_id="C1",
                    kind="circle",
                    center=GeoPointInput(31.0, 121.0),
                    radius_m=100.0,
                )
            ]
        )

        copied = vm.duplicate(0)
        vm.delete(0)

        self.assertEqual(copied.obstacle_id, "C2")
        self.assertEqual([item.obstacle_id for item in vm.items], ["C2"])
        self.assertEqual(vm.selected_index, 0)

    def test_validate_all_reports_duplicate_id_and_invalid_circle(self) -> None:
        vm = ObstacleEditorViewModel(
            [
                ObstacleInput(
                    obstacle_id="C1",
                    kind="circle",
                    center=GeoPointInput(31.0, 121.0),
                    radius_m=0.0,
                ),
                ObstacleInput(
                    obstacle_id="C1",
                    kind="circle",
                    center=GeoPointInput(31.0, 121.0),
                    radius_m=20.0,
                ),
            ]
        )

        errors = vm.validate_all()

        self.assertIn("障碍 ID 不能重复", errors[0])
        self.assertIn("半径必须大于 0", errors[0])
        self.assertIn("障碍 ID 不能重复", errors[1])

    def test_validate_all_rejects_self_intersecting_quadrilateral(self) -> None:
        vm = ObstacleEditorViewModel(
            [
                ObstacleInput(
                    obstacle_id="R1",
                    kind="rect",
                    points=(
                        GeoPointInput(31.0, 121.0),
                        GeoPointInput(31.1, 121.1),
                        GeoPointInput(31.0, 121.1),
                        GeoPointInput(31.1, 121.0),
                    ),
                )
            ]
        )

        self.assertIn("边界不能自相交", vm.validate_all()[0])


if __name__ == "__main__":
    unittest.main()
