"""障碍库编辑窗口。注意：控件只编辑应用层草稿，文件读写通过回调完成。"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.runner.obstacle_editor_application import (
    GeoPointInput,
    ObstacleInput,
    ObstacleLibraryData,
)
from src.ui.gui.obstacle_editor_view_model import ObstacleEditorViewModel

SaveHandler = Callable[[list[ObstacleInput], Path | None], ObstacleLibraryData]
PreviewHandler = Callable[[list[ObstacleInput]], None]


class ObstacleEditorDialog(QDialog):
    """编辑外部经纬度障碍库。注意：保存前始终校验整套草稿。"""

    def __init__(
        self,
        library: ObstacleLibraryData,
        *,
        origin: GeoPointInput | None,
        save_handler: SaveHandler,
        preview_handler: PreviewHandler | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化编辑器并复制障碍库，取消时不影响磁盘。"""

        super().__init__(parent)
        self.library = library
        self.vm = ObstacleEditorViewModel(library.obstacles, origin=origin)
        self._save_handler = save_handler
        self._preview_handler = preview_handler
        self._syncing = False
        self.saved = False
        self.setWindowTitle("配置障碍区信息")
        # 编辑时需要同时拖动避障窗口、观察主地图，因此不能用模态窗口锁住父窗口。
        self.setModal(False)
        self.setMinimumSize(900, 620)
        self.resize(980, 680)
        self._build_ui()
        self._rebuild_list()
        self._refresh_validation_and_preview()

    def _build_ui(self) -> None:
        """构建列表、动态表单和保存操作区。"""

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # 顶部明确显示真正写入的文件，避免用户误以为只改当前场景内存。
        header = QHBoxLayout()
        title = QLabel("配置障碍区信息")
        title.setObjectName("stageTitle")
        self.file_label = QLabel(str(self.library.path))
        self.file_label.setObjectName("reportPill")
        self.file_label.setToolTip(str(self.library.path))
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.file_label)
        root.addLayout(header)
        warning = QLabel("修改当前文件会影响所有引用它的仿真配置；如需隔离场景，请使用“另存并应用”。")
        warning.setObjectName("avoidHint")
        warning.setWordWrap(True)
        root.addWidget(warning)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        body.addWidget(self._build_list_group(), 2)
        body.addWidget(self._build_detail_group(), 3)

        # 底部状态不使用弹窗，用户可以就地修正重复 ID 或非法几何。
        self.validation_label = QLabel("")
        self.validation_label.setObjectName("avoidHint")
        self.validation_label.setWordWrap(True)
        root.addWidget(self.validation_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        self.save_as_button = QPushButton("另存并应用")
        self.save_as_button.clicked.connect(self._save_as)
        self.save_button = QPushButton("保存并应用")
        self.save_button.clicked.connect(lambda: self._save(None))
        actions.addWidget(cancel_button)
        actions.addWidget(self.save_as_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

    def _build_list_group(self) -> QWidget:
        """构建障碍列表及增删复制按钮。"""

        group = QGroupBox("障碍区列表", self)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 18, 10, 10)
        self.obstacle_list = QListWidget(group)
        self.obstacle_list.currentRowChanged.connect(self._load_selected)
        self.obstacle_list.itemChanged.connect(self._on_list_item_changed)
        layout.addWidget(self.obstacle_list, 1)

        add_row = QHBoxLayout()
        self.add_circle_button = QPushButton("新增圆形")
        self.add_circle_button.clicked.connect(self._add_circle)
        self.add_rect_button = QPushButton("新增四点区域")
        self.add_rect_button.clicked.connect(self._add_quadrilateral)
        add_row.addWidget(self.add_circle_button)
        add_row.addWidget(self.add_rect_button)
        layout.addLayout(add_row)

        edit_row = QHBoxLayout()
        self.duplicate_button = QPushButton("复制")
        self.duplicate_button.clicked.connect(self._duplicate_selected)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self._delete_selected)
        edit_row.addWidget(self.duplicate_button)
        edit_row.addWidget(self.delete_button)
        layout.addLayout(edit_row)
        return group

    def _build_detail_group(self) -> QWidget:
        """构建公共字段、圆形字段和四点字段。"""

        group = QGroupBox("障碍区信息", self)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 18, 10, 10)
        self.detail_container = QWidget(group)
        detail_layout = QVBoxLayout(self.detail_container)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        common = QFormLayout()
        self.id_edit = QLineEdit()
        self.id_edit.textChanged.connect(self._on_form_changed)
        self.type_combo = QComboBox()
        self.type_combo.addItem("圆形障碍区", "circle")
        self.type_combo.addItem("四点障碍区", "rect")
        self.type_combo.currentIndexChanged.connect(self._on_shape_changed)
        self.enabled_check = QCheckBox("默认参与避障规划")
        self.enabled_check.toggled.connect(self._on_form_changed)
        common.addRow("障碍 ID", self.id_edit)
        common.addRow("类型", self.type_combo)
        common.addRow("默认启用", self.enabled_check)
        detail_layout.addLayout(common)

        self.circle_widget = QWidget(self.detail_container)
        circle_form = QFormLayout(self.circle_widget)
        circle_form.setContentsMargins(0, 8, 0, 0)
        self.center_latitude_spin = self._coordinate_spin(latitude=True)
        self.center_longitude_spin = self._coordinate_spin(latitude=False)
        self.radius_spin = self._number_spin(0.0, 10_000_000.0, 1)
        self.radius_spin.setSuffix(" m")
        circle_form.addRow("中心纬度", self.center_latitude_spin)
        circle_form.addRow("中心经度", self.center_longitude_spin)
        circle_form.addRow("半径", self.radius_spin)
        detail_layout.addWidget(self.circle_widget)

        self.points_widget = QWidget(self.detail_container)
        point_grid = QGridLayout(self.points_widget)
        point_grid.setContentsMargins(0, 8, 0, 0)
        point_grid.addWidget(QLabel("顶点"), 0, 0)
        point_grid.addWidget(QLabel("纬度"), 0, 1)
        point_grid.addWidget(QLabel("经度"), 0, 2)
        self.point_spins: list[tuple[QDoubleSpinBox, QDoubleSpinBox]] = []
        for index in range(4):
            latitude_spin = self._coordinate_spin(latitude=True)
            longitude_spin = self._coordinate_spin(latitude=False)
            self.point_spins.append((latitude_spin, longitude_spin))
            point_grid.addWidget(QLabel(f"P{index + 1}"), index + 1, 0)
            point_grid.addWidget(latitude_spin, index + 1, 1)
            point_grid.addWidget(longitude_spin, index + 1, 2)
        detail_layout.addWidget(self.points_widget)
        detail_layout.addStretch(1)
        layout.addWidget(self.detail_container, 1)
        return group

    def _coordinate_spin(self, *, latitude: bool) -> QDoubleSpinBox:
        """创建经纬度输入框。注意：七位小数与文件输出精度一致。"""

        maximum = 90.0 if latitude else 180.0
        spin = self._number_spin(-maximum, maximum, 7)
        spin.setSingleStep(0.0001)
        return spin

    def _number_spin(self, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        """创建直接键入的浮点框并绑定表单刷新。"""

        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.valueChanged.connect(self._on_form_changed)
        return spin

    def _rebuild_list(self) -> None:
        """根据草稿重建列表并恢复当前选择。"""

        selected = self.vm.selected_index
        self.obstacle_list.blockSignals(True)
        self.obstacle_list.clear()
        for obstacle in self.vm.items:
            item = QListWidgetItem(self._item_text(obstacle))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if obstacle.enabled else Qt.CheckState.Unchecked)
            self.obstacle_list.addItem(item)
        self.obstacle_list.blockSignals(False)
        if self.vm.items:
            self.obstacle_list.setCurrentRow(max(0, min(selected, len(self.vm.items) - 1)))
        else:
            self._load_selected(-1)

    def _load_selected(self, index: int) -> None:
        """把所选草稿灌入右侧表单。"""

        self.vm.selected_index = index
        has_selection = 0 <= index < len(self.vm.items)
        self.detail_container.setEnabled(has_selection)
        self.duplicate_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        if not has_selection:
            self.id_edit.clear()
            return
        obstacle = self.vm.items[index]
        self._syncing = True
        self.id_edit.setText(obstacle.obstacle_id)
        self.type_combo.setCurrentIndex(self.type_combo.findData(obstacle.kind))
        self.enabled_check.setChecked(obstacle.enabled)
        center = obstacle.center or self.vm.origin
        self.center_latitude_spin.setValue(center.latitude_deg)
        self.center_longitude_spin.setValue(center.longitude_deg)
        self.radius_spin.setValue(obstacle.radius_m)
        points = obstacle.points if len(obstacle.points) == 4 else self._default_points()
        for (latitude_spin, longitude_spin), point in zip(self.point_spins, points, strict=True):
            latitude_spin.setValue(point.latitude_deg)
            longitude_spin.setValue(point.longitude_deg)
        self._syncing = False
        self._update_shape_widgets()
        self._refresh_validation_and_preview()

    def _on_shape_changed(self, _index: int) -> None:
        """切换形状并为缺失的几何字段提供安全默认值。"""

        if self._syncing:
            return
        index = self.vm.selected_index
        if not 0 <= index < len(self.vm.items):
            return
        obstacle = self.vm.items[index]
        kind = str(self.type_combo.currentData())
        if kind == "circle" and obstacle.center is None:
            obstacle.center = self.vm.origin
            obstacle.radius_m = obstacle.radius_m if obstacle.radius_m > 0.0 else 100.0
        if kind == "rect" and len(obstacle.points) != 4:
            obstacle.points = self._default_points()
        self._update_shape_widgets()
        self._on_form_changed()

    def _on_form_changed(self, _value: object = None) -> None:
        """把右侧表单写回当前草稿并刷新校验。"""

        if self._syncing:
            return
        index = self.vm.selected_index
        if not 0 <= index < len(self.vm.items):
            return
        obstacle = self.vm.items[index]
        obstacle.obstacle_id = self.id_edit.text()
        obstacle.kind = str(self.type_combo.currentData())
        obstacle.enabled = self.enabled_check.isChecked()
        if obstacle.kind == "circle":
            obstacle.center = GeoPointInput(
                self.center_latitude_spin.value(),
                self.center_longitude_spin.value(),
            )
            obstacle.radius_m = self.radius_spin.value()
        else:
            obstacle.points = tuple(
                GeoPointInput(latitude_spin.value(), longitude_spin.value())
                for latitude_spin, longitude_spin in self.point_spins
            )
        self.vm.mark_dirty()
        list_item = self.obstacle_list.item(index)
        if list_item is not None:
            # 右侧修改需同步左侧真实复选框；屏蔽 itemChanged 避免同一次操作重复回写。
            signals_were_blocked = self.obstacle_list.blockSignals(True)
            list_item.setText(self._item_text(obstacle))
            list_item.setCheckState(Qt.CheckState.Checked if obstacle.enabled else Qt.CheckState.Unchecked)
            self.obstacle_list.blockSignals(signals_were_blocked)
        self._refresh_validation_and_preview()

    def _on_list_item_changed(self, item: QListWidgetItem) -> None:
        """响应左侧勾选变化，并同步右侧字段和地图预览。"""

        if self._syncing:
            return
        index = self.obstacle_list.row(item)
        if not 0 <= index < len(self.vm.items):
            return
        self.vm.items[index].enabled = item.checkState() == Qt.CheckState.Checked
        self.vm.mark_dirty()
        if self.obstacle_list.currentRow() != index:
            # 勾选任意行时同步选中它，让右侧显示正在操作的同一个障碍区。
            self.obstacle_list.setCurrentRow(index)
            return
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(self.vm.items[index].enabled)
        self.enabled_check.blockSignals(False)
        self._refresh_validation_and_preview()

    def _add_circle(self) -> None:
        """新增并选中圆形障碍。"""

        self.vm.add_circle()
        self._rebuild_list()

    def _add_quadrilateral(self) -> None:
        """新增并选中四点障碍。"""

        self.vm.add_quadrilateral()
        self._rebuild_list()

    def _duplicate_selected(self) -> None:
        """复制当前障碍。"""

        if self.vm.selected_index < 0:
            return
        self.vm.duplicate(self.vm.selected_index)
        self._rebuild_list()

    def _delete_selected(self) -> None:
        """删除当前障碍。"""

        if self.vm.selected_index < 0:
            return
        self.vm.delete(self.vm.selected_index)
        self._rebuild_list()
        self._refresh_validation_and_preview()

    def _refresh_validation_and_preview(self) -> None:
        """更新错误提示、保存按钮和主俯视图草稿。"""

        errors = self.vm.validate_all()
        can_save = not errors
        self.save_button.setEnabled(can_save)
        self.save_as_button.setEnabled(can_save)
        if errors:
            first_index = min(errors)
            obstacle_id = self.vm.items[first_index].obstacle_id or f"第 {first_index + 1} 项"
            self.validation_label.setText(f"{obstacle_id}：{'；'.join(errors[first_index])}")
            return
        self.validation_label.setText(f"数据有效，共 {len(self.vm.items)} 个障碍。")
        if self._preview_handler is not None:
            self._preview_handler(copy.deepcopy(self.vm.items))

    def _save_as(self) -> None:
        """选择新 JSON 文件并保存应用。"""

        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "另存障碍库并应用",
            str(self.library.path.with_name(f"{self.library.path.stem}_custom.json")),
            "JSON 文件 (*.json)",
        )
        if not selected:
            return
        target = Path(selected)
        if not target.suffix:
            target = target.with_suffix(".json")
        self._save(target)

    def _save(self, target: Path | None) -> None:
        """调用应用层保存回调，成功后关闭编辑器。"""

        errors = self.vm.validate_all()
        if errors:
            self._refresh_validation_and_preview()
            return
        try:
            saved = self._save_handler(copy.deepcopy(self.vm.items), target)
        except (OSError, ValueError) as exc:
            self.validation_label.setText(f"保存失败：{exc}")
            return
        self.library = saved
        self.saved = True
        self.accept()

    def _update_shape_widgets(self) -> None:
        """只显示当前类型需要的几何字段。"""

        is_circle = self.type_combo.currentData() == "circle"
        self.circle_widget.setVisible(is_circle)
        self.points_widget.setVisible(not is_circle)

    def _default_points(self) -> tuple[GeoPointInput, ...]:
        """返回航线原点附近的默认四点区域。"""

        latitude = self.vm.origin.latitude_deg
        longitude = self.vm.origin.longitude_deg
        offset = 0.001
        return (
            GeoPointInput(latitude - offset, longitude - offset),
            GeoPointInput(latitude - offset, longitude + offset),
            GeoPointInput(latitude + offset, longitude + offset),
            GeoPointInput(latitude + offset, longitude - offset),
        )

    @staticmethod
    def _item_text(obstacle: ObstacleInput) -> str:
        """生成列表摘要。"""

        kind = "圆形" if obstacle.kind == "circle" else "四点区域"
        return f"{obstacle.obstacle_id or '（未命名）'}  {kind}"
