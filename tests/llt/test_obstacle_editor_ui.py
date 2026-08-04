"""障碍库编辑窗口离屏交互测试。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.runner.obstacle_editor_application import GeoPointInput, ObstacleInput, ObstacleLibraryData
from src.ui.gui.main_window import MainWindow
from src.ui.gui.obstacle_editor import ObstacleEditorDialog


class ObstacleEditorUiTests(unittest.TestCase):
    """覆盖编辑窗口的形状切换、增删和保存回调。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试进程共享的 QApplication。"""

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self) -> tuple[ObstacleEditorDialog, list[list[ObstacleInput]]]:
        """创建带保存记录器的最小窗口。"""

        saved: list[list[ObstacleInput]] = []
        library = ObstacleLibraryData(
            config_path=Path("base.json"),
            path=Path("element/obstacles.json"),
            reference="element/obstacles.json",
            obstacles=(
                ObstacleInput(
                    obstacle_id="C1",
                    kind="circle",
                    center=GeoPointInput(31.0, 121.0),
                    radius_m=100.0,
                ),
            ),
        )

        def save_handler(items: list[ObstacleInput], target: Path | None) -> ObstacleLibraryData:
            """记录保存内容并返回模拟成功结果。"""

            del target
            saved.append(items)
            return library

        dialog = ObstacleEditorDialog(
            library,
            origin=GeoPointInput(31.0, 121.0),
            save_handler=save_handler,
        )
        self.assertEqual(dialog.windowTitle(), "配置障碍区信息")
        self.assertFalse(dialog.isModal())
        self.addCleanup(dialog.close)
        return dialog, saved

    def test_add_quadrilateral_switches_detail_form(self) -> None:
        dialog, _saved = self._dialog()

        dialog.add_rect_button.click()

        self.assertEqual(dialog.vm.items[-1].kind, "rect")
        self.assertTrue(dialog.points_widget.isVisibleTo(dialog))
        self.assertFalse(dialog.circle_widget.isVisibleTo(dialog))

    def test_duplicate_id_disables_save_and_shows_validation(self) -> None:
        dialog, _saved = self._dialog()
        dialog.add_circle_button.click()
        dialog.id_edit.setText("C1")

        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn("障碍 ID 不能重复", dialog.validation_label.text())

    def test_save_uses_current_form_values(self) -> None:
        dialog, saved = self._dialog()
        dialog.id_edit.setText("CUSTOM")
        dialog.radius_spin.setValue(345.0)

        dialog.save_button.click()

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0].obstacle_id, "CUSTOM")
        self.assertEqual(saved[0][0].radius_m, 345.0)

    def test_list_checkbox_updates_detail_enabled_state(self) -> None:
        """点击左侧复选框应同步右侧默认启用字段。"""

        dialog, _saved = self._dialog()
        item = dialog.obstacle_list.item(0)
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)

        item.setCheckState(Qt.CheckState.Unchecked)

        self.assertFalse(dialog.vm.items[0].enabled)
        self.assertFalse(dialog.enabled_check.isChecked())

    def test_detail_enabled_state_updates_list_checkbox(self) -> None:
        """点击右侧默认启用应同步左侧复选框。"""

        dialog, _saved = self._dialog()
        item = dialog.obstacle_list.item(0)
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)

        dialog.enabled_check.setChecked(False)

        self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(dialog.vm.items[0].enabled)

    def test_main_window_opens_editor_and_cancel_restores_saved_obstacles(self) -> None:
        """主窗口编辑草稿应实时预览，取消后恢复当前磁盘版本。"""

        project_root = Path(__file__).resolve().parents[2]
        window = MainWindow(project_root=project_root, auto_load_config=False)
        self.addCleanup(window.close)
        window._apply_config_path(str(project_root / "configs" / "single_avoidance_80km.json"))
        original_count = len(window.obstacles)

        window.edit_obstacle_library_button.click()
        editor = window.obstacle_editor
        self.assertIsNotNone(editor)
        assert editor is not None
        self.assertFalse(editor.isModal())
        self.assertTrue(window.avoidance_window.isEnabled())
        editor.add_circle_button.click()
        self.assertEqual(len(window.obstacles), original_count + 1)

        editor.reject()

        self.assertEqual(len(window.obstacles), original_count)
        self.assertIsNone(window.obstacle_editor)


if __name__ == "__main__":
    unittest.main()
