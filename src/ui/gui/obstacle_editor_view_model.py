"""障碍库编辑 ViewModel。注意：仅管理草稿和校验，不依赖 Qt。"""

from __future__ import annotations

import copy

from src.runner.obstacle_editor_application import (
    GeoPointInput,
    ObstacleInput,
    validate_obstacle_inputs,
)


class ObstacleEditorViewModel:
    """管理障碍编辑草稿。注意：磁盘读写由应用层回调负责。"""

    def __init__(
        self,
        obstacles: list[ObstacleInput] | tuple[ObstacleInput, ...],
        *,
        origin: GeoPointInput | None = None,
    ) -> None:
        """复制初始障碍并设置新增障碍的默认中心。"""

        self.items = [copy.deepcopy(obstacle) for obstacle in obstacles]
        self.origin = origin or GeoPointInput(0.0, 0.0)
        self.selected_index = 0 if self.items else -1
        self.dirty = False

    def add_circle(self) -> ObstacleInput:
        """新增圆形障碍并选中。"""

        obstacle = ObstacleInput(
            obstacle_id=self._next_id("C"),
            kind="circle",
            center=self.origin,
            radius_m=100.0,
        )
        return self._append(obstacle)

    def add_quadrilateral(self) -> ObstacleInput:
        """在航线原点附近新增一个小四点区域并选中。"""

        latitude = self.origin.latitude_deg
        longitude = self.origin.longitude_deg
        offset = 0.001
        obstacle = ObstacleInput(
            obstacle_id=self._next_id("R"),
            kind="rect",
            points=(
                GeoPointInput(latitude - offset, longitude - offset),
                GeoPointInput(latitude - offset, longitude + offset),
                GeoPointInput(latitude + offset, longitude + offset),
                GeoPointInput(latitude + offset, longitude - offset),
            ),
        )
        return self._append(obstacle)

    def duplicate(self, index: int) -> ObstacleInput:
        """复制指定障碍并分配同类型的新 ID。"""

        source = self.items[index]
        obstacle = copy.deepcopy(source)
        obstacle.obstacle_id = self._next_id("C" if source.kind == "circle" else "R")
        return self._append(obstacle)

    def delete(self, index: int) -> None:
        """删除指定障碍并把选择移动到相邻项。"""

        del self.items[index]
        self.selected_index = min(index, len(self.items) - 1)
        self.dirty = True

    def mark_dirty(self) -> None:
        """标记当前草稿已有用户修改。"""

        self.dirty = True

    def validate_all(self) -> dict[int, list[str]]:
        """返回整套草稿的字段与几何错误。"""

        return validate_obstacle_inputs(self.items)

    def _append(self, obstacle: ObstacleInput) -> ObstacleInput:
        """追加、选中并标记草稿变化。"""

        self.items.append(obstacle)
        self.selected_index = len(self.items) - 1
        self.dirty = True
        return obstacle

    def _next_id(self, prefix: str) -> str:
        """生成当前库内未占用的顺序 ID。"""

        occupied = {obstacle.obstacle_id.strip().casefold() for obstacle in self.items}
        sequence = 1
        while f"{prefix}{sequence}".casefold() in occupied:
            sequence += 1
        return f"{prefix}{sequence}"
