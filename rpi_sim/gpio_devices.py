"""GPIO peripherals with multiplexed outputs or microsecond pulse trains."""

from math import isfinite


SEGMENT_PINS = (11, 4, 23, 8, 7, 10, 18, 25)
DIGIT_PINS = (22, 27, 17, 24)
IR_KEYS = {
    "LEFT": 0x00FF22DD, "RIGHT": 0x00FFC23D, "UP": 0x00FF629D, "DOWN": 0x00FFA857,
    "OK": 0x00FF02FD, "1": 0x00FF6897, "2": 0x00FF9867, "3": 0x00FFB04F,
    "4": 0x00FF30CF, "5": 0x00FF18E7, "6": 0x00FF7A85,
    "7": 0x00FF10EF, "8": 0x00FF38C7, "9": 0x00FF5AA5,
    "*": 0x00FF42BD, "0": 0x00FF4AB5, "#": 0x00FF52AD,
}


class SevenSegment:
    def __init__(self):
        self.masks = [0] * 4
        self.active = [False] * 4
        self.disabled_at = [None] * 4

    def observe(self, segments, selected, now_ns):
        for digit, enabled in enumerate(selected):
            if enabled:
                self.masks[digit] = segments
            if self.active[digit] and not enabled:
                self.disabled_at[digit] = now_ns
            self.active[digit] = enabled

    def visible(self, now_ns):
        return tuple(
            mask if active or (stopped is not None and now_ns - stopped < 120_000_000) else 0
            for mask, active, stopped in zip(self.masks, self.active, self.disabled_at)
        )


class DHT11:
    idle = 1

    def __init__(self):
        self.temperature = 24.0
        self.humidity = 50
        self.low_since = None
        self.armed = False

    def drive(self, level, now_ns):
        if level == 0:
            if self.low_since is None:
                self.low_since = now_ns
            self.armed = False
        elif self.low_since is not None:
            self.armed = now_ns - self.low_since >= 18_000_000
            self.low_since = None

    def release(self, now_ns):
        if self.low_since is not None:
            self.drive(1, now_ns)
        armed, self.armed = self.armed, False
        if not armed:
            return None
        whole, fractional = divmod(round(10 * self.temperature), 10)
        data = [self.humidity, 0, whole, fractional]
        data.append(sum(data) & 0xFF)
        pulses = [(0, 80), (1, 80)]
        for byte in data:
            for shift in range(7, -1, -1):
                pulses.extend(((0, 50), (1, 70 if byte & (1 << shift) else 27)))
        pulses.append((0, 50))
        return pulses

    def configure(self, temperature, humidity):
        if not isinstance(temperature, (float, int)) or not isfinite(temperature) or not 0 <= temperature <= 50:
            raise ValueError("DHT11 temperature must be between 0 and 50 °C")
        if type(humidity) is not int or not 0 <= humidity <= 100:
            raise ValueError("DHT11 humidity must be an integer between 0 and 100%")
        self.temperature, self.humidity = temperature, humidity

    def reset(self):
        self.low_since = None
        self.armed = False


class Ultrasonic:
    idle = 0

    def __init__(self):
        self.distance = 50.0
        self.no_echo = False
        self.high_since = None

    def drive(self, level, now_ns):
        if level:
            if self.high_since is None:
                self.high_since = now_ns
            return None
        first, self.high_since = self.high_since, None
        if first is None or now_ns - first < 10_000 or self.no_echo:
            return None
        width = round(self.distance * 2_000_000 / 34300)
        return [(1, width)]

    def configure(self, distance, no_echo):
        if not isinstance(distance, (float, int)) or not isfinite(distance) or not 2 <= distance <= 400:
            raise ValueError("Ultrasonic distance must be between 2 and 400 cm")
        if type(no_echo) is not bool:
            raise ValueError("No echo must be a boolean")
        self.distance, self.no_echo = distance, no_echo

    def reset(self):
        self.high_since = None


class Infrared:
    idle = 1

    def __init__(self):
        self.last_key = None

    def press(self, key):
        value = IR_KEYS[key]
        self.last_key = key
        pulses = [(0, 9000), (1, 4500)]
        for shift in range(31, -1, -1):
            pulses.extend(((0, 563), (1, 1688 if value & (1 << shift) else 563)))
        pulses.append((0, 563))
        return pulses
