"""Controls for configuring and interacting with virtual components."""

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from rpi_sim_client.gpio import BCM_TO_BOARD

from .board import COMPONENT_NAMES, GPIO_DEVICES, I2C_ADDRESSES, I2C_DEFAULTS
from .gpio_devices import DIGIT_PINS, SEGMENT_PINS


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
        self.echo_pin = QComboBox()
        self.display_pins = QWidget()
        grid = QGridLayout(self.display_pins)
        grid.setContentsMargins(0, 0, 0, 0)
        self.segments = [QComboBox() for _ in SEGMENT_PINS]
        self.digits = [QComboBox() for _ in DIGIT_PINS]
        for index, (label, picker) in enumerate(zip(("A", "B", "C", "D", "E", "F", "G", "DP", "D1", "D2", "D3", "D4"),
                                                    self.segments + self.digits)):
            row, column = divmod(index, 2)
            caption = QLabel(label)
            caption.setBuddy(picker)
            picker.setAccessibleName(f"{'Segment' if index < 8 else 'Digit'} {label}")
            grid.addWidget(caption, row, column * 2)
            grid.addWidget(picker, row, column * 2 + 1)
        display_pickers = self.segments + self.digits
        for picker in (self.pin, self.green_pin, self.blue_pin, self.echo_pin, *display_pickers):
            for pin in sorted(free_pins):
                picker.addItem(f"GPIO {pin} · pin {BCM_TO_BOARD[pin]}", pin)
        self.green_pin.setCurrentIndex(min(1, len(free_pins) - 1))
        self.blue_pin.setCurrentIndex(min(2, len(free_pins) - 1))
        self.echo_pin.setCurrentIndex(min(1, len(free_pins) - 1))
        preferred = (*SEGMENT_PINS, *DIGIT_PINS)
        alternatives = iter(sorted(set(free_pins) - set(preferred)))
        for picker, pin in zip(display_pickers, preferred):
            pin = pin if pin in free_pins else next(alternatives, None)
            picker.setCurrentIndex(picker.findData(pin))
        self.active_low = QCheckBox("Light when the output is LOW")
        self.digit_active_low = QCheckBox("Enable a digit when its pin is LOW")
        self.digit_active_low.setChecked(True)
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
        form.addRow("Echo pin", self.echo_pin)
        form.addRow("Display pins", self.display_pins)
        form.addRow("Polarity", self.active_low)
        form.addRow("Digit polarity", self.digit_active_low)
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
            elif component.kind == "ultrasonic":
                self.echo_pin.setCurrentIndex(self.echo_pin.findData(component.echo_pin))
            elif component.kind == "seven_segment":
                for picker, pin in zip(display_pickers, component.pins):
                    picker.setCurrentIndex(picker.findData(pin))
                self.digit_active_low.setChecked(component.digit_active_low is not False)
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
        for picker in (self.pin, self.green_pin, self.blue_pin, self.echo_pin, *display_pickers):
            picker.currentIndexChanged.connect(self._validate_pins)
        self._update_kind()
        if component and component.kind in I2C_ADDRESSES:
            self.address.setCurrentIndex(self.address.findData(component.address))

    def _update_kind(self):
        kind = self.kind.currentData()
        self.name.setPlaceholderText(COMPONENT_NAMES[kind])
        rgb = kind == "rgb"
        display = kind == "seven_segment"
        ultrasonic = kind == "ultrasonic"
        i2c = kind in I2C_ADDRESSES
        self.form.labelForField(self.pin).setText("Red pin" if rgb else ("Trigger pin" if ultrasonic else "GPIO pin"))
        self.form.setRowVisible(self.pin, not i2c and not display)
        self.form.setRowVisible(self.green_pin, rgb)
        self.form.setRowVisible(self.blue_pin, rgb)
        self.form.setRowVisible(self.echo_pin, ultrasonic)
        self.form.setRowVisible(self.display_pins, display)
        self.form.setRowVisible(self.active_low, kind in ("led", "rgb", "seven_segment"))
        self.form.setRowVisible(self.digit_active_low, display)
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
        elif kind == "seven_segment":
            display = self._display_assignments()
            if None in display or len(set(display)) != 12:
                error = "Choose twelve different GPIO pins for the display."
        elif kind == "ultrasonic":
            echo = self.echo_pin.currentData()
            if echo is None or pins[0] is None or echo == pins[0]:
                error = "Choose different GPIO pins for Trigger and Echo."
        elif pins[0] is None:
            error = "All GPIO pins are in use."
        self.pin_error.setText(error)
        self.form.setRowVisible(self.pin_error, bool(error))
        self.ok.setEnabled(not error)

    def values(self):
        kind = self.kind.currentData()
        is_i2c = kind in I2C_ADDRESSES
        return (
            kind, None if is_i2c or kind == "seven_segment" else self.pin.currentData(), self.name.text().strip(),
            kind in ("led", "rgb", "seven_segment") and self.active_low.isChecked(),
            self.green_pin.currentData() if kind == "rgb" else None,
            self.blue_pin.currentData() if kind == "rgb" else None,
            self.bus.value() if is_i2c else None,
            self.address.currentData() if is_i2c else None,
        )

    def _display_assignments(self):
        return tuple(picker.currentData() for picker in self.segments + self.digits)

    def gpio_options(self):
        kind = self.kind.currentData()
        if kind == "ultrasonic":
            return {"echo_pin": self.echo_pin.currentData()}
        if kind == "seven_segment":
            pins = self._display_assignments()
            return {
                "segment_pins": pins[:8], "digit_pins": pins[8:],
                "digit_active_low": self.digit_active_low.isChecked(),
            }
        return {}


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


