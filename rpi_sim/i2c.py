"""Digital I²C bus driven by a deterministic clock, independent of Qt and transport."""

import errno
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Timing:
    low: int
    high: int
    hold_start: int
    setup_start: int
    setup_stop: int
    bus_free: int


MODES = {
    "standard": Timing(5000, 5000, 4000, 4700, 4000, 4700),
    "fast": Timing(1300, 1200, 600, 600, 600, 1300),
    "fast-plus": Timing(500, 500, 260, 260, 260, 500),
    "high-speed": Timing(160, 135, 160, 160, 160, 160),
    "ultra-fast": Timing(100, 100, 50, 50, 50, 80),
}


class I2CTarget:
    """Byte-level target interface. Times are simulated nanoseconds."""

    general_call = False
    ultra_fast = False
    device_id = None

    def begin(self, read, now):
        return True

    def write(self, value, now):
        return True

    def read(self, now):
        return 0xFF

    def read_ack(self, acknowledged, now):
        pass

    def stop(self, now):
        pass

    def stretch(self, now):
        return 0

    def holds_sda(self):
        return False

    def holds_scl(self):
        return False

    def recovery_clock(self):
        pass


class DeviceID(I2CTarget):
    """Optional 24-bit identification at the reserved address 0x7C."""

    def __init__(self, targets):
        self.targets = targets
        self.identity = None
        self.offset = 0
        self.selecting = False

    def supports(self, target):
        return target is not None and target.device_id is not None

    def begin(self, read, now):
        if read:
            return self.identity is not None
        self.stop(now)
        self.selecting = any(not tenbit and self.supports(target) for (tenbit, _), target in self.targets.items())
        return self.selecting

    def write(self, value, now):
        if not self.selecting:
            return False
        self.selecting = False
        target = self.targets.get((False, value >> 1))
        if not self.supports(target):
            return False
        self.identity = target.device_id.to_bytes(3, "big")
        return True

    def read(self, now):
        value = self.identity[self.offset]
        self.offset = (self.offset + 1) % 3
        return value

    def read_ack(self, acknowledged, now):
        if not acknowledged:
            self.stop(now)

    def stop(self, now):
        self.identity = None
        self.offset = 0
        self.selecting = False


@dataclass(frozen=True)
class Message:
    address: int
    read: bool
    tenbit: bool
    data: bytes
    length: int
    block: bool
    extra: int
    stop: bool
    nostart: bool

    @classmethod
    def parse(cls, record):
        if not isinstance(record, dict):
            raise ValueError("Expected an I²C message object")
        address = record.get("address")
        read, tenbit, block, stop, nostart = (
            record.get(key, False) for key in ("read", "tenbit", "block", "stop", "nostart")
        )
        if any(type(flag) is not bool for flag in (read, tenbit, block, stop, nostart)):
            raise ValueError("I²C message flags must be boolean")
        if type(address) is not int or not 0 <= address <= (0x3FF if tenbit else 0x7F):
            raise ValueError("I²C address is outside the selected address width")
        data = record.get("data", [])
        if not isinstance(data, list) or len(data) > 4096:
            raise ValueError("I²C write data must contain at most 4096 bytes")
        data = bytes(data)
        length, extra = record.get("length", 0), record.get("extra", 0)
        if type(length) is not int or not 0 <= length <= 4096 or type(extra) is not int or extra not in (0, 1):
            raise ValueError("Invalid I²C read length")
        if (read and data) or (not read and (length or block)) or (extra and not block) or (block and length):
            raise ValueError("I²C read and write arguments cannot be combined")
        if read and nostart and not (length or block):
            raise ValueError("An I²C read continuation needs data")
        return cls(address, read, tenbit, data, length, block, extra, stop, nostart)


