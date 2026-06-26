"""离线控制效果分析窗口。"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class AnalysisChannel:
    """单个控制误差分析通道。注意：field_name 必须对应 snapshots.jsonl 的节点字段。"""

    # 内部稳定键，用于缓冲区索引和控件 objectName。
    key: str
    # 展示名称直接进入表格、单选框和导出 CSV。
    label: str
    # 单位目前只保留给后续图表标题和导出扩展。
    unit: str
    # snapshots.jsonl 节点对象中的字段名。
    field_name: str


@dataclass(frozen=True)
class MetricSummary:
    """某个样本集合的统计结果。注意：variance 使用总体方差。"""

    # 有效样本数量，导出时用于判断指标可信度。
    count: int
    # 有符号均值，可观察误差是否长期偏向某一侧。
    mean: float
    # 总体方差，阶段二已明确不使用样本方差。
    variance: float
    # 标准差，和方差表达同一离散程度但量纲与原误差一致。
    std: float
    # RMS 反映综合误差能量，对零均值误差近似等于标准差。
    rms: float
    # 最大绝对误差，用于快速定位最坏情况。
    max_abs: float
    # 最大绝对误差首次出现的仿真时刻。
    max_abs_time_s: float


@dataclass
class SourceData:
    """单个输入文件解析后的内存数据。注意：samples 按 node_id 和通道 key 分组。"""

    # A 或 B，用于表格左右位置、图例和导出来源。
    label: str
    # 原始文件路径，导出时原样保留。
    path: Path | None = None
    # 是否参与当前表格、曲线和导出。
    enabled: bool = False
    # 解析错误只影响当前输入源，不影响另一份文件。
    error: str = ""
    # node_id -> channel_key -> [(time_s, value)]。
    samples: dict[str, dict[str, list[tuple[float, float]]]] = field(default_factory=dict)
    # 文件内最早仿真时刻，用于自动填充时间段。
    t_min: float | None = None
    # 文件内最晚仿真时刻，用于自动填充时间段。
    t_max: float | None = None


@dataclass(frozen=True)
class CurveSpec:
    """一条滑动窗口曲线的绘制参数。注意：points 为窗口起点时刻和统计值。"""

    # 图例名称，固定为 A 或 B。
    name: str
    # 曲线颜色，A/B 使用不同主色。
    color: str
    # B 使用虚线，避免只靠颜色区分。
    dashed: bool
    # 曲线点为窗口锚点时间和对应指标值。
    points: list[tuple[float, float]]


CHANNELS: tuple[AnalysisChannel, ...] = (
    # 位置误差三个轴按航迹坐标系 x/y/z 顺序展示。
    AnalysisChannel("pos_x", "前向位置误差 x", "m", "track_pos_err_x_m"),
    AnalysisChannel("pos_y", "垂向位置误差 y", "m", "track_pos_err_y_m"),
    AnalysisChannel("pos_z", "侧向位置误差 z", "m", "track_pos_err_z_m"),
    # 速度误差三个轴保持同样顺序，便于和位置误差对照。
    AnalysisChannel("vel_x", "前向速度误差 x", "m/s", "track_vel_err_x_mps"),
    AnalysisChannel("vel_y", "垂向速度误差 y", "m/s", "track_vel_err_y_mps"),
    AnalysisChannel("vel_z", "侧向速度误差 z", "m/s", "track_vel_err_z_mps"),
)
METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    # 表格列名统一用中文，导出字段名另保留英文。
    ("mean", "均值"),
    ("variance", "方差"),
    ("std", "标准差"),
    ("rms", "RMS"),
    ("max_abs", "最大绝对值"),
    ("max_abs_time_s", "发生时刻(s)"),
)
WINDOW_METRICS: tuple[tuple[str, str, str], ...] = (
    # 四个窗口指标各自独立 Y 轴，避免量级差异压扁曲线。
    ("mean", "窗口均值", "#2563eb"),
    ("std", "窗口标准差", "#f97316"),
    ("rms", "窗口 RMS", "#7c3aed"),
    ("max_abs", "窗口最大绝对值", "#dc2626"),
)
# A/B 颜色不复用主界面状态色，避免和健康/告警语义混淆。
SOURCE_COLORS = {"A": "#2563eb", "B": "#dc2626"}
# 默认窗口宽度来自阶段二草图，用于空态和首次加载。
DEFAULT_WINDOW_S = 5.0
# X 轴右侧留白，避免末尾点贴住边框。
X_MARGIN_S = 0.5

# 本窗口先做阶段三 UI 工作台，同时带最小可用分析内核。
# 输入契约只认 snapshots.jsonl，不读取 events/config，也不解释扰动原因。
# A/B 是并列输入源；差值、比值和结论判断留给后续对比阶段。
# 汇总表固定六个通道，绘图通道单选不改变表格和导出内容。
# 表格单元格保留 “A | B” 的视觉结构，禁用的一侧显示为空。
# all 表示把所有飞机的同一通道样本合并后统计。
# 单机对象只使用该 node_id 样本，不做跨飞机补齐。
# 时间段过滤使用闭区间，结束时刻样本会参与统计。
# 滑动窗口以真实采样时刻为锚点，不插值生成额外点。
# 窗口内部使用左闭右开区间，减少边界重复计入。
# 导出只写汇总指标，不写滑动窗口曲线。
# 弹窗只镜像主窗口当前状态，不保存独立分析参数。


class ChartPopupDialog(QDialog):
    """只显示滑动窗口图表的弹窗。注意：数据由主窗口刷新时同步推送。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化弹窗容器，等待主窗口填充四个图表。"""
        super().__init__(parent)
        self.setWindowTitle("滑动窗口曲线")
        self.resize(980, 640)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(10, 10, 10, 10)
        self._grid.setSpacing(10)

    def set_curves(
        self,
        curves_by_metric: dict[str, list[CurveSpec]],
        x_range: tuple[float, float],
        channel_label: str,
    ) -> None:
        """用最新曲线数据重建弹窗图表。"""
        # 弹窗跟随主窗口刷新，直接重建四图能避免残留旧曲线。
        _clear_layout(self._grid)
        for index, (metric_key, title, color) in enumerate(WINDOW_METRICS):
            view = _make_chart_view(
                f"{title} - {channel_label}",
                curves_by_metric.get(metric_key, []),
                x_range,
                color,
            )
            self._grid.addWidget(view, index // 2, index % 2)


class DataAnalysisWindow(QDialog):
    """离线控制效果分析窗口。注意：只消费 snapshots.jsonl，不依赖仿真控制器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """初始化输入源、控件引用和界面布局。"""
        super().__init__(parent)
        self.setWindowTitle("离线控制效果分析")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 680)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._sources = {
            "A": SourceData("A", enabled=True),
            "B": SourceData("B", enabled=False),
        }
        self._source_checks: dict[str, QCheckBox] = {}
        self._path_labels: dict[str, QLabel] = {}
        self._target_combo: QComboBox
        self._start_input: QDoubleSpinBox
        self._end_input: QDoubleSpinBox
        self._window_input: QDoubleSpinBox
        self._summary_table: QTableWidget
        self._channel_buttons: dict[str, QRadioButton] = {}
        self._channel_group: QButtonGroup
        self._chart_grid: QGridLayout
        self._status_label: QLabel
        self._popup: ChartPopupDialog | None = None
        # 初始化期间控件会设置默认值，先屏蔽 valueChanged 触发的刷新。
        self._refreshing = True

        self._build_ui()
        self._refreshing = False
        # 构造完成后做一次空态刷新，让表格和图表占位立即可见。
        self._refresh_all(reset_time=True)

    @property
    def summary_table(self) -> QTableWidget:
        """返回汇总指标表，供测试和后续窗口集成复用。"""
        return self._summary_table

    def _build_ui(self) -> None:
        """构建上中下三段式界面。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addWidget(self._build_top_panel())
        root.addWidget(self._build_summary_panel(), stretch=2)
        root.addWidget(self._build_bottom_panel(), stretch=2)

    def _build_top_panel(self) -> QWidget:
        """构建文件 A/B、对象、时间段和窗口宽度控制区。"""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QGridLayout(panel)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(6)

        for row, label in enumerate(("A", "B")):
            enabled = label == "A"
            cb = QCheckBox(f"文件 {label}")
            cb.setChecked(enabled)
            # 启用状态只控制参与分析，不会清空已经加载的文件数据。
            cb.toggled.connect(lambda checked, source_label=label: self._set_source_enabled(source_label, checked))
            self._source_checks[label] = cb
            path_label = QLabel("未选择 snapshots.jsonl")
            path_label.setObjectName(f"offlinePath{label}")
            path_label.setMinimumWidth(320)
            path_label.setStyleSheet("color: #475569;")
            button = QPushButton("选择文件")
            button.clicked.connect(lambda _=False, source_label=label: self._choose_file(source_label))
            self._path_labels[label] = path_label

            lay.addWidget(cb, row, 0)
            lay.addWidget(path_label, row, 1, 1, 5)
            lay.addWidget(button, row, 6)

        lay.addWidget(QLabel("对象"), 0, 7)
        self._target_combo = QComboBox()
        self._target_combo.setObjectName("offlineTargetCombo")
        # all 是固定对象，具体飞机编号由加载文件动态补齐。
        self._target_combo.addItem("all")
        self._target_combo.currentTextChanged.connect(lambda _text: self._refresh_all())
        lay.addWidget(self._target_combo, 0, 8)

        lay.addWidget(QLabel("开始 s"), 0, 9)
        # 开始/结束时间与窗口宽度都用数值控件，减少非法字符串处理。
        self._start_input = self._make_time_input("offlineStartInput")
        lay.addWidget(self._start_input, 0, 10)

        lay.addWidget(QLabel("结束 s"), 1, 7)
        self._end_input = self._make_time_input("offlineEndInput")
        self._end_input.setValue(120.0)
        lay.addWidget(self._end_input, 1, 8)

        lay.addWidget(QLabel("窗口 s"), 1, 9)
        self._window_input = self._make_time_input("offlineWindowInput")
        # 窗口宽度必须为正，否则窗口统计没有明确含义。
        self._window_input.setMinimum(0.001)
        self._window_input.setValue(DEFAULT_WINDOW_S)
        lay.addWidget(self._window_input, 1, 10)

        for column in range(11):
            lay.setColumnStretch(column, 0)
        lay.setColumnStretch(1, 1)
        return panel

    def _make_time_input(self, object_name: str) -> QDoubleSpinBox:
        """创建秒单位数值输入框。"""
        spin = QDoubleSpinBox()
        spin.setObjectName(object_name)
        spin.setRange(0.0, 1_000_000.0)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        # 键入过程中不连续刷新，回车或失焦后再重新分析。
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda _value: self._refresh_all())
        return spin

    def _build_summary_panel(self) -> QWidget:
        """构建汇总指标表区域。"""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("汇总指标")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        export_btn = QPushButton("导出全部指标")
        export_btn.setObjectName("offlineExportAllButton")
        export_btn.clicked.connect(self._export_all_metrics)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(export_btn)
        lay.addLayout(header)

        self._summary_table = QTableWidget(len(CHANNELS), 1 + len(METRIC_COLUMNS))
        self._summary_table.setObjectName("offlineSummaryTable")
        self._summary_table.setHorizontalHeaderLabels(["通道"] + [name for _key, name in METRIC_COLUMNS])
        self._summary_table.verticalHeader().setVisible(False)
        # 汇总表是结果视图，不允许用户直接编辑单元格。
        self._summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._summary_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._summary_table.setAlternatingRowColors(True)
        self._summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, 1 + len(METRIC_COLUMNS)):
            # 指标列等宽拉伸，给 A|B 双值留出空间。
            self._summary_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self._summary_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._summary_table, stretch=1)
        return panel

    def _build_bottom_panel(self) -> QWidget:
        """构建绘图通道选择区和四个滑动窗口曲线区。"""
        panel = QWidget()
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self._build_channel_panel())
        lay.addWidget(self._build_chart_panel(), stretch=1)
        return panel

    def _build_channel_panel(self) -> QWidget:
        """构建左侧绘图通道单选列表。"""
        box = QGroupBox("绘图通道")
        box.setFixedWidth(230)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)
        self._channel_group = QButtonGroup(self)
        self._channel_group.setExclusive(True)
        for index, channel in enumerate(CHANNELS):
            button = QRadioButton(channel.label)
            button.setObjectName(f"offlineChannel_{channel.key}")
            # 默认绘制第一个通道；汇总表仍显示全部通道。
            button.setChecked(index == 0)
            button.toggled.connect(lambda checked: self._refresh_all() if checked else None)
            self._channel_buttons[channel.key] = button
            self._channel_group.addButton(button)
            lay.addWidget(button)
        lay.addStretch()
        return box

    def _build_chart_panel(self) -> QWidget:
        """构建右侧滑动窗口曲线面板。"""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("滑动窗口曲线")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        popup_btn = QPushButton("□")
        popup_btn.setObjectName("offlineChartPopupButton")
        popup_btn.setFixedSize(28, 28)
        # 符号按钮贴近草图，含义通过 tooltip 说明。
        popup_btn.setToolTip("弹出图表窗口")
        popup_btn.clicked.connect(self._open_chart_popup)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #64748b;")
        header.addWidget(title)
        header.addWidget(popup_btn)
        header.addStretch()
        # 右上角显示当前绘图通道，和左侧单选列表形成确认。
        header.addWidget(self._status_label)
        lay.addLayout(header)

        chart_host = QWidget()
        self._chart_grid = QGridLayout(chart_host)
        self._chart_grid.setContentsMargins(0, 0, 0, 0)
        self._chart_grid.setSpacing(10)
        # 四张图固定为 2x2 等权布局，避免右列被图表 sizeHint 挤压。
        self._chart_grid.setColumnStretch(0, 1)
        self._chart_grid.setColumnStretch(1, 1)
        self._chart_grid.setRowStretch(0, 1)
        self._chart_grid.setRowStretch(1, 1)
        lay.addWidget(chart_host, stretch=1)
        return panel

    def _set_source_enabled(self, label: str, enabled: bool) -> None:
        """更新输入文件启用状态，并立即刷新表格和曲线。"""
        self._sources[label].enabled = enabled
        # 启用源改变会影响默认时间段，因此允许同步范围。
        self._refresh_all(reset_time=True)

    def _choose_file(self, label: str) -> None:
        """弹出文件选择框并加载指定 A/B 输入文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"选择文件 {label}",
            "dist/logs",
            "快照文件 (snapshots.jsonl *.jsonl)",
        )
        if path:
            self._load_file(label, path)

    def _load_file(self, label: str, path: str | Path) -> None:
        """加载指定输入文件，并在解析完成后刷新界面。"""
        source = self._sources[label]
        # 保留文件启用勾选，只替换对应输入源的样本。
        source.path = Path(path)
        source.error = ""
        source.samples.clear()
        source.t_min = None
        source.t_max = None
        try:
            self._parse_snapshot_file(source)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            # 错误挂在当前输入源上，另一份文件仍可继续分析。
            source.error = str(exc)
        self._update_path_label(label)
        self._refresh_all(reset_time=True)

    def _parse_snapshot_file(self, source: SourceData) -> None:
        """解析 snapshots.jsonl，将六个误差通道写入 source.samples。"""
        if source.path is None:
            return
        times: list[float] = []
        with source.path.open(encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, start=1):
                stripped = raw.strip()
                if not stripped:
                    # 允许日志末尾或人工编辑后留下空行。
                    continue
                record = json.loads(stripped)
                t = float(record.get("time_s", 0.0))
                times.append(t)
                nodes = record.get("nodes")
                if not isinstance(nodes, list):
                    raise ValueError(f"第 {line_no} 行缺少 nodes 列表")
                for node in nodes:
                    # 每个节点独立入库，all 场景在查询时再合并。
                    self._append_node_samples(source, t, node, line_no)
        if not times:
            raise ValueError("文件为空")
        # 时间范围按所有非空记录计算，不要求每帧都包含同一批节点。
        source.t_min = min(times)
        source.t_max = max(times)

    def _append_node_samples(self, source: SourceData, t: float, node: object, line_no: int) -> None:
        """把单个节点的一帧数据追加到样本缓冲。"""
        if not isinstance(node, dict):
            raise ValueError(f"第 {line_no} 行存在非对象节点")
        node_id = str(node.get("node_id", "")).strip()
        if not node_id:
            raise ValueError(f"第 {line_no} 行存在空 node_id")
        node_samples = source.samples.setdefault(node_id, {})
        for channel in CHANNELS:
            # 老日志缺少新误差字段时按控制器默认值 0 处理。
            raw_value = node.get(channel.field_name, 0.0)
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                raise ValueError(f"第 {line_no} 行 {node_id}/{channel.label} 不是数值") from None
            node_samples.setdefault(channel.key, []).append((t, value))

    def _update_path_label(self, label: str) -> None:
        """刷新某个输入源的文件名显示和错误提示。"""
        source = self._sources[label]
        path_label = self._path_labels[label]
        if source.path is None:
            path_label.setText("未选择 snapshots.jsonl")
            # 未选择文件时清空 tooltip，避免残留上一次路径。
            path_label.setToolTip("")
            return
        prefix = source.path.name
        if source.error:
            # 顶栏只展示短状态，详细异常放在 tooltip，避免撑坏布局。
            path_label.setText(f"{prefix} - 加载失败")
            path_label.setToolTip(source.error)
            path_label.setStyleSheet("color: #dc2626;")
        else:
            path_label.setText(prefix)
            path_label.setToolTip(str(source.path))
            path_label.setStyleSheet("color: #475569;")

    def _refresh_all(self, *, reset_time: bool = False) -> None:
        """统一刷新对象列表、时间范围、汇总表、窗口曲线和弹窗。"""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            # 顺序很重要：对象和时间范围会影响后续表格与曲线。
            self._refresh_target_options()
            if reset_time:
                self._reset_time_range_from_sources()
            self._refresh_summary_table()
            self._refresh_window_charts()
        finally:
            self._refreshing = False

    def _refresh_target_options(self) -> None:
        """根据已加载文件刷新对象下拉框，保留当前选择。"""
        current = self._target_combo.currentText() or "all"
        # 下拉项取已加载文件并集，B 未启用时也能提前选择对象。
        node_ids = sorted({node_id for source in self._sources.values() for node_id in source.samples})
        options = ["all", *node_ids]
        if current not in options:
            current = "all"
        with QSignalBlocker(self._target_combo):
            # 阻塞信号，避免重填下拉框时递归触发表格刷新。
            self._target_combo.clear()
            self._target_combo.addItems(options)
            self._target_combo.setCurrentText(current)

    def _reset_time_range_from_sources(self) -> None:
        """按启用且已加载的文件重置分析时间段输入。"""
        loaded = [source for source in self._enabled_sources() if source.t_min is not None and source.t_max is not None]
        if not loaded:
            # 没有启用文件时仍用已加载文件给出可见时间范围。
            loaded = [source for source in self._sources.values() if source.t_min is not None and source.t_max is not None]
        if not loaded:
            return
        t_min = min(source.t_min for source in loaded if source.t_min is not None)
        t_max = max(source.t_max for source in loaded if source.t_max is not None)
        with QSignalBlocker(self._start_input), QSignalBlocker(self._end_input):
            # 自动填值本身不应再触发一次完整分析。
            self._start_input.setValue(t_min)
            self._end_input.setValue(t_max)

    def _refresh_summary_table(self) -> None:
        """重新计算并填充六通道汇总指标表。"""
        target = self._target_combo.currentText() or "all"
        start_s, end_s = self._time_range()
        for row, channel in enumerate(CHANNELS):
            self._set_table_item(row, 0, channel.label)
            for column, (metric_key, _metric_name) in enumerate(METRIC_COLUMNS, start=1):
                values = []
                for source_label in ("A", "B"):
                    # 分别计算 A/B，再拼成 “左 | 右”。
                    summary = self._summary_for(self._sources[source_label], target, channel, start_s, end_s)
                    values.append(self._format_metric(summary, metric_key))
                self._set_table_item(row, column, f"{values[0]} | {values[1]}".strip())
        self._summary_table.resizeRowsToContents()

    def _set_table_item(self, row: int, column: int, text: str) -> None:
        """设置表格单元格文本并统一居中。"""
        item = QTableWidgetItem(text)
        # 所有数值居中，A|B 双值在表格里更容易扫读。
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._summary_table.setItem(row, column, item)

    def _summary_for(
        self,
        source: SourceData,
        target: str,
        channel: AnalysisChannel,
        start_s: float,
        end_s: float,
    ) -> MetricSummary | None:
        """计算某个输入源、对象和通道的时间段汇总指标。"""
        if not source.enabled or source.error or not source.samples:
            # 禁用、加载失败和未加载在表格上都显示为空位。
            return None
        points = self._points_for(source, target, channel.key, start_s, end_s)
        return _calc_summary(points)

    def _format_metric(self, summary: MetricSummary | None, metric_key: str) -> str:
        """格式化单个指标；禁用、未加载或无样本时显示空位。"""
        if summary is None or summary.count == 0:
            return ""
        value = getattr(summary, metric_key)
        # 发生时刻保留三位小数，便于定位具体问题帧。
        return f"{value:.3f}" if metric_key == "max_abs_time_s" else f"{value:.2f}"

    def _refresh_window_charts(self) -> None:
        """按当前绘图通道重建四个滑动窗口曲线。"""
        channel = self._selected_channel()
        curves_by_metric = self._window_curves(channel)
        x_range = self._chart_x_range(curves_by_metric)
        # 主图区直接重建，确保启用状态、虚线样式和坐标轴同步。
        _clear_layout(self._chart_grid)
        for index, (metric_key, title, color) in enumerate(WINDOW_METRICS):
            view = _make_chart_view(title, curves_by_metric.get(metric_key, []), x_range, color)
            self._chart_grid.addWidget(view, index // 2, index % 2)
        self._status_label.setText(channel.label)
        if self._popup is not None:
            # 弹窗打开时始终镜像主窗口当前通道和数据。
            self._popup.set_curves(curves_by_metric, x_range, channel.label)

    def _selected_channel(self) -> AnalysisChannel:
        """返回左侧单选列表当前选中的绘图通道。"""
        for channel in CHANNELS:
            if self._channel_buttons[channel.key].isChecked():
                return channel
        # 理论上不会发生；保底返回首通道，避免空选择导致刷新失败。
        return CHANNELS[0]

    def _window_curves(self, channel: AnalysisChannel) -> dict[str, list[CurveSpec]]:
        """计算当前通道的四类滑动窗口曲线数据。"""
        target = self._target_combo.currentText() or "all"
        start_s, end_s = self._time_range()
        window_s = self._window_input.value()
        curves_by_metric: dict[str, list[CurveSpec]] = {metric_key: [] for metric_key, _title, _color in WINDOW_METRICS}
        for source_label in ("A", "B"):
            source = self._sources[source_label]
            if not source.enabled or source.error or not source.samples:
                continue
            points = self._points_for(source, target, channel.key, start_s, end_s)
            # 每个启用文件各自产生一组窗口指标曲线。
            metrics = _sliding_window(points, start_s, end_s, window_s)
            for metric_key, _title, _color in WINDOW_METRICS:
                curve_points = [(t, getattr(summary, metric_key)) for t, summary in metrics]
                curves_by_metric[metric_key].append(
                    CurveSpec(source_label, SOURCE_COLORS[source_label], source_label == "B", curve_points)
                )
        return curves_by_metric

    def _chart_x_range(self, curves_by_metric: dict[str, list[CurveSpec]]) -> tuple[float, float]:
        """根据窗口曲线点和输入时间段确定图表 X 轴范围。"""
        all_t = [
            t
            for curves in curves_by_metric.values()
            for curve in curves
            for t, _value in curve.points
        ]
        if all_t:
            # X 轴按真实窗口锚点缩放，右侧留白避免末尾点贴边。
            return min(all_t), max(all_t) + X_MARGIN_S
        start_s, end_s = self._time_range()
        return start_s, max(start_s + X_MARGIN_S, end_s)

    def _points_for(
        self,
        source: SourceData,
        target: str,
        channel_key: str,
        start_s: float,
        end_s: float,
    ) -> list[tuple[float, float]]:
        """提取目标对象在指定时间段内的通道样本。"""
        result: list[tuple[float, float]] = []
        node_ids: Iterable[str]
        if target == "all":
            # all 合并所有飞机同一通道样本，再整体统计。
            node_ids = source.samples.keys()
        else:
            node_ids = (target,)
        for node_id in node_ids:
            channel_points = source.samples.get(node_id, {}).get(channel_key, [])
            result.extend((t, value) for t, value in channel_points if start_s <= t <= end_s)
        # 多机合并后按时间排序，保证窗口锚点顺序稳定。
        result.sort(key=lambda item: item[0])
        return result

    def _time_range(self) -> tuple[float, float]:
        """返回规范化后的开始和结束时间。"""
        start_s = self._start_input.value()
        end_s = self._end_input.value()
        if end_s < start_s:
            # 用户输反时内部交换，界面不强制弹错。
            start_s, end_s = end_s, start_s
        return start_s, end_s

    def _enabled_sources(self) -> list[SourceData]:
        """返回当前启用的输入源列表。"""
        return [source for source in self._sources.values() if source.enabled]

    def _open_chart_popup(self) -> None:
        """打开或刷新只包含滑动窗口图表的弹窗。"""
        if self._popup is None:
            self._popup = ChartPopupDialog(self)
            # 关闭弹窗后停止同步刷新，避免访问已释放控件。
            self._popup.finished.connect(lambda _code: self._clear_popup_ref())
        self._refresh_window_charts()
        self._popup.show()
        self._popup.raise_()

    def _clear_popup_ref(self) -> None:
        """弹窗关闭后清理引用，避免后续刷新访问已销毁对象。"""
        self._popup = None

    def _export_all_metrics(self) -> None:
        """导出启用文件的全机和逐机六通道汇总指标。"""
        active = [source for source in self._enabled_sources() if source.path is not None and not source.error]
        if not active:
            # 导出是显式动作，没有数据时用对话框即时反馈。
            QMessageBox.information(self, "导出全部指标", "没有可导出的已启用文件。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出全部指标", "offline_metrics.csv", "CSV 文件 (*.csv)")
        if not path:
            # 用户取消保存时保持静默，不改变当前分析结果。
            return
        try:
            self._write_metrics_csv(Path(path), active)
        except OSError as exc:
            QMessageBox.warning(self, "导出全部指标", f"写入失败：{exc}")

    def _write_metrics_csv(self, path: Path, sources: list[SourceData]) -> None:
        """把所有启用文件的全机和逐机指标写入 CSV。"""
        start_s, end_s = self._time_range()
        fields = [
            "input_label",
            "source_path",
            "scope",
            "node_id",
            "channel",
            "count",
            "mean",
            "variance",
            "std",
            "rms",
            "max_abs",
            "max_abs_time_s",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            # utf-8-sig 让 Excel 直接打开 CSV 时能正确识别中文表头。
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for source in sources:
                # A/B 顺序写入同一个 CSV，input_label 保留来源。
                for row in self._metric_rows_for_source(source, start_s, end_s):
                    writer.writerow(row)

    def _metric_rows_for_source(self, source: SourceData, start_s: float, end_s: float) -> list[dict[str, object]]:
        """生成单个输入源的全机和逐机指标行。"""
        rows: list[dict[str, object]] = []
        targets = [("all", "all"), *[("node", node_id) for node_id in sorted(source.samples)]]
        for scope, node_id in targets:
            target = "all" if scope == "all" else node_id
            for channel in CHANNELS:
                # 导出覆盖全部通道，不受绘图通道单选影响。
                summary = _calc_summary(self._points_for(source, target, channel.key, start_s, end_s))
                if summary is None:
                    # 无样本也输出结构化行，便于外部脚本发现缺口。
                    summary = MetricSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                rows.append(
                    {
                        "input_label": source.label,
                        "source_path": str(source.path or ""),
                        "scope": scope,
                        "node_id": node_id,
                        "channel": channel.label,
                        "count": summary.count,
                        "mean": f"{summary.mean:.6g}",
                        "variance": f"{summary.variance:.6g}",
                        "std": f"{summary.std:.6g}",
                        "rms": f"{summary.rms:.6g}",
                        "max_abs": f"{summary.max_abs:.6g}",
                        "max_abs_time_s": f"{summary.max_abs_time_s:.6g}",
                    }
                )
        return rows


def _calc_summary(points: list[tuple[float, float]]) -> MetricSummary | None:
    """计算样本点的均值、方差、标准差、RMS 和最大绝对值。"""
    if not points:
        return None
    values = [value for _time_s, value in points]
    count = len(values)
    mean = sum(values) / count
    # 阶段二约定使用总体方差，不做 n-1 修正。
    variance = sum((value - mean) ** 2 for value in values) / count
    rms = math.sqrt(sum(value * value for value in values) / count)
    # max 保留首次达到最大绝对值的样本时刻。
    max_time, max_value = max(points, key=lambda item: abs(item[1]))
    return MetricSummary(
        count=count,
        mean=mean,
        variance=variance,
        std=math.sqrt(variance),
        rms=rms,
        max_abs=abs(max_value),
        max_abs_time_s=max_time,
    )


def _sliding_window(
    points: list[tuple[float, float]],
    start_s: float,
    end_s: float,
    window_s: float,
) -> list[tuple[float, MetricSummary]]:
    """按样本时刻滑动窗口，返回每个窗口起点对应的统计结果。"""
    if not points or window_s <= 0.0 or end_s < start_s:
        return []
    # 只以真实样本时刻做锚点，避免图上出现插值点。
    anchors = sorted({t for t, _value in points if start_s <= t <= end_s})
    result: list[tuple[float, MetricSummary]] = []
    for anchor in anchors:
        if anchor > end_s:
            continue
        window_end = anchor + window_s
        # 左闭右开窗口减少相邻窗口边界样本重复计入。
        summary = _calc_summary([(t, value) for t, value in points if anchor <= t < window_end and t <= end_s])
        if summary is not None:
            result.append((anchor, summary))
    return result


def _make_chart_view(
    title: str,
    curves: list[CurveSpec],
    x_range: tuple[float, float],
    accent_color: str,
) -> QChartView:
    """创建一个滑动窗口图表视图。"""
    chart = QChart()
    chart.setTitle(title)
    chart.setTitleFont(_chart_title_font())
    chart.setMargins(QMargins(4, 4, 8, 4))
    chart.setBackgroundBrush(QColor("#f8fafc"))
    # 只有 A/B 同时存在时显示图例，单文件场景保持简洁。
    chart.legend().setVisible(len(curves) > 1)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

    x_axis = QValueAxis()
    # 传入范围可能只有一个点，最小跨度保护避免 Qt 轴退化。
    x_axis.setRange(x_range[0], max(x_range[0] + 0.001, x_range[1]))
    x_axis.setTitleText("t (s)")
    x_axis.setGridLineColor(QColor("#e2e8f0"))
    chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)

    y_axis = QValueAxis()
    y_axis.setGridLineColor(QColor("#e2e8f0"))
    chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)

    all_y: list[float] = []
    for curve in curves:
        series = QLineSeries()
        series.setName(curve.name)
        pen = QPen(QColor(curve.color), 2.0)
        if curve.dashed:
            # B 文件使用虚线，和表格右侧数据对应。
            pen.setStyle(Qt.PenStyle.DashLine)
        series.setPen(pen)
        series.replace([QPointF(t, value) for t, value in curve.points])
        chart.addSeries(series)
        series.attachAxis(x_axis)
        series.attachAxis(y_axis)
        all_y.extend(value for _t, value in curve.points)
    if not curves:
        # 空图仍挂空 series，保持坐标轴和面板尺寸稳定。
        empty = QLineSeries()
        empty.setPen(QPen(QColor(accent_color), 1.5))
        chart.addSeries(empty)
        empty.attachAxis(x_axis)
        empty.attachAxis(y_axis)
    _apply_chart_y_range(y_axis, all_y)

    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    # ChartView 参与网格伸缩，保证 2x2 图表在窄窗口下仍均分空间。
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    view.setMinimumWidth(240)
    view.setMinimumHeight(155)
    return view


def _chart_title_font() -> QFont:
    """返回图表标题字体。"""
    font = QFont()
    font.setPointSize(9)
    font.setBold(True)
    return font


def _apply_chart_y_range(axis: QValueAxis, values: list[float]) -> None:
    """根据数据自动设置 Y 轴范围，空数据时给出固定占位范围。"""
    if not values:
        axis.setRange(-1.0, 1.0)
        axis.setTickCount(3)
        return
    low = min(values)
    high = max(values)
    if math.isclose(low, high, abs_tol=1e-9):
        # 常值曲线也给上下边距，避免贴成边框线。
        margin = max(1.0, abs(low) * 0.2)
    else:
        margin = (high - low) * 0.15
    axis.setRange(low - margin, high + margin)
    axis.setTickCount(5)


def _clear_layout(layout: QGridLayout | QVBoxLayout | QHBoxLayout) -> None:
    """删除布局内所有子项和控件。"""
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        if child_layout is not None:
            # 递归处理嵌套布局，避免反复刷新后残留控件。
            _clear_layout(child_layout)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
