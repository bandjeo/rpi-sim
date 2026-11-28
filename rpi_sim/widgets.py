"""Controls for configuring and interacting with virtual components."""

from PySide6.QtCore import QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from rpi_sim_client.gpio import BCM_TO_BOARD

from .board import COMPONENT_NAMES, I2C_ADDRESSES, I2C_DEFAULTS


class ComponentDialog(QDialog):
    def __init__(self, free_pins, component=None, parent=None, *, used_addresses=()):
        super().__init__(parent)
        self.setWindowTitle("Edit component" if component else "Add component")
        self.setMinimumWidth(350)
        form = self.form = QFormLayout(self)
        form.setContentsMargins(22, 22, 22, 22)
        form.setSpacing(12)

        self.kind = QComboBox()
        for key, name in COMPONENT_NAMES.items():
            self.kind.addItem(name, key)
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.pin = QComboBox()
        self.green_pin = QComboBox()
        self.blue_pin = QComboBox()
        for picker in (self.pin, self.green_pin, self.blue_pin):
            for pin in sorted(free_pins):
                picker.addItem(f"GPIO {pin} · pin {BCM_TO_BOARD[pin]}", pin)
        self.green_pin.setCurrentIndex(min(1, len(free_pins) - 1))
        self.blue_pin.setCurrentIndex(min(2, len(free_pins) - 1))
        self.active_low = QCheckBox("Light when the output is LOW")
        self.used_addresses = set(used_addresses)
        self.bus = QSpinBox()
        self.bus.setRange(0, 255)
        self.bus.setValue(component.bus if component and component.bus is not None else 1)
        self.address = QComboBox()
        form.addRow("Type", self.kind)
        form.addRow("Name", self.name)
        form.addRow("GPIO pin", self.pin)
        form.addRow("Green pin", self.green_pin)
        form.addRow("Blue pin", self.blue_pin)
        form.addRow("Polarity", self.active_low)
        form.addRow("I²C bus", self.bus)
        form.addRow("Address", self.address)
        self.pin_error = QLabel("Choose three different GPIO pins for an RGB LED.")
        self.pin_error.setWordWrap(True)
        self.pin_error.setObjectName("muted")
        form.addRow(self.pin_error)
        if component:
            self.kind.setCurrentIndex(self.kind.findData(component.kind))
            self.kind.setEnabled(False)
            self.name.setText(component.name)
            self.pin.setCurrentIndex(self.pin.findData(component.pin))
            if component.kind == "rgb":
                self.green_pin.setCurrentIndex(self.green_pin.findData(component.green_pin))
                self.blue_pin.setCurrentIndex(self.blue_pin.findData(component.blue_pin))
            self.active_low.setChecked(component.active_low)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Save" if component else "Add")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self.bus.valueChanged.connect(self._update_addresses)
        self.address.currentIndexChanged.connect(self._validate_pins)
        for picker in (self.pin, self.green_pin, self.blue_pin):
            picker.currentIndexChanged.connect(self._validate_pins)
        self._update_kind()
        if component and component.kind in I2C_ADDRESSES:
            self.address.setCurrentIndex(self.address.findData(component.address))

    def _update_kind(self):
        kind = self.kind.currentData()
        self.name.setPlaceholderText(COMPONENT_NAMES[kind])
        rgb = kind == "rgb"
        i2c = kind in I2C_ADDRESSES
        self.form.labelForField(self.pin).setText("Red pin" if rgb else "GPIO pin")
        self.form.setRowVisible(self.pin, not i2c)
        self.form.setRowVisible(self.green_pin, rgb)
        self.form.setRowVisible(self.blue_pin, rgb)
        self.form.setRowVisible(self.active_low, kind in ("led", "rgb"))
        self.form.setRowVisible(self.bus, i2c)
        self.form.setRowVisible(self.address, i2c)
        self._update_addresses()

    def _update_addresses(self):
        kind = self.kind.currentData()
        previous = self.address.currentData()
        self.address.clear()
        for address in I2C_ADDRESSES.get(kind, ()):
            if (self.bus.value(), address) not in self.used_addresses:
                self.address.addItem(f"0x{address:02X}", address)
        preferred = previous if self.address.findData(previous) >= 0 else I2C_DEFAULTS.get(kind)
        index = self.address.findData(preferred)
        if index >= 0:
            self.address.setCurrentIndex(index)
        self._validate_pins()

    def _validate_pins(self):
        pins = (self.pin.currentData(), self.green_pin.currentData(), self.blue_pin.currentData())
        kind = self.kind.currentData()
        error = ""
        if kind in I2C_ADDRESSES:
            if self.address.currentData() is None:
                error = "All supported addresses are in use on this bus."
        elif kind == "rgb" and (None in pins or len(set(pins)) != 3):
            error = "Choose three different GPIO pins for an RGB LED."
        elif pins[0] is None:
            error = "All GPIO pins are in use."
        self.pin_error.setText(error)
        self.form.setRowVisible(self.pin_error, bool(error))
        self.ok.setEnabled(not error)

    def values(self):
        kind = self.kind.currentData()
        is_i2c = kind in I2C_ADDRESSES
        return (
            kind, None if is_i2c else self.pin.currentData(), self.name.text().strip(),
            kind in ("led", "rgb") and self.active_low.isChecked(),
            self.green_pin.currentData() if kind == "rgb" else None,
            self.blue_pin.currentData() if kind == "rgb" else None,
            self.bus.value() if is_i2c else None,
            self.address.currentData() if is_i2c else None,
        )


