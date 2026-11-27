"""Qt process controller for an exercise and its simulator connection."""

import codecs
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from rpi_sim_client.protocol import MessageReader, ProtocolError, encode_message

from .session import unpack_request


class ScriptProcess(QObject):
    console_output = Signal(str)
    status_changed = Signal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_requests)
        self.process.readyReadStandardError.connect(self._read_console)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error_occurred)
        self.stop_timer = QTimer(self, singleShot=True, interval=1200)
        self.stop_timer.timeout.connect(self.process.kill)
        self.reader = MessageReader()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.stopping = False

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def launch(self, python, script):
        if not self.session.claim(self._send):
            return
        self.reader = MessageReader()
        self.decoder.reset()
        self.stopping = False
        self.process.setWorkingDirectory(str(script.parent))
        bootstrap = Path(__file__).with_name("run_script.py")
        self.status_changed.emit("Running")
        self.process.start(python, ["-B", "-u", "-X", "utf8", str(bootstrap), str(script)])

    def send_console(self, line):
        self._send({"type": "console", "text": line})

    def stop(self):
        if self.running and not self.stopping:
            self.stopping = True
            self.status_changed.emit("Stopping")
            self._send({"type": "stop"})
            self.stop_timer.start()

    def close(self):
        if self.running:
            self.stopping = True
            self.process.kill()
            self.process.waitForFinished(1000)

    def _send(self, message):
        if self.running:
            self.process.write(encode_message(message))

    def _read_requests(self):
        try:
            for message in self.reader.feed(bytes(self.process.readAllStandardOutput())):
                self._send(self.session.handle_request(*unpack_request(message)))
        except ProtocolError as error:
            self.reader = MessageReader()
            self.console_output.emit(f"\n[GPIO connection error: {error}]\n")
            self.process.kill()

    def _read_console(self):
        data = self.decoder.decode(bytes(self.process.readAllStandardError()))
        if data:
            self.console_output.emit(data)

    def _finished(self, exit_code, exit_status):
        self.stop_timer.stop()
        self._read_requests()
        self._read_console()
        tail = self.decoder.decode(b"", final=True)
        if self.stopping:
            summary = "Stopped"
        elif exit_status == QProcess.ExitStatus.CrashExit:
            summary = "Process crashed"
        else:
            summary = f"Exited with code {exit_code}"
        self.session.release(self._send)
        self.console_output.emit(f"{tail}\n[{summary}]\n")
        self.status_changed.emit("Idle")

    def _error_occurred(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.stop_timer.stop()
            self.session.release(self._send)
            self.console_output.emit(f"[Could not start Python: {self.process.errorString()}]\n")
            self.status_changed.emit("Idle")
