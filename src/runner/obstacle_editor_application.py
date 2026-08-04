"""障碍库编辑应用层。注意：保存外部经纬度，不把 GUI 使用的 ENU 坐标直接写回文件。"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from src.data.geo import GeoOrigin, geodetic_to_enu
from src.data.geo_config import format_geodetic_degree
from src.data.obstaclefile import ObstacleFileManager
from src.runner.gui_application import GeoReference, ObstacleKind, ObstacleSpec

_OBSTACLE_FILE_MANAGER = ObstacleFileManager()


@dataclass(frozen=True)
class GeoPointInput:
    """界面可编辑的经纬度点。注意：字段顺序固定为纬度、经度。"""

    latitude_deg: float
    longitude_deg: float


@dataclass
class ObstacleInput:
    """单个外部障碍草稿。注意：circle 使用 center，rect 使用四个 points。"""

    obstacle_id: str
    kind: str
    enabled: bool = True
    center: GeoPointInput | None = None
    radius_m: float = 0.0
    points: tuple[GeoPointInput, ...] = ()
    # 未识别字段原样保留，避免界面保存时丢失客户附加元数据。
    extra_fields: dict[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ObstacleLibraryData:
    """障碍库编辑快照。注意：path 是实际文件，reference 是主配置中的原始引用。"""

    config_path: Path
    path: Path
    reference: str
    obstacles: tuple[ObstacleInput, ...]


def load_obstacle_library(config_path: str | Path) -> ObstacleLibraryData:
    """读取主配置引用的原始障碍库。注意：只接受 JSON 主配置和外部 obstacles_file。"""

    resolved_config = Path(config_path).resolve()
    data = _read_main_config(resolved_config)
    avoidance = data.get("avoidance")
    if not isinstance(avoidance, dict):
        raise ValueError("当前配置没有 avoidance 对象")
    reference = avoidance.get("obstacles_file")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("当前配置没有可编辑的 avoidance.obstacles_file")
    path = _OBSTACLE_FILE_MANAGER.resolve_path(resolved_config, reference).resolve()
    raw_obstacles = _OBSTACLE_FILE_MANAGER.load_obstacles(resolved_config, reference)
    obstacles = tuple(_obstacle_from_raw(raw, index) for index, raw in enumerate(raw_obstacles))
    return ObstacleLibraryData(resolved_config, path, reference, obstacles)


def save_obstacle_library(
    config_path: str | Path,
    obstacles: list[ObstacleInput] | tuple[ObstacleInput, ...],
    *,
    target_path: str | Path | None = None,
) -> ObstacleLibraryData:
    """保存障碍库；另存时同步更新主配置引用。注意：校验失败不会修改磁盘。"""

    current = load_obstacle_library(config_path)
    normalized = [copy.deepcopy(obstacle) for obstacle in obstacles]
    errors = validate_obstacle_inputs(normalized)
    if errors:
        first_index = min(errors)
        raise ValueError(f"第 {first_index + 1} 个障碍：{'；'.join(errors[first_index])}")

    destination = current.path if target_path is None else Path(target_path).resolve()
    if destination.suffix.lower() != ".json":
        raise ValueError("障碍库只支持保存为 .json 文件")
    payload = [_obstacle_to_raw(obstacle) for obstacle in normalized]
    _OBSTACLE_FILE_MANAGER.save_obstacles(current.config_path, str(destination), payload)

    reference = current.reference
    if destination != current.path:
        # 优先写相对路径以保持工程可迁移；跨盘时退回绝对路径。
        reference = _portable_reference(current.config_path.parent, destination)
        data = _read_main_config(current.config_path)
        avoidance = data.get("avoidance")
        if not isinstance(avoidance, dict):
            raise ValueError("当前配置没有 avoidance 对象")
        avoidance["obstacles_file"] = reference
        _write_json_atomic(current.config_path, data)
    return ObstacleLibraryData(current.config_path, destination, reference, tuple(normalized))


def validate_obstacle_inputs(
    obstacles: list[ObstacleInput] | tuple[ObstacleInput, ...],
) -> dict[int, list[str]]:
    """校验整套障碍草稿并按下标返回错误。注意：重复 ID 会标记所有冲突项。"""

    errors: dict[int, list[str]] = {}
    ids: dict[str, list[int]] = {}
    for index, obstacle in enumerate(obstacles):
        item_errors = _validate_obstacle(obstacle)
        if item_errors:
            errors[index] = item_errors
        normalized_id = obstacle.obstacle_id.strip().casefold()
        if normalized_id:
            ids.setdefault(normalized_id, []).append(index)
    for indexes in ids.values():
        if len(indexes) <= 1:
            continue
        for index in indexes:
            errors.setdefault(index, []).insert(0, "障碍 ID 不能重复")
    return errors


def obstacle_inputs_to_specs(
    obstacles: list[ObstacleInput] | tuple[ObstacleInput, ...],
    origin: GeoReference | None,
) -> list[ObstacleSpec]:
    """把经纬度草稿转成 GUI 预览使用的 ENU 障碍。注意：不写文件。"""

    errors = validate_obstacle_inputs(obstacles)
    if errors:
        first_index = min(errors)
        raise ValueError(f"第 {first_index + 1} 个障碍：{'；'.join(errors[first_index])}")
    if origin is None:
        raise ValueError("当前航线没有经纬度原点，无法预览经纬度障碍")
    geo_origin = GeoOrigin(origin.latitude_deg, origin.longitude_deg)
    specs: list[ObstacleSpec] = []
    for obstacle in obstacles:
        if obstacle.kind == ObstacleKind.CIRCLE:
            assert obstacle.center is not None
            east, north = geodetic_to_enu(
                obstacle.center.latitude_deg,
                obstacle.center.longitude_deg,
                geo_origin,
            )
            specs.append(
                ObstacleSpec(
                    obstacle.obstacle_id,
                    ObstacleKind.CIRCLE,
                    obstacle.enabled,
                    center_x=east,
                    center_y=north,
                    radius=obstacle.radius_m,
                )
            )
            continue
        vertices = tuple(
            geodetic_to_enu(point.latitude_deg, point.longitude_deg, geo_origin)
            for point in obstacle.points
        )
        specs.append(
            ObstacleSpec(
                obstacle.obstacle_id,
                ObstacleKind.POLYGON,
                obstacle.enabled,
                vertices=vertices,
            )
        )
    return specs


def _read_main_config(path: Path) -> dict[str, object]:
    """读取 JSON 主配置。注意：障碍编辑器不修改 YAML 配置。"""

    if path.suffix.lower() != ".json":
        raise ValueError("障碍库编辑器当前只支持 JSON 主配置")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("主配置必须是 JSON 对象")
    return data


def _obstacle_from_raw(raw: object, index: int) -> ObstacleInput:
    """把障碍文件条目转成强类型草稿。注意：编辑器只接收经纬度 circle/rect。"""

    if not isinstance(raw, dict):
        raise ValueError(f"obstacles[{index}] 必须是对象")
    kind = str(raw.get("type", ""))
    obstacle_id = str(raw.get("id", "")).strip()
    enabled = bool(raw.get("enabled", True))
    if kind == ObstacleKind.CIRCLE:
        center = _point_from_raw(raw.get("center"), f"obstacles[{index}].center")
        radius_m = _finite_float(raw.get("radius_m"), f"obstacles[{index}].radius_m")
        extras = _extra_fields(raw, {"id", "type", "enabled", "center", "radius_m"})
        return ObstacleInput(obstacle_id, kind, enabled, center, radius_m, extra_fields=extras)
    if kind == ObstacleKind.RECT:
        raw_points = raw.get("points")
        if not isinstance(raw_points, list):
            raise ValueError(f"obstacles[{index}].points 必须是数组")
        points = tuple(
            _point_from_raw(point, f"obstacles[{index}].points[{point_index}]")
            for point_index, point in enumerate(raw_points)
        )
        extras = _extra_fields(raw, {"id", "type", "enabled", "points"})
        return ObstacleInput(obstacle_id, kind, enabled, points=points, extra_fields=extras)
    raise ValueError(f"obstacles[{index}].type 仅支持 circle 或 rect")


def _obstacle_to_raw(obstacle: ObstacleInput) -> dict[str, object]:
    """把强类型草稿序列化为现有障碍 JSON 契约。"""

    raw = copy.deepcopy(obstacle.extra_fields)
    raw.update({"id": obstacle.obstacle_id.strip(), "type": obstacle.kind, "enabled": obstacle.enabled})
    if obstacle.kind == ObstacleKind.CIRCLE:
        assert obstacle.center is not None
        raw["radius_m"] = float(obstacle.radius_m)
        raw["center"] = _point_to_raw(obstacle.center)
    else:
        raw["points"] = [_point_to_raw(point) for point in obstacle.points]
    return raw


def _point_from_raw(raw: object, field_name: str) -> GeoPointInput:
    """读取经纬度点。注意：编辑器拒绝内部 ENU，防止保存时坐标语义漂移。"""

    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} 必须是经纬度对象")
    latitude = _finite_float(raw.get("latitude_deg"), f"{field_name}.latitude_deg")
    longitude = _finite_float(raw.get("longitude_deg"), f"{field_name}.longitude_deg")
    return GeoPointInput(latitude, longitude)


def _point_to_raw(point: GeoPointInput) -> dict[str, float]:
    """按项目统一精度输出经纬度点。"""

    return {
        "latitude_deg": format_geodetic_degree(point.latitude_deg),
        "longitude_deg": format_geodetic_degree(point.longitude_deg),
    }


def _finite_float(value: object, field_name: str) -> float:
    """读取有限浮点数。注意：NaN 和无穷大均视为非法输入。"""

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 必须是有限数字")
    return number


def _validate_obstacle(obstacle: ObstacleInput) -> list[str]:
    """校验单个障碍的字段与几何。"""

    errors: list[str] = []
    if not obstacle.obstacle_id.strip():
        errors.append("障碍 ID 不能为空")
    if obstacle.kind == ObstacleKind.CIRCLE:
        if obstacle.center is None:
            errors.append("圆形障碍必须提供中心点")
        elif point_error := _validate_point(obstacle.center):
            errors.extend(point_error)
        if not math.isfinite(obstacle.radius_m) or obstacle.radius_m <= 0.0:
            errors.append("半径必须大于 0")
        return errors
    if obstacle.kind != ObstacleKind.RECT:
        errors.append("障碍类型仅支持圆形或四点区域")
        return errors
    if len(obstacle.points) != 4:
        errors.append("四点区域必须恰好包含 4 个顶点")
        return errors
    for point in obstacle.points:
        errors.extend(_validate_point(point))
    if errors:
        return errors
    xy = [(point.longitude_deg, point.latitude_deg) for point in obstacle.points]
    if len(set(xy)) != 4:
        errors.append("四个顶点不能重复")
    if _segments_intersect(xy[0], xy[1], xy[2], xy[3]) or _segments_intersect(
        xy[1], xy[2], xy[3], xy[0]
    ):
        errors.append("边界不能自相交")
    area_twice = sum(
        xy[index][0] * xy[(index + 1) % 4][1] - xy[(index + 1) % 4][0] * xy[index][1]
        for index in range(4)
    )
    if abs(area_twice) <= 1e-14:
        errors.append("四点区域面积必须大于 0")
    return errors


def _validate_point(point: GeoPointInput) -> list[str]:
    """校验一个经纬度点。"""

    errors: list[str] = []
    if not math.isfinite(point.latitude_deg) or not -90.0 <= point.latitude_deg <= 90.0:
        errors.append("纬度必须在 -90 到 90 之间")
    if not math.isfinite(point.longitude_deg) or not -180.0 <= point.longitude_deg <= 180.0:
        errors.append("经度必须在 -180 到 180 之间")
    return errors


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """判断两条不共享端点的线段是否相交。注意：共线重叠也视为非法。"""

    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        """返回三点叉积符号。"""

        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    ab_c = orientation(a, b, c)
    ab_d = orientation(a, b, d)
    cd_a = orientation(c, d, a)
    cd_b = orientation(c, d, b)
    epsilon = 1e-14
    if abs(ab_c) <= epsilon or abs(ab_d) <= epsilon or abs(cd_a) <= epsilon or abs(cd_b) <= epsilon:
        return True
    return (ab_c > 0.0) != (ab_d > 0.0) and (cd_a > 0.0) != (cd_b > 0.0)


def _extra_fields(raw: dict[str, object], standard_keys: set[str]) -> dict[str, object]:
    """复制障碍条目的客户扩展字段。"""

    return {key: copy.deepcopy(value) for key, value in raw.items() if key not in standard_keys}


def _portable_reference(config_dir: Path, destination: Path) -> str:
    """生成可迁移的障碍文件引用。注意：跨盘符时保留绝对路径。"""

    try:
        relative = os.path.relpath(destination, config_dir)
    except ValueError:
        return destination.as_posix()
    return Path(relative).as_posix()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """原子写入 JSON 对象。注意：临时文件与目标同目录，保证 replace 不跨卷。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