class ComponentGlyph(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.active = False
        self.color = QColor()
        self.setFixedSize(66, 64)

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            if self.kind in ("led", "rgb"):
                self._draw_led(painter)
            elif self.kind == "button":
                self._draw_button(painter)
            elif self.kind == "buzzer":
                self._draw_buzzer(painter)
            else:
                self._draw_pir(painter)
        finally:
            painter.end()

    def _draw_led(self, painter):
        color = self.color if self.active else QColor("#33425b")
        if self.active:
            halo = QColor(color)
            halo.setAlpha(128)
            glow = QRadialGradient(33, 28, 30)
            glow.setColorAt(0, halo)
            halo.setAlpha(0)
            glow.setColorAt(1, halo)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(QRectF(3, -2, 60, 60))
        lead_pen = QPen(QColor("#8395ad"), 3)
        lead_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(lead_pen)
        painter.drawLine(27, 43, 27, 56)
        painter.drawLine(39, 43, 39, 52)
        if self.kind == "rgb":
            painter.drawLine(22, 42, 22, 51)
            painter.drawLine(33, 43, 33, 54)
        painter.setPen(QPen(color.lighter(145) if self.active else QColor("#515c74"), 1))
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(20, 12, 26, 34), 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(17, 38, 32, 8), 4, 4)
        painter.setBrush(color.lighter(180) if self.active else QColor("#61708a"))
        painter.drawEllipse(QRectF(26, 18, 6, 9))

    def _draw_buzzer(self, painter):
        painter.setPen(QPen(QColor("#647790"), 2))
        painter.drawLine(24, 42, 24, 54)
        painter.drawLine(35, 42, 35, 51)
        painter.setPen(QPen(QColor("#a4b4ca"), 1))
        painter.setBrush(QColor("#3a4c64"))
        painter.drawEllipse(QRectF(10, 9, 39, 39))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f7bf78" if self.active else "#172638"))
        painter.drawEllipse(QRectF(25, 24, 9, 9))
        if self.active:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#f7bf78"), 1.5))
            painter.drawArc(QRectF(17, 11, 39, 35), -45 * 16, 90 * 16)
            painter.drawArc(QRectF(14, 5, 50, 47), -45 * 16, 90 * 16)

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


