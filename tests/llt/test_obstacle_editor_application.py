"""障碍库编辑应用层回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.runner.gui_application import GeoReference, ObstacleKind
from src.runner.obstacle_editor_application import (
    GeoPointInput,
    ObstacleInput,
    load_obstacle_library,
    obstacle_inputs_to_specs,
    save_obstacle_library,
)


class ObstacleEditorApplicationTests(unittest.TestCase):
    """覆盖原始经纬度加载、保存、另存引用和预览转换。"""

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        """创建带外部航线与障碍库的最小主配置。"""

        element = root / "element"
        element.mkdir()
        route = {
            "speed_mps": 45.0,
            "waypoints": [
                {"latitude_deg": 31.0, "longitude_deg": 121.0, "altitude_m": 1200.0},
                {"latitude_deg": 31.1, "longitude_deg": 121.1, "altitude_m": 1200.0},
            ],
        }
        obstacles = [
            {
                "id": "C1",
                "type": "circle",
                "enabled": True,
                "center": {"latitude_deg": 31.02, "longitude_deg": 121.03},
                "radius_m": 800.0,
            }
        ]
        (element / "route.json").write_text(json.dumps(route), encoding="utf-8")
        obstacle_path = element / "obstacles.json"
        obstacle_path.write_text(json.dumps(obstacles), encoding="utf-8")
        config_path = root / "base.json"
        config_path.write_text(
            json.dumps(
                {
                    "route_file": "element/route.json",
                    "avoidance": {
                        "enabled": True,
                        "obstacles_file": "element/obstacles.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        return config_path, obstacle_path

    def test_load_preserves_external_geodetic_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, obstacle_path = self._fixture(Path(tmp))

            library = load_obstacle_library(config_path)

        self.assertEqual(library.path, obstacle_path.resolve())
        self.assertEqual(library.reference, "element/obstacles.json")
        self.assertEqual(library.obstacles[0].center, GeoPointInput(31.02, 121.03))
        self.assertEqual(library.obstacles[0].radius_m, 800.0)

    def test_save_overwrites_current_library_as_geodetic_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, obstacle_path = self._fixture(Path(tmp))
            obstacles = [
                ObstacleInput(
                    obstacle_id="C9",
                    kind="circle",
                    enabled=False,
                    center=GeoPointInput(32.12345678, 122.23456789),
                    radius_m=450.0,
                )
            ]

            saved = save_obstacle_library(config_path, obstacles)
            payload = json.loads(obstacle_path.read_text(encoding="utf-8"))

        self.assertEqual(saved.path, obstacle_path.resolve())
        self.assertEqual(payload[0]["center"]["latitude_deg"], 32.1234568)
        self.assertEqual(payload[0]["center"]["longitude_deg"], 122.2345679)
        self.assertFalse(payload[0]["enabled"])

    def test_save_as_updates_main_config_with_relative_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, _obstacle_path = self._fixture(root)
            target = root / "element" / "custom_obstacles.json"
            obstacles = [
                ObstacleInput(
                    obstacle_id="R1",
                    kind="rect",
                    points=(
                        GeoPointInput(31.0, 121.0),
                        GeoPointInput(31.0, 121.1),
                        GeoPointInput(31.1, 121.1),
                        GeoPointInput(31.1, 121.0),
                    ),
                )
            ]

            saved = save_obstacle_library(config_path, obstacles, target_path=target)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            target_exists = target.is_file()

        self.assertEqual(saved.path, target.resolve())
        self.assertEqual(config["avoidance"]["obstacles_file"], "element/custom_obstacles.json")
        self.assertTrue(target_exists)

    def test_preview_conversion_uses_route_origin(self) -> None:
        obstacle = ObstacleInput(
            obstacle_id="C1",
            kind="circle",
            center=GeoPointInput(31.0, 121.0),
            radius_m=200.0,
        )

        specs = obstacle_inputs_to_specs(
            [obstacle],
            GeoReference(latitude_deg=31.0, longitude_deg=121.0),
        )

        self.assertEqual(specs[0].kind, ObstacleKind.CIRCLE)
        self.assertAlmostEqual(specs[0].center_x, 0.0, places=4)
        self.assertAlmostEqual(specs[0].center_y, 0.0, places=4)
        self.assertEqual(specs[0].radius, 200.0)

    def test_load_rejects_inline_or_missing_obstacles_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "base.json"
            config_path.write_text(json.dumps({"avoidance": {"obstacles": []}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "obstacles_file"):
                load_obstacle_library(config_path)


if __name__ == "__main__":
    unittest.main()
