"""Synthesize virtual buzzer tones on the system's default audio output."""

import math
from array import array
from dataclasses import dataclass

from PySide6 import QtMultimedia
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from .board import BuzzerTone

Audio = QtMultimedia.QtAudio if hasattr(QtMultimedia, "QtAudio") else QtMultimedia.QAudio
PCM = {
    QAudioFormat.SampleFormat.UInt8: ("B", 128, 127),
    QAudioFormat.SampleFormat.Int16: ("h", 0, 32767),
    QAudioFormat.SampleFormat.Int32: ("i", 0, 2147483647),
    QAudioFormat.SampleFormat.Float: ("f", 0, 1.0),
}


@dataclass
class Voice:
    tone: BuzzerTone
    phase: float = 0.0
    minimum_frames: int = 0


class BuzzerAudio(QObject):
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tones = {}
        self.voices = {}
        self.sink = None
        self.output = None
        self.pending = b""
        self.error = ""
        self.timer = QTimer(self, interval=8, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._pump)

    def set_tones(self, tones):
        if tones == self.tones:
            return
        previous = self.tones
        self.tones = tones
        if self.sink is None and (not tones or not self._open()):
            return
        for component_id, tone in tones.items():
            if previous.get(component_id) != tone or component_id not in self.voices:
                voice = self.voices.get(component_id, Voice(tone))
                voice.tone = tone
                # A very short digital pulse should still produce an audible beep.
                voice.minimum_frames = self.format.sampleRate() // 20 if tone.duty is None else 0
                self.voices[component_id] = voice
        self._pump()

    def stop(self):
        self.tones.clear()
        self._close()

    def _open(self):
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self._fail("No default audio output device is available.")
            return False
        self.format = device.preferredFormat()
        if not self.format.isValid() or self.format.sampleFormat() not in PCM:
            self._fail("The default audio output has an unsupported sample format.")
            return False
        self.sink = QAudioSink(device, self.format, self)
        self.target_bytes = self.format.bytesForDuration(40_000)
        self.sink.setBufferSize(self.target_bytes)
        self.output = self.sink.start()
        if self.output is None or self.sink.state() == Audio.State.StoppedState:
            self._fail("Could not start the default audio output.")
            return False
        self.error = ""
        self.timer.start()
        return True

    def _close(self, drain=False):
        self.timer.stop()
        self.voices.clear()
        self.pending = b""
        if self.sink is not None:
            if drain:
                self.sink.stop()
            else:
                self.sink.reset()
            self.output = None
            self.sink.deleteLater()
            self.sink = None

    def _fail(self, message):
        self._close()
        if self.error != message:
            self.error = message
            self.failed.emit(message)

    def _pump(self):
        sink = self.sink
        if sink is None:
            return
        if sink.state() == Audio.State.StoppedState:
            self._fail("The default audio output stopped unexpectedly.")
            return
        self.voices = {
            key: voice for key, voice in self.voices.items() if key in self.tones or voice.minimum_frames > 0
        }
        free = sink.bytesFree()
        if not self.pending and not self.voices:
            if sink.state() == Audio.State.IdleState or free == sink.bufferSize():
                self._close(drain=True)
            return
        if free <= 0:
            return
        if not self.pending:
            queued = sink.bufferSize() - free
            available = min(free, self.target_bytes - queued)
            frames = available // self.format.bytesPerFrame()
            if frames <= 0:
                return
            self.pending = self._render(frames)
        written = self.output.write(self.pending[:free])
        if written < 0:
            self._fail("Could not write to the default audio output.")
        else:
            self.pending = self.pending[written:]

    def _render(self, frames):
        rate = self.format.sampleRate()
        mixed = array("d", [0.0]) * frames
        amplitude = 0.20 / len(self.voices)
        for key, voice in self.voices.items():
            frequency, duty = voice.tone.frequency, voice.tone.duty
            length = frames if key in self.tones else min(frames, voice.minimum_frames)
            voice.minimum_frames = max(0, voice.minimum_frames - length)
            if frequency >= rate / 2:
                continue
            step = math.tau * frequency / rate
            # The fundamental of the PWM waveform follows sin(pi * duty).
            volume = amplitude if duty is None else amplitude * math.sin(math.pi * duty)
            phase = voice.phase
            for index in range(length):
                mixed[index] += volume * math.sin(phase)
                phase += step
            voice.phase = phase % math.tau

        code, center, scale = PCM[self.format.sampleFormat()]
        channels = self.format.channelCount()
        pcm = array(code)
        for sample in mixed:
            value = sample if code == "f" else center + round(sample * scale)
            pcm.extend([value] * channels)
        return pcm.tobytes()