class SevenSegmentDisplay(QWidget):
    # A–G, followed by the decimal point.
    SHAPES = tuple(QPolygonF([QPointF(x, y) for x, y in points]) for points in (
        ((8, 2), (37, 2), (41, 6), (36, 10), (9, 10), (5, 6)),
        ((41, 8), (45, 12), (45, 28), (40, 32), (36, 28), (36, 13)),
        ((40, 34), (45, 38), (45, 54), (41, 58), (36, 53), (36, 38)),
        ((9, 55), (36, 55), (40, 60), (36, 64), (8, 64), (4, 60)),
        ((4, 34), (8, 38), (8, 53), (4, 58), (0, 54), (0, 38)),
        ((5, 8), (9, 13), (9, 28), (4, 32), (0, 28), (0, 12)),
        ((9, 29), (36, 29), (40, 33), (36, 37), (9, 37), (4, 33)),
    ))
    CHARACTERS = dict(zip((0, 0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F), " 0123456789"))

    def __init__(self):
        super().__init__()
        self.masks = None
        self.setMinimumHeight(86)
        self.setAccessibleName("Four-digit display")
        self.refresh((0, 0, 0, 0))

    def refresh(self, masks):
        if masks != self.masks:
            self.masks = masks
            self.setAccessibleDescription("".join(
                self.CHARACTERS.get(mask & 0x7F, "?") + ("." if mask & 0x80 else "") for mask in masks
            ))
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#141925"))
            painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
            scale = min((self.width() - 20) / 230, (self.height() - 20) / 64)
            painter.translate((self.width() - 230 * scale) / 2, (self.height() - 64 * scale) / 2)
            painter.scale(scale, scale)
            for mask in self.masks:
                for bit, shape in enumerate(self.SHAPES):
                    painter.setBrush(QColor("#ff706b" if mask & (1 << bit) else "#4b3039"))
                    painter.drawPolygon(shape)
                painter.setBrush(QColor("#ff706b" if mask & 0x80 else "#4b3039"))
                painter.drawEllipse(QRectF(47, 55, 8, 8))
                painter.translate(58, 0)
        finally:
            painter.end()