class Wire:
    """Resolved digital SDA and SCL levels and a bounded transition log."""

    def __init__(self):
        self.now = 0
        self.scl = self.sda = 1
        self.timing = MODES["standard"]
        self.tracing = False
        self.trace = deque(maxlen=8192)

    def mark(self, event):
        if self.tracing:
            self.trace.append((self.now, self.scl, self.sda, event))

    def lines(self, scl, sda, event=""):
        if self.tracing and (event or scl != self.scl or sda != self.sda):
            self.trace.append((self.now, scl, sda, event))
        self.scl, self.sda = scl, sda

    def start(self, repeated):
        if repeated:
            self.lines(0, 1)
            self.now += self.timing.low
            self.lines(1, 1)
            self.now += self.timing.setup_start
        self.lines(1, 0, "RESTART" if repeated else "START")
        self.now += self.timing.hold_start
        self.lines(0, 0)

    def bit(self, value, event="", stretch=0, clock=None):
        low, high = clock or (self.timing.low, self.timing.high)
        self.lines(0, value)
        if stretch:
            self.mark("STRETCH")
        self.now += low + stretch
        self.lines(1, value, event)
        self.now += high
        self.lines(0, value)

    def stop(self):
        self.lines(0, 0)
        self.now += self.timing.low
        self.lines(1, 0)
        self.now += self.timing.setup_stop
        self.lines(1, 1, "STOP")
        self.now += self.timing.bus_free


@dataclass
class Controller:
    index: int
    steps: object
    step: tuple
    clock: tuple | None