class LCDDisplay(QWidget):
    def __init__(self):
        super().__init__()
        self.device = None
        self.blink_phase = False
        self.blink = QTimer(self, interval=400)
        self.blink.timeout.connect(self._blink_cursor)
        self.setMinimumHeight(72)
        self.setAccessibleName("LCD1602 display")

    def refresh(self, device):
        self.device = device
        if device.lcd.display_on and device.lcd.blink_on:
            if not self.blink.isActive():
                self.blink.start()
        else:
            self.blink.stop()
            self.blink_phase = False
        self.setAccessibleDescription("\n".join(device.lcd.lines()))
        self.update()

    def _blink_cursor(self):
        self.blink_phase = not self.blink_phase
        self.update()

    def paintEvent(self, _event):
        if self.device is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            lit = self.device.backlight
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#a7d47c" if lit else "#323e41"))
            painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
            lcd = self.device.lcd
            ink = QColor("#1c351b" if lit else "#c2ccbe")
            font = QFont("monospace")
            font.setStyleHint(QFont.StyleHint.TypeWriter)
            font.setPixelSize(min(19, max(9, (self.width() - 16) // 10)))
            painter.setFont(font)
            cell_width = (self.width() - 14) / 16
            for row, addresses in enumerate(lcd.visible_addresses()):
                for column, address in enumerate(addresses):
                    cell = QRectF(7 + column * cell_width, 10 + row * 25, cell_width, 23)
                    value = lcd.cell(address)
                    if isinstance(value, str):
                        painter.setPen(ink)
                        painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, value)
                    else:
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(ink)
                        step = min(cell_width / 6, 2.1)
                        for y, bits in enumerate(value):
                            for x in range(5):
                                if bits & (16 >> x):
                                    painter.drawRect(QRectF(cell.center().x() + (x - 2.5) * step,
                                                            cell.center().y() + (y - 4) * step, step, step))
                    if lcd.display_on and not lcd.character_ram and address == lcd.address:
                        if self.blink_phase:
                            painter.fillRect(cell.adjusted(1, 1, -1, -1), ink)
                        elif lcd.cursor_on:
                            painter.fillRect(QRectF(cell.left() + 1, cell.bottom() - 3, cell.width() - 2, 2), ink)
        finally:
            painter.end()


class ComponentCard(QFrame):
    edit = Signal(str)
    remove = Signal(str)
    interact = Signal(str, bool)
    motion_changed = Signal(str, tuple, tuple)

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

        if component.kind in I2C_ADDRESSES:
            self._make_i2c_controls(layout)
            return

        detail = QHBoxLayout()
        self.glyph = ComponentGlyph(component.kind)
        detail.addWidget(self.glyph)
        labels = QVBoxLayout()
        labels.setSpacing(4)
        if component.kind == "rgb":
            pin_text = " · ".join(f"{channel} {pin}" for channel, pin in zip("RGB", component.pins))
            self.pin_label = QLabel(pin_text)
            self.pin_label.setToolTip("BCM GPIO numbers for red, green, and blue")
        else:
            self.pin_label = QLabel(f"GPIO {component.pin} · pin {BCM_TO_BOARD[component.pin]}")
        self.pin_label.setObjectName("muted")
        labels.addWidget(self.pin_label)
        self.state = QLabel()
        self.state.setObjectName("componentState")
        labels.addWidget(self.state, 0, Qt.AlignmentFlag.AlignLeft)
        detail.addLayout(labels, 1)
        layout.addLayout(detail)
        layout.addStretch()

        if component.kind in ("led", "rgb", "buzzer"):
            self.action = None
            if component.kind != "buzzer":
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

    def _make_i2c_controls(self, layout):
        component = self.component
        address = QLabel(f"I²C {component.bus} · 0x{component.address:02X}")
        address.setObjectName("muted")
        layout.addWidget(address)
        if component.kind == "lcd":
            self.screen = LCDDisplay()
            layout.addWidget(self.screen)
            layout.addStretch()
            return
        self.motion_controls = []
        for name, unit, magnitudes, step in (
            ("Acceleration", "g", (0.0, 0.0, 1.0), 0.1),
            ("Rotation", "°/s", (0.0, 0.0, 0.0), 10.0),
        ):
            layout.addWidget(QLabel(f"{name} ({unit})"))
            axes = QHBoxLayout()
            axes.setSpacing(4)
            controls = []
            for axis, magnitude in zip("XYZ", magnitudes):
                control = QDoubleSpinBox()
                limit = 16 if unit == "g" else 2000
                control.setRange(-limit, limit)
                control.setSingleStep(step)
                control.setDecimals(1)
                control.setValue(magnitude)
                control.setKeyboardTracking(False)
                control.setAccessibleName(f"{name} {axis} in {unit}")
                axis_label = QLabel(axis)
                axis_label.setBuddy(control)
                axes.addWidget(axis_label)
                axes.addWidget(control, 1)
                controls.append(control)
            self.motion_controls.append(controls)
            layout.addLayout(axes)
        for control in (*self.motion_controls[0], *self.motion_controls[1]):
            control.valueChanged.connect(self._send_motion)

    def _send_motion(self):
        acceleration, rotation = (tuple(control.value() for control in axes) for axes in self.motion_controls)
        self.motion_changed.emit(self.component.id, acceleration, rotation)

    def refresh(self, board):
        component = self.component
        kind = component.kind
        if kind == "lcd":
            self.screen.refresh(board.i2c_device(component))
            return
        if kind == "mpu6050":
            device = board.i2c_device(component)
            for controls, values in zip(self.motion_controls, (device.acceleration, device.rotation)):
                for control, value in zip(controls, values):
                    blocked = control.blockSignals(True)
                    control.setValue(value)
                    control.blockSignals(blocked)
            return
        active = board.component_active(component)
        if kind == "rgb":
            self.glyph.color = QColor(*board.rgb_color(component))
            text = self.glyph.color.name().upper() if active else "Off"
        elif kind == "led":
            level = board.intensity(component.pin, component.active_low)
            self.glyph.color = QColor(*(round(value * level) for value in (97, 223, 161)))
            text = f"{level:.0%}" if 0 < level < 1 else ("On" if active else "Off")
        elif kind == "buzzer":
            tone = board.buzzer_tone(component.pin)
            text = f"{tone.frequency:g} Hz" if tone else "Silent"
        else:
            states = {"button": ("Released", "Pressed"), "pir": ("Clear", "Motion")}
            text = states[kind][int(active)]
        self.state.setText(text)
        self.state.setStyleSheet("color: #7fe4b2;" if active else "color: #a1afc4;")
        self.glyph.active = active
        self.glyph.update()
        if kind == "button":
            previous = self.action.blockSignals(True)
            self.action.setChecked(active)
            self.action.blockSignals(previous)
            self.action.setText("Release button" if active else "Press button")
        elif kind == "pir":
            self.action.setText("Retrigger motion" if active else "Trigger motion")
