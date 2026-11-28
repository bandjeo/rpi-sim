"""Desktop GUI for configuring the board and running Python scripts."""

import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QIODevice, QSaveFile, QSettings, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from rpi_sim_client.gpio import BCM_TO_BOARD
from rpi_sim_client.protocol import HOST

from .audio import BuzzerAudio
from .board import COMPONENT_NAMES, I2C_ADDRESSES, Board, Component, layout_json, load_layout, parse_layout
from .process import ScriptProcess
from .server import GPIOServer
from .session import BoardSession
from .widgets import ComponentCard, ComponentDialog

PROJECT = Path(__file__).resolve().parents[1]
DEMO_COMPONENTS = [
    Component("motion-light", "led", 18, "Motion light"),
    Component("manual-light", "led", 23, "Manual light"),
    Component("button", "button", 24, "Push button"),
    Component("pir", "pir", 17, "PIR sensor"),
]


class SimulatorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RPi Simulator")
        self.resize(1090, 680)
        self.setMinimumSize(820, 530)
        self.python = sys.executable
        self.settings = QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, "rpi-sim", "RPi Simulator", self
        )
        self.layout_path = None
        self.cards = {}
        self.pulse_timers = {}
        self.i2c_updates = set()
        self.i2c_repaint = QTimer(self, singleShot=True, interval=16)
        self.i2c_repaint.timeout.connect(self._refresh_i2c)
        self.display_repaint = QTimer(self, interval=33)
        self.display_repaint.timeout.connect(self._refresh_displays)
        self.audio = BuzzerAudio(self)
        self.audio.failed.connect(lambda message: self._append_console(f"\n[Buzzer: {message}]\n"))
        self.board = Board(self._update_pin, self._send_edge, self._update_i2c, self._send_pulses)
        self.session = BoardSession(self.board)
        self.runner = ScriptProcess(self.session, self)
        self.runner.console_output.connect(self._append_console)
        self.runner.status_changed.connect(self._show_run_status)
        self.server = GPIOServer(self.session, self)
        self.server.connection_changed.connect(self._show_external_status)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(16)
        root.addLayout(self._header())
        root.addWidget(self._script_bar())

        workbench = QSplitter(Qt.Orientation.Horizontal)
        workbench.setChildrenCollapsible(False)
        workbench.addWidget(self._components_panel())
        workbench.addWidget(self._console_panel())
        workbench.setSizes([570, 430])
        root.addWidget(workbench, 1)
        self._restore_layout()
        self.script.editingFinished.connect(self._remember_layout)
        self._show_run_status("Idle")
        if self.server.start():
            self.connection_label.setText(f"{HOST}:{self.server.listener.serverPort()}")
            self.connection_label.setToolTip("Address for Python scripts run outside this app")
        else:
            self.connection_label.setText("External connection unavailable")
            self.connection_label.setToolTip(self.server.error)

    def _header(self):
        header = QHBoxLayout()
        title = QLabel("RPi Simulator")
        title.setObjectName("title")
        header.addWidget(title, 1)
        self.connection_label = QLabel()
        self.connection_label.setObjectName("muted")
        header.addWidget(self.connection_label)
        self.demo_button = QPushButton("Load demo")
        self.demo_button.clicked.connect(self._load_demo)
        header.addWidget(self.demo_button)

        self.layout_button = QToolButton()
        self.layout_button.setText("Layout")
        menu = QMenu(self.layout_button)
        menu.addAction("New layout", self._new_layout)
        menu.addAction("Open layout…", self._open_layout)
        menu.addAction("Save layout…", self._save_layout)
        self.layout_button.setMenu(menu)
        self.layout_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.layout_button)
        return header

    def _script_bar(self):
        bar = QFrame()
        bar.setObjectName("scriptBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 10)
        script_label = QLabel("Script")
        row.addWidget(script_label)
        self.script = QLineEdit()
        self.script.setPlaceholderText("Path to a Python script")
        self.script.setAccessibleName("Python script path")
        script_label.setBuddy(self.script)
        self.script.returnPressed.connect(self._run_script)
        row.addWidget(self.script, 1)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse_script)
        row.addWidget(self.browse_button)
        self.python_button = QPushButton("Python…")
        self.python_button.setToolTip(self.python)
        self.python_button.clicked.connect(self._select_python)
        row.addWidget(self.python_button)
        self.run_button = QPushButton("Run")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._run_script)
        row.addWidget(self.run_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.runner.stop)
        row.addWidget(self.stop_button)
        return bar

    def _components_panel(self):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumWidth(360)
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 14, 14, 12)
        box.setSpacing(13)
        header = QHBoxLayout()
        title = QLabel("Components")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.component_count = QLabel()
        self.component_count.setObjectName("muted")
        header.addWidget(self.component_count)
        header.addStretch()
        self.add_button = QPushButton("Add component")
        self.add_button.clicked.connect(self._add_component)
        header.addWidget(self.add_button)
        box.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        deck = QWidget()
        deck_layout = QVBoxLayout(deck)
        deck_layout.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        deck_layout.addLayout(self.grid)
        deck_layout.addStretch()
        scroll.setWidget(deck)
        box.addWidget(scroll, 1)
        return panel

    def _console_panel(self):
        panel = QFrame()
        panel.setObjectName("consolePanel")
        panel.setMinimumWidth(285)
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 14, 14, 12)
        box.setSpacing(11)
        header = QHBoxLayout()
        title = QLabel("Console")
        title.setObjectName("sectionTitle")
        header.addWidget(title, 1)
        self.run_status = QLabel()
        self.run_status.setObjectName("runStatus")
        header.addWidget(self.run_status)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.console.clear())
        header.addWidget(clear)
        box.addLayout(header)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2500)
        self.console.setPlaceholderText("Script output appears here.")
        box.addWidget(self.console, 1)

        input_row = QHBoxLayout()
        self.console_input = QLineEdit()
        self.console_input.setPlaceholderText("Send input to the script")
        self.console_input.setAccessibleName("Script console input")
        self.console_input.returnPressed.connect(self._send_console_input)
        input_row.addWidget(self.console_input, 1)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_console_input)
        input_row.addWidget(self.send_button)
        box.addLayout(input_row)
        return panel

    def _redraw_components(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.cards.clear()
        for index, component in enumerate(self.board.components.values()):
            card = ComponentCard(component)
            card.edit.connect(self._edit_component)
            card.remove.connect(self._remove_component)
            card.interact.connect(self._interact)
            card.motion_changed.connect(self.board.set_motion)
            card.sensor_changed.connect(self._configure_sensor)
            card.remote_pressed.connect(self.board.press_remote)
            card.refresh(self.board)
            card.menu_button.setEnabled(not self.session.busy)
            key = component.id if component.pin is None else component.pin
            self.cards[key] = card
            self.grid.addWidget(card, index // 2, index % 2)
        if not self.cards:
            empty = QLabel("Add a component or load the demo.")
            empty.setObjectName("empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(empty, 0, 0, 1, 2)
        self.component_count.setText(str(len(self.cards)))
        self.add_button.setEnabled(not self.session.busy)
        self._update_audio()
        if any(card.component.kind == "seven_segment" for card in self.cards.values()):
            self.display_repaint.start()
        else:
            self.display_repaint.stop()
        self._remember_layout()

    def _available_pins(self, except_id=None):
        return set(BCM_TO_BOARD) - {
            pin for item in self.board.components.values() if item.id != except_id for pin in item.pins
        }

    def _edit_component(self, component_id=None):
        previous = self.board.components.get(component_id)
        used_addresses = {
            (item.bus, item.address) for item in self.board.components.values()
            if item.id != component_id and item.kind in I2C_ADDRESSES
        }
        dialog = ComponentDialog(self._available_pins(component_id), previous, self, used_addresses=used_addresses)
        if dialog.exec() != QDialog.DialogCode.Accepted or self.session.busy:
            return
        kind, pin, name, active_low, green_pin, blue_pin, bus, address = dialog.values()
        if not name:
            label = COMPONENT_NAMES[kind]
            labels = {item.name for item in self.board.components.values() if item != previous}
            name = label
            number = 2
            while name in labels:
                name = f"{label} {number}"
                number += 1
        component = Component(
            component_id or uuid4().hex, kind, pin, name, active_low, green_pin, blue_pin, bus, address,
            **dialog.gpio_options(),
        )
        self.board.put_component(component)
        self._redraw_components()

    def _add_component(self):
        self._edit_component()

    def _remove_component(self, component_id):
        timer = self.pulse_timers.pop(component_id, None)
        if timer:
            timer.stop()
            timer.deleteLater()
        self.board.remove_component(component_id)
        self._redraw_components()

    def _interact(self, component_id, active):
        component = self.board.components[component_id]
        self.board.set_input(component_id, active)
        if component.kind == "pir":
            timer = self.pulse_timers.get(component_id)
            if timer is None:
                timer = QTimer(self, singleShot=True, interval=2000)
                timer.timeout.connect(lambda: self.board.set_input(component_id, False))
                self.pulse_timers[component_id] = timer
            timer.start()

    def _update_pin(self, pin):
        component = self.board.component_at(pin)
        card = self.cards.get(component.pin) if component else None
        if card and component.kind not in ("dht11", "ultrasonic"):
            card.refresh(self.board)
        if component and component.kind == "buzzer":
            self._update_audio()

    def _configure_sensor(self, component_id, values):
        self.board.gpio_devices[component_id].configure(*values)

    def _refresh_displays(self):
        for card in self.cards.values():
            if card.component.kind == "seven_segment":
                card.refresh(self.board)

    def _update_audio(self):
        self.audio.set_tones({
            item.id: tone for item in self.board.components.values() if item.kind == "buzzer"
            if (tone := self.board.buzzer_tone(item.pin)) is not None
        })

    def _update_i2c(self, component_id):
        self.i2c_updates.add(component_id)
        if not self.i2c_repaint.isActive():
            self.i2c_repaint.start()

    def _refresh_i2c(self):
        for component_id in self.i2c_updates:
            if card := self.cards.get(component_id):
                card.refresh(self.board)
        self.i2c_updates.clear()

    def _send_edge(self, channel, registration):
        self.session.send_edge(channel, registration)

    def _send_pulses(self, pin, pulses, delay):
        self.session.send_pulses(pin, pulses, delay)

    def _replace_layout(self, components, path=None):
        self.board.replace_components(components)
        for timer in self.pulse_timers.values():
            timer.stop()
            timer.deleteLater()
        self.pulse_timers.clear()
        self.layout_path = path
        self._redraw_components()

    def _remember_layout(self):
        self.settings.setValue("workspace/layout", layout_json(self.board.components.values()))
        self.settings.setValue("workspace/layoutPath", self.layout_path or "")
        self.settings.setValue("workspace/scriptPath", self.script.text())

    def _restore_layout(self):
        snapshot = self.settings.value("workspace/layout", "", type=str)
        try:
            components = parse_layout(snapshot)
        except ValueError:
            self._load_demo()
            return
        path = self.settings.value("workspace/layoutPath", "", type=str)
        self.script.setText(self.settings.value("workspace/scriptPath", "", type=str))
        self._replace_layout(components, path or None)

    def _load_demo(self):
        self.script.setText(str(PROJECT / "examples" / "motion_lights.py"))
        self._replace_layout(DEMO_COMPONENTS)

    def _new_layout(self):
        self._replace_layout([])

    def _open_layout(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open layout", self.layout_path or "", "Layout files (*.json);;All files (*)"
        )
        if path and not self.session.busy:
            try:
                self._replace_layout(load_layout(path), path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Could not open layout", str(error))

    def _save_layout(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save layout", self.layout_path or "layout.json", "Layout files (*.json)")
        if path:
            if not Path(path).suffix:
                path += ".json"
            output = QSaveFile(path)
            data = layout_json(self.board.components.values()).encode("utf-8")
            if not output.open(QIODevice.OpenModeFlag.WriteOnly) or output.write(data) != len(data) or not output.commit():
                QMessageBox.warning(self, "Could not save layout", output.errorString())
                return
            self.layout_path = path
            self._remember_layout()

    def _browse_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Python script", self.script.text(), "Python scripts (*.py)")
        if path:
            self.script.setText(path)
            self._remember_layout()

    def _select_python(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Python interpreter", self.python)
        if path:
            self.python = path
            self.python_button.setToolTip(path)

    def _run_script(self):
        if self.session.busy:
            return
        path = Path(self.script.text().strip()).expanduser().absolute()
        if not path.is_file() or path.suffix.lower() != ".py":
            self._append_console("[Choose an existing Python script.]\n")
            return
        self.console.clear()
        self.console_input.clear()
        self.runner.launch(self.python, path)

    def _show_external_status(self, connected):
        self._show_run_status("External script" if connected else "Idle")
        self.console.setPlaceholderText(
            "The external script uses its own terminal." if connected else "Script output appears here."
        )

    def _show_run_status(self, status):
        idle = status == "Idle"
        if idle:
            self.audio.stop()
        self.run_status.setText(status)
        self.run_status.setStyleSheet(
            "color: #7fe4b2;" if status in ("Running", "External script") else "color: #a1afc4;"
        )
        for widget in (self.run_button, self.browse_button, self.script, self.python_button, self.demo_button, self.layout_button):
            widget.setEnabled(idle)
        self.add_button.setEnabled(idle)
        for card in self.cards.values():
            card.menu_button.setEnabled(idle)
        for widget in (self.stop_button, self.console_input, self.send_button):
            widget.setEnabled(status == "Running")

    def _append_console(self, text):
        scrollbar = self.console.verticalScrollBar()
        at_end = scrollbar.value() == scrollbar.maximum()
        cursor = QTextCursor(self.console.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        if at_end:
            scrollbar.setValue(scrollbar.maximum())

    def _send_console_input(self):
        if self.runner.running:
            line = self.console_input.text()
            self._append_console(line + "\n")
            self.runner.send_console(line)
            self.console_input.clear()

    def closeEvent(self, event):
        self._remember_layout()
        self.settings.sync()
        self.audio.stop()
        self.server.close()
        self.runner.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RPi Simulator")
    app.setStyle("Fusion")
    app.setStyleSheet(Path(__file__).with_name("theme.qss").read_text(encoding="utf-8"))
    window = SimulatorWindow()
    window.show()
    return app.exec()