class I2CBus:
    def __init__(self):
        self.targets = {}
        self._device_id = DeviceID(self.targets)
        self.mode = "standard"
        self.start_byte = False
        self.wire = Wire()
        self._selected = ()
        self._touched = set()
        self._ten_high = None
        self._ten_address = None
        self._addressing = False
        self._started = False

    def attach(self, address, target, *, tenbit=False):
        if type(address) is not int:
            raise ValueError("Invalid I²C target address")
        valid = 0 <= address <= 0x3FF if tenbit else 0x08 <= address <= 0x77
        if not valid:
            raise ValueError("Invalid I²C target address")
        key = (tenbit, address)
        if key in self.targets:
            raise ValueError(f"I²C address 0x{address:X} is already in use")
        identity = target.device_id
        if identity is not None and (type(identity) is not int or not 0 <= identity <= 0xFFFFFF):
            raise ValueError("I²C Device ID must be a 24-bit value")
        self.targets[key] = target

    def configure(self, mode=None, trace=None, start_byte=None):
        if mode is not None and (not isinstance(mode, str) or mode not in MODES):
            raise ValueError("Unknown I²C speed mode")
        if any(value is not None and type(value) is not bool for value in (trace, start_byte)):
            raise ValueError("I²C trace and START byte options must be boolean")
        if mode is not None:
            self.mode = mode
        if trace is not None:
            self.wire.tracing = trace
        if start_byte is not None:
            self.start_byte = start_byte

    def trace(self, limit=256, clear=False):
        if type(limit) is not int or not 0 <= limit <= 1024 or type(clear) is not bool:
            raise ValueError("Trace limit must be between 0 and 1024 events")
        items = list(self.wire.trace)[-limit:] if limit else []
        if clear:
            self.wire.trace.clear()
        return items

    def transfer(self, records):
        outcome = self.contend([records])[0]
        if "errno" in outcome:
            raise OSError(outcome["errno"], outcome["error"])
        return outcome["reads"]

    def contend(self, transfers, clocks=None):
        """Run transfers beginning on the same clock edge, with one result per controller."""
        if not isinstance(transfers, list) or not 1 <= len(transfers) <= 8:
            raise ValueError("Choose one to eight I²C controllers")
        if len(transfers) > 1 and self.mode == "ultra-fast":
            raise ValueError("Ultra Fast-mode uses one controller")
        timing = MODES["fast" if self.mode == "high-speed" else self.mode]
        if clocks is None:
            clocks = [None] * len(transfers)
        if not isinstance(clocks, list) or len(clocks) != len(transfers):
            raise ValueError("Supply one clock per I²C controller")
        controllers = []
        budget = 8192
        for index, (records, clock) in enumerate(zip(transfers, clocks)):
            if clock is not None and (
                not isinstance(clock, list) or len(clock) != 2
                or any(type(value) is not int or value > 1_000_000_000 for value in clock)
                or clock[0] < timing.low or clock[1] < timing.high
            ):
                raise ValueError("Controller clock must respect the speed mode's low and high periods")
            if not isinstance(records, list) or not 1 <= len(records) <= 42:
                raise ValueError("An I²C transfer must contain one to 42 messages")
            messages = [Message.parse(record) for record in records]
            budget -= sum(len(part.data) + part.length + (34 if part.block else 0) for part in messages)
            if budget < 0:
                raise ValueError("I²C requests are limited to 8192 data bytes")
            if self.mode == "ultra-fast" and any(part.read for part in messages):
                raise ValueError("Ultra Fast-mode supports writes only")
            for offset, part in enumerate(messages):
                if part.nostart:
                    previous = messages[offset - 1] if offset else None
                    if previous is None or previous.stop or (
                        previous.address, previous.tenbit, previous.read
                    ) != (part.address, part.tenbit, part.read):
                        raise ValueError("A message without START must continue the previous transfer")
            steps = self._steps(messages, index if len(transfers) > 1 else 1)
            controllers.append(Controller(index, steps, next(steps), tuple(clock) if clock is not None else None))

        self.wire.timing = timing
        results = [None] * len(controllers)
        try:
            if self.mode != "ultra-fast":
                self._check_clock()
                if self._sda_holders():
                    self.wire.lines(1, 0, "BUS BUSY")
                    raise OSError(errno.EBUSY, "I²C SDA is held low")
            while controllers:
                kind = controllers[0].step[0]
                if any(controller.step[0] != kind for controller in controllers):
                    raise OSError(errno.EPROTO, "Controllers disagreed on an I²C bus condition")
                value = self._step(kind, controllers, results)
                remaining = []
                for controller in controllers:
                    try:
                        controller.step = controller.steps.send(value)
                    except StopIteration as done:
                        results[controller.index] = {"reads": done.value}
                    finally:
                        if results[controller.index] is None:
                            remaining.append(controller)
                controllers = remaining
        except OSError as error:
            for controller in controllers:
                results[controller.index] = {"errno": error.errno, "error": error.strerror}
        finally:
            if self._started:
                self._stop()
        return results

    def _steps(self, messages, controller_code):
        reads = []
        previous_ten = None
        for index, message in enumerate(messages):
            fresh = index == 0 or messages[index - 1].stop
            if not message.nostart:
                yield "start", None
                if fresh and self.mode == "high-speed":
                    yield "hs-code", 0x08 | controller_code
                    yield "start", None
                elif fresh and self.start_byte and self.mode != "ultra-fast":
                    yield "start-byte", 0x01
                    yield "start", None
                address = message.address
                if message.tenbit:
                    header = 0xF0 | ((address >> 7) & 0x06)
                    if not message.read or address != previous_ten:
                        yield "send", header
                        yield "send", address & 0xFF
                        if message.read:
                            yield "start", None
                    if message.read:
                        yield "send", header | 1
                    previous_ten = address
                else:
                    yield "send", (address << 1) | message.read
                    previous_ten = None

            data = []
            if message.read:
                length = message.length
                if message.block:
                    count = yield "read", None
                    if not 1 <= count <= 32:
                        yield "reply", 1
                        raise OSError(errno.EPROTO, f"Invalid SMBus block length {count}")
                    data.append(count)
                    yield "reply", 0
                    length = count + message.extra
                continued = index + 1 < len(messages) and messages[index + 1].nostart
                for offset in range(length):
                    data.append((yield "read", None))
                    yield "reply", int(offset == length - 1 and not continued)
            else:
                for value in message.data:
                    yield "send", value
            reads.append(data)
            if message.stop or index == len(messages) - 1:
                yield "stop", None
                previous_ten = None
        return reads

    def _step(self, kind, controllers, results):
        if kind == "start":
            self.wire.start(self._started)
            self._started = self._addressing = True
            self._selected = ()
            self._ten_high = None
        elif kind in ("send", "hs-code", "start-byte"):
            value = self._arbitrate(controllers, results, 8, self._stretch())
            if kind == "hs-code" or kind == "start-byte":
                self.wire.bit(1, "NACK", clock=self._clock(controllers))
                self.wire.mark("HS CODE" if kind == "hs-code" else "START BYTE")
                if kind == "hs-code":
                    self.wire.timing = MODES["high-speed"]
            else:
                address = self._addressing
                if address:
                    acknowledged = self._address(value)
                else:
                    responses = [target.write(value, self.wire.now) for target in self._selected]
                    acknowledged = any(responses)
                if self.mode == "ultra-fast":
                    self.wire.bit(1, "UFm")
                else:
                    self.wire.bit(int(not acknowledged), "ACK" if acknowledged else "NACK",
                                  clock=self._clock(controllers))
                    if not acknowledged:
                        code = errno.ENXIO if address else getattr(errno, "EREMOTEIO", errno.EIO)
                        raise OSError(code, "I²C address not acknowledged" if address else "I²C data not acknowledged")
        elif kind == "read":
            delay = self._stretch()
            value = self._selected[0].read(self.wire.now + delay)
            clock = self._clock(controllers)
            for shift in range(7, -1, -1):
                self.wire.bit((value >> shift) & 1, stretch=delay if shift == 7 else 0, clock=clock)
            return value
        elif kind == "reply":
            value = self._arbitrate(controllers, results, 1)
            for target in self._selected:
                target.read_ack(not value, self.wire.now)
        else:
            self._stop()

    def _arbitrate(self, controllers, results, width, stretch=0):
        value = 0
        for shift in range(width - 1, -1, -1):
            bit = (controllers[0].step[1] >> shift) & 1
            if bit and len(controllers) > 1:
                bit = min((controller.step[1] >> shift) & 1 for controller in controllers)
            event = ("NACK" if bit else "ACK") if width == 1 else ""
            self.wire.bit(bit, event, stretch if shift == width - 1 else 0, self._clock(controllers))
            value = (value << 1) | bit
            if bit == 0 and len(controllers) > 1:
                for controller in controllers[:]:
                    if (controller.step[1] >> shift) & 1:
                        self.wire.mark(f"ARBITRATION LOST {controller.index + 1}")
                        results[controller.index] = {"errno": errno.EAGAIN, "error": "I²C arbitration lost"}
                        controllers.remove(controller)
        return value

    def _clock(self, controllers):
        if self.wire.timing == MODES["high-speed"] or not any(controller.clock for controller in controllers):
            return None
        default = self.wire.timing.low, self.wire.timing.high
        clocks = [controller.clock or default for controller in controllers]
        return max(low for low, _ in clocks), min(high for _, high in clocks)

    def _eligible(self, target):
        return self.mode != "ultra-fast" or target.ultra_fast

    def _address(self, value):
        if self._ten_high is None and value in (0xF8, 0xF9):
            self._ten_address = None
            return self._select(self._device_id, read=bool(value & 1))
        self._device_id.stop(self.wire.now)
        if self._ten_high is not None:
            address = self._ten_high | value
            self._ten_address = address
            self._ten_high = None
            return self._select(self.targets.get((True, address)), read=False)
        if value & 0xF8 == 0xF0:
            high = (value & 0x06) << 7
            if not value & 1:
                self._ten_address = None
                self._ten_high = high
                return any(
                    tenbit and (address & 0x300) == high and self._eligible(target)
                    for (tenbit, address), target in self.targets.items()
                )
            address = self._ten_address
            return self._select(
                self.targets.get((True, address)) if address is not None and address & 0x300 == high else None,
                read=True,
            )
        self._ten_address = None
        if value == 0:
            self._addressing = False
            self._selected = tuple(
                target for (tenbit, _), target in self.targets.items()
                if not tenbit and target.general_call and self._eligible(target) and target.begin(False, self.wire.now)
            )
            self._touched.update(self._selected)
            return bool(self._selected)
        return self._select(self.targets.get((False, value >> 1)), read=bool(value & 1))

    def _select(self, target, read):
        self._addressing = False
        if target is None or not self._eligible(target) or not target.begin(read, self.wire.now):
            return False
        self._selected = (target,)
        self._touched.add(target)
        return True

    def _stretch(self):
        return max((target.stretch(self.wire.now) for target in self._selected), default=0)

    def _sda_holders(self):
        return tuple(target for target in self.targets.values() if target.holds_sda())

    def _check_clock(self):
        if any(target.holds_scl() for target in self.targets.values()):
            self.wire.lines(0, int(not self._sda_holders()), "BUS BUSY")
            raise OSError(errno.EBUSY, "I²C SCL is held low")

    def recover(self):
        """Pulse SCL up to nine times to release a target holding SDA low."""
        self.wire.timing = MODES["standard"]
        self._check_clock()
        for _ in range(9):
            holders = self._sda_holders()
            if not holders:
                self.wire.stop()
                return
            self.wire.bit(0, "RECOVERY")
            for target in holders:
                target.recovery_clock()
        if self._sda_holders():
            self.wire.lines(1, 0, "BUS BUSY")
            raise OSError(errno.EBUSY, "I²C bus recovery did not release SDA")
        self.wire.stop()

    def _stop(self):
        self.wire.stop()
        if self.mode == "high-speed":
            self.wire.timing = MODES["fast"]
        for target in self._touched:
            target.stop(self.wire.now)
        self._touched.clear()
        self._selected = ()
        self._ten_high = self._ten_address = None
        self._started = self._addressing = False
