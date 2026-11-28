"""Local sampling and capture of pulse trains from simulated GPIO devices."""

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field
from itertools import accumulate
from threading import Condition
from time import monotonic_ns


@dataclass
class PulseFrame:
    pulses: tuple
    delay_us: int = 0
    started_ns: int | None = None
    ends_ns: tuple = field(init=False)

    def __post_init__(self):
        self.ends_ns = tuple(
            time_us * 1000 for time_us in accumulate((width for _, width in self.pulses), initial=self.delay_us)
        )

    def level(self, now, idle):
        index = bisect_right(self.ends_ns, now - self.started_ns) - 1
        return self.pulses[index][0] if 0 <= index < len(self.pulses) else idle

    @property
    def deadline(self):
        return self.started_ns + self.ends_ns[-1]


class PulseInputs:
    def __init__(self):
        self.frames = {}
        self.condition = Condition()
        self.closed = False

    def deliver(self, pin, pulses, delay):
        frame = PulseFrame(tuple(tuple(pulse) for pulse in pulses), delay)
        with self.condition:
            self.frames.setdefault(pin, deque(maxlen=8)).append(frame)
            self.condition.notify_all()

    def clear(self, pin):
        with self.condition:
            self.frames.pop(pin, None)
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.frames.clear()
            self.condition.notify_all()

    def sample(self, pin, idle):
        with self.condition:
            queue = self.frames.get(pin)
            if self.closed:
                raise RuntimeError("The simulator has disconnected")
            now = monotonic_ns()
            while queue:
                frame = queue[0]
                if frame.started_ns is None:
                    frame.started_ns = now
                if now < frame.deadline:
                    return frame.level(now, idle)
                queue.popleft()
            return idle

    def capture(self, pin, timeout):
        end = monotonic_ns() + round(timeout * 1_000_000_000)
        with self.condition:
            while not self.closed:
                now = monotonic_ns()
                queue = self.frames.get(pin)
                wake_at = end
                if queue:
                    frame = queue[0]
                    if frame.started_ns is None:
                        frame.started_ns = now
                    if frame.deadline <= now:
                        return queue.popleft().pulses
                    wake_at = min(end, frame.deadline)
                if wake_at <= now:
                    return None
                self.condition.wait((wake_at - now) / 1_000_000_000)
            raise RuntimeError("The simulator has disconnected")
