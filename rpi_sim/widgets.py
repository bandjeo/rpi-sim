"""Controls for configuring and interacting with virtual components."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from rpi_sim_client.gpio import BCM_TO_BOARD

from .board import COMPONENT_NAMES


class ComponentDialog(QDialog):
    def __init__(self, free_pins, component=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit component" if component else "Add component")
        self.setMinimumWidth(350)
        form = QFormLayout(self)
        form.setContentsMargins(22, 22, 22, 22)
        form.setSpacing(12)

        self.kind = QComboBox()
        for key, name in COMPONENT_NAMES.items():
            self.kind.addItem(name, key)
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.pin = QComboBox()
        for pin in sorted(free_pins):
            self.pin.addItem(f"GPIO {pin} · pin {BCM_TO_BOARD[pin]}", pin)
        self.active_low = QCheckBox("Light when the pin is LOW")
        form.addRow("Type", self.kind)
        form.addRow("Name", self.name)
        form.addRow("GPIO pin", self.pin)
        form.addRow("Polarity", self.active_low)
        if component:
            self.kind.setCurrentIndex(self.kind.findData(component.kind))
            self.kind.setEnabled(False)
            self.name.setText(component.name)
            self.pin.setCurrentIndex(self.pin.findData(component.pin))
            self.active_low.setChecked(component.active_low)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save" if component else "Add")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self._update_kind()

    def _update_kind(self):
        kind = self.kind.currentData()
        self.name.setPlaceholderText(COMPONENT_NAMES[kind])
        self.active_low.setEnabled(kind == "led")

    def values(self):
        kind = self.kind.currentData()
        return kind, self.pin.currentData(), self.name.text().strip(), kind == "led" and self.active_low.isChecked()


class ComponentGlyph(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.active = False
        self.setFixedSize(66, 64)

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            if self.kind == "led":
                self._draw_led(painter)
            elif self.kind == "button":
                self._draw_button(painter)
            else:
                self._draw_pir(painter)
        finally:
            painter.end()

    def _draw_led(self, painter):
        if self.active:
            glow = QRadialGradient(33, 28, 30)
            glow.setColorAt(0, QColor(74, 231, 164, 128))
            glow.setColorAt(1, QColor(74, 231, 164, 0))
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(QRectF(3, -2, 60, 60))
        lead_pen = QPen(QColor("#8395ad"), 3)
        lead_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(lead_pen)
        painter.drawLine(27, 43, 27, 56)
        painter.drawLine(39, 43, 39, 52)
        painter.setPen(QPen(QColor("#8ef2be" if self.active else "#515c74"), 1))
        painter.setBrush(QColor("#61dfa1" if self.active else "#33425b"))
        painter.drawRoundedRect(QRectF(20, 12, 26, 34), 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(17, 38, 32, 8), 4, 4)
        painter.setBrush(QColor("#d6ffec" if self.active else "#61708a"))
        painter.drawEllipse(QRectF(26, 18, 6, 9))

    def _draw_button(self, painter):
        painter.setPen(QPen(QColor("#5f7186"), 2))
        painter.drawLine(8, 23, 58, 23)
        painter.drawLine(8, 43, 58, 43)
        painter.setBrush(QColor("#28374b"))
        painter.drawRoundedRect(QRectF(13, 12, 40, 42), 8, 8)
        painter.setPen(QPen(QColor("#90a9fa" if self.active else "#6d7fc4"), 1))
        painter.setBrush(QColor("#a2b6ff" if self.active else "#5368af"))
        painter.drawEllipse(QRectF(21, 21 if self.active else 18, 24, 24))

    def _draw_pir(self, painter):
        painter.setPen(QPen(QColor("#54747a"), 1))
        painter.setBrush(QColor("#26474c"))
        painter.drawRoundedRect(QRectF(9, 11, 48, 46), 7, 7)
        painter.setBrush(QColor("#baf4df" if self.active else "#a0b8c6"))
        painter.drawEllipse(QRectF(17, 14, 32, 32))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(22, 19, 22, 22))
        painter.drawEllipse(QRectF(28, 25, 10, 10))
        painter.drawLine(33, 14, 33, 46)
        painter.drawLine(17, 30, 49, 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffc478" if self.active else "#597783"))
        painter.drawEllipse(QRectF(31, 49, 4, 4))


class ComponentCard(QFrame):
    edit = Signal(str)
    remove = Signal(str)
    interact = Signal(str, bool)

    def __init__(self, component, parent=None):
        super().__init__(parent)
        self.component = component
        self.setObjectName("componentCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 12, 13, 13)
        layout.setSpacing(9)

        header = QHBoxLayout()
        self.name = QLabel(component.name)
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setObjectName("componentName")
        self.name.setWordWrap(True)
        header.addWidget(self.name, 1)
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("componentMenu")
        self.menu_button.setText("⋯")
        self.menu_button.setAccessibleName(f"Options for {component.name}")
        menu = QMenu(self.menu_button)
        menu.addAction("Edit…", lambda: self.edit.emit(component.id))
        menu.addAction("Remove", lambda: self.remove.emit(component.id))
        self.menu_button.setMenu(menu)
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.menu_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        detail = QHBoxLayout()
        self.glyph = ComponentGlyph(component.kind)
        detail.addWidget(self.glyph)
        labels = QVBoxLayout()
        labels.setSpacing(4)
        self.pin_label = QLabel(f"GPIO {component.pin} · pin {BCM_TO_BOARD[component.pin]}")
        self.pin_label.setObjectName("muted")
        labels.addWidget(self.pin_label)
        self.state = QLabel()
        self.state.setObjectName("componentState")
        labels.addWidget(self.state, 0, Qt.AlignmentFlag.AlignLeft)
        detail.addLayout(labels, 1)
        layout.addLayout(detail)
        layout.addStretch()

        if component.kind == "led":
            self.action = None
            caption = QLabel("Active low" if component.active_low else "Active high")
            caption.setObjectName("muted")
            caption.setMinimumHeight(31)
            layout.addWidget(caption)
        else:
            self.action = QPushButton()
            if component.kind == "button":
                self.action.setCheckable(True)
                self.action.toggled.connect(lambda active: self.interact.emit(component.id, active))
            else:
                self.action.clicked.connect(lambda: self.interact.emit(component.id, True))
            layout.addWidget(self.action)

    def show_state(self, active):
        self.glyph.active = active
        self.glyph.update()
        kind = self.component.kind
        states = {"led": ("Off", "On"), "button": ("Released", "Pressed"), "pir": ("Clear", "Motion")}
        self.state.setText(states[kind][int(active)])
        self.state.setStyleSheet("color: #7fe4b2;" if active else "color: #a1afc4;")
        if kind == "button":
            previous = self.action.blockSignals(True)
            self.action.setChecked(active)
            self.action.blockSignals(previous)
            self.action.setText("Release button" if active else "Press button")
        elif kind == "pir":
            self.action.setText("Retrigger motion" if active else "Trigger motion")