class ComponentCard(QFrame):
    edit = Signal(str)
    remove = Signal(str)
    interact = Signal(str, bool)
    motion_changed = Signal(str, tuple, tuple)
    sensor_changed = Signal(str, tuple)
    remote_pressed = Signal(str, str)

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
        if component.kind in GPIO_DEVICES:
            self._make_gpio_controls(layout)
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

    def _make_gpio_controls(self, layout):
        component = self.component
        kind = component.kind
        if kind == "seven_segment":
            self.screen = SevenSegmentDisplay()
            layout.addWidget(self.screen)
            pins = QLabel(
                "A–G, DP  " + " · ".join(map(str, component.segment_pins))
                + "\nD1–D4  " + " · ".join(map(str, component.digit_pins))
            )
            pins.setObjectName("muted")
            pins.setWordWrap(True)
            pins.setToolTip("BCM GPIO assignments")
            layout.addWidget(pins)
            layout.addStretch()
            return

        pin_text = f"GPIO {component.pin}"
        if kind == "ultrasonic":
            pin_text = f"Trigger GPIO {component.pin} · Echo GPIO {component.echo_pin}"
        pins = QLabel(pin_text)
        pins.setObjectName("muted")
        pins.setWordWrap(True)
        layout.addWidget(pins)
        if kind == "ir":
            self.state = QLabel()
            self.state.setObjectName("muted")
            layout.addWidget(self.state)
            grid = QGridLayout()
            grid.setSpacing(4)
            self.remote_buttons = {}
            keys = (
                (None, "UP", None), ("LEFT", "OK", "RIGHT"), (None, "DOWN", None),
                ("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"), ("*", "0", "#"),
            )
            for row, keys_in_row in enumerate(keys):
                for column, key in enumerate(keys_in_row):
                    if key is not None:
                        button = QPushButton(key)
                        button.setAccessibleName(f"Send {key}")
                        button.clicked.connect(lambda _checked=False, name=key: self.remote_pressed.emit(component.id, name))
                        grid.addWidget(button, row, column)
                        self.remote_buttons[key] = button
            layout.addLayout(grid)
            return

        inputs = QFormLayout()
        inputs.setSpacing(6)
        if kind == "dht11":
            self.temperature = QDoubleSpinBox()
            self.temperature.setRange(0, 50)
            self.temperature.setDecimals(1)
            self.temperature.setSuffix(" °C")
            self.humidity = QSpinBox()
            self.humidity.setRange(0, 100)
            self.humidity.setSuffix(" %")
            inputs.addRow("Temperature", self.temperature)
            inputs.addRow("Humidity", self.humidity)
            self.temperature.setKeyboardTracking(False)
            self.humidity.setKeyboardTracking(False)
            self.temperature.valueChanged.connect(self._send_sensor_values)
            self.humidity.valueChanged.connect(self._send_sensor_values)
        else:
            self.distance = QDoubleSpinBox()
            self.distance.setRange(2, 400)
            self.distance.setDecimals(1)
            self.distance.setSuffix(" cm")
            self.distance.setKeyboardTracking(False)
            self.no_echo = QCheckBox("No echo")
            self.distance.valueChanged.connect(self._send_sensor_values)
            self.no_echo.toggled.connect(self._send_sensor_values)
            inputs.addRow("Distance", self.distance)
            inputs.addRow(self.no_echo)
        layout.addLayout(inputs)
        layout.addStretch()

    def _send_sensor_values(self):
        values = (
            (self.temperature.value(), self.humidity.value()) if self.component.kind == "dht11"
            else (self.distance.value(), self.no_echo.isChecked())
        )
        self.sensor_changed.emit(self.component.id, values)

    def _refresh_gpio_device(self, board):
        kind = self.component.kind
        device = board.gpio_devices[self.component.id]
        if kind == "seven_segment":
            self.screen.refresh(board.display_segments(self.component.id))
        elif kind == "ir":
            self.state.setText(f"Last key  {device.last_key or '—'}")
        elif kind == "dht11":
            for control, value in ((self.temperature, device.temperature), (self.humidity, device.humidity)):
                blocked = control.blockSignals(True)
                control.setValue(value)
                control.blockSignals(blocked)
        else:
            blocked = self.distance.blockSignals(True)
            self.distance.setValue(device.distance)
            self.distance.blockSignals(blocked)
            blocked = self.no_echo.blockSignals(True)
            self.no_echo.setChecked(device.no_echo)
            self.no_echo.blockSignals(blocked)

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
        if kind in GPIO_DEVICES:
            self._refresh_gpio_device(board)
            return
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
