"""Digital models for a PCF8574-backed character LCD and an MPU6050."""

from collections import deque

from .i2c import I2CTarget


class HD44780:
    ROWS = (0x00, 0x40)
    ADDRESSES = tuple(range(0x28)) + tuple(range(0x40, 0x68))

    def __init__(self):
        self.ddram = bytearray(b" " * 0x80)
        self.cgram = bytearray(64)
        self.four_bit = False
        self.pending = None
        self.read_low = False
        self.address = 0
        self.character_ram = False
        self.increment = True
        self.shift_on_write = False
        self.shift = 0
        self.display_on = False
        self.cursor_on = False
        self.blink_on = False
        self.busy_until = 0

    def latch(self, nibble, data, now):
        if not self.four_bit:
            return self._receive(nibble << 4, data, now)
        if self.pending is None:
            self.pending = nibble << 4
            return False
        value = self.pending | nibble
        self.pending = None
        return self._receive(value, data, now)

    def _receive(self, value, data, now):
        self.busy_until = now + (1_520_000 if not data and value in (1, 2, 3) else 37_000)
        if data:
            memory = self.cgram if self.character_ram else self.ddram
            memory[self.address] = value & 0x1F if self.character_ram else value
            self._step_address(1 if self.increment else -1)
            if self.shift_on_write and not self.character_ram:
                self.shift += 1 if self.increment else -1
        elif value & 0x80:
            self.character_ram = False
            self.address = value & 0x7F
        elif value & 0x40:
            self.character_ram = True
            self.address = value & 0x3F
        elif value & 0x20:
            self.four_bit = not bool(value & 0x10)
            self.read_low = False
        elif value & 0x10:
            direction = 1 if value & 4 else -1
            if value & 8:
                self.shift -= direction
            else:
                self._step_address(direction)
        elif value & 8:
            self.display_on, self.cursor_on, self.blink_on = (bool(value & flag) for flag in (4, 2, 1))
        elif value & 4:
            self.increment = bool(value & 2)
            self.shift_on_write = bool(value & 1)
        elif value & 2:
            self.address = self.shift = 0
            self.character_ram = False
        elif value & 1:
            self.ddram[:] = b" " * len(self.ddram)
            self.address = self.shift = 0
            self.character_ram = False
        return True

    def _step_address(self, step):
        if self.character_ram:
            self.address = (self.address + step) & 0x3F
        elif self.address in self.ADDRESSES:
            index = (self.ADDRESSES.index(self.address) + step) % len(self.ADDRESSES)
            self.address = self.ADDRESSES[index]
        else:
            self.address = (self.address + step) & 0x7F

    def peek_nibble(self, data, now):
        if data:
            value = (self.cgram if self.character_ram else self.ddram)[self.address]
        else:
            value = self.address | (0x80 if now < self.busy_until else 0)
        return (value >> (0 if self.read_low else 4)) & 0x0F

    def finish_read(self, data):
        complete = self.read_low or not self.four_bit
        self.read_low = not self.read_low if self.four_bit else False
        if data and complete:
            self._step_address(1 if self.increment else -1)

    def visible_addresses(self):
        return tuple(tuple(base + (column + self.shift) % 40 for column in range(16)) for base in self.ROWS)

    def cell(self, address):
        value = self.ddram[address] if self.display_on else 0x20
        if value < 16:
            start = (value % 8) * 8
            return tuple(self.cgram[start:start + 8])
        if value == 0xDF:
            return "°"
        return chr(value) if 0x20 <= value <= 0x7E else "·"

    def lines(self):
        return tuple("".join(cell if isinstance(cell := self.cell(address), str) else "▦"
                             for address in row) for row in self.visible_addresses())


class LCD1602(I2CTarget):
    """An eight-bit PCF8574 latch wired to the LCD as in the exercise."""

    RS, RW, ENABLE, BACKLIGHT = 1, 2, 4, 8

    def __init__(self, changed):
        self.changed = changed
        self.port = 0xFF
        self.enable_risen = False
        self.lcd = HD44780()

    @property
    def backlight(self):
        return bool(self.port & self.BACKLIGHT)

    def write(self, value, now):
        previous = self.port
        self.port = value
        changed = bool((previous ^ value) & self.BACKLIGHT)
        if value & self.ENABLE and not previous & self.ENABLE:
            self.enable_risen = True
        elif self.enable_risen and not value & self.ENABLE:
            self.enable_risen = False
            data = bool(previous & self.RS)
            if previous & self.RW:
                self.lcd.finish_read(data)
            else:
                changed = self.lcd.latch(previous >> 4, data, now) or changed
        if changed:
            self.changed()
        return True

    def read(self, now):
        if self.port & self.ENABLE and self.port & self.RW:
            nibble = self.lcd.peek_nibble(bool(self.port & self.RS), now)
            return self.port & (0x0F | nibble << 4)
        return self.port


class MPU6050(I2CTarget):
    WHO_AM_I = 0x75
    POWER = 0x6B
    USER_CTRL = 0x6A
    BANK_SEL, MEM_ADDR, MEM_DATA = 0x6D, 0x6E, 0x6F
    FIFO_HIGH, FIFO_LOW, FIFO_DATA = 0x72, 0x73, 0x74
    INT_STATUS = 0x3A
    ACCEL_CONFIG, GYRO_CONFIG, SAMPLE_DIV = 0x1C, 0x1B, 0x19
    ACCEL_DATA = 0x3B
    ACCEL_SCALE = (16384.0, 8192.0, 4096.0, 2048.0)
    GYRO_SCALE = (131.0, 65.5, 32.8, 16.4)
    FIFO_CAPACITY = 1024

    def __init__(self):
        self.acceleration = (0.0, 0.0, 1.0)
        self.rotation = (0.0, 0.0, 0.0)
        self.reset()

    def reset(self):
        self.registers = bytearray(128)
        self.registers[self.WHO_AM_I] = 0x68
        self.registers[self.POWER] = 0x40
        self.memory = bytearray(32 * 256)
        self.memory[16 * 256 + 6] = 1
        self.fifo = deque(maxlen=self.FIFO_CAPACITY)
        self.fifo_count = 0
        self.next_sample = None
        self.register = 0
        self.expect_register = True
        self.sample = bytes(14)

    def begin(self, read, now):
        self.expect_register = not read
        if read:
            self.sample = self._sample()
        return True

    def _sample(self):
        if self.registers[self.POWER] & 0x40:
            return bytes(14)
        accel = self.ACCEL_SCALE[(self.registers[self.ACCEL_CONFIG] >> 3) & 3]
        gyro = self.GYRO_SCALE[(self.registers[self.GYRO_CONFIG] >> 3) & 3]
        samples = [*(value * accel for value in self.acceleration),
                   (25.0 - 36.53) * 340,
                   *(value * gyro for value in self.rotation)]
        disabled = self.registers[0x6C]
        for index, mask in enumerate((0x20, 0x10, 0x08, 0, 0x04, 0x02, 0x01)):
            if disabled & mask:
                samples[index] = 0
        return b"".join(max(-32768, min(32767, round(value))).to_bytes(2, "big", signed=True) for value in samples)

    def _advance(self):
        if self.register not in (self.MEM_DATA, self.FIFO_DATA):
            self.register = (self.register + 1) & 0x7F

    def _memory_index(self):
        address = self.registers[self.MEM_ADDR]
        self.registers[self.MEM_ADDR] = (address + 1) & 0xFF
        return ((self.registers[self.BANK_SEL] & 0x1F) << 8) | address

    def _clear_fifo(self):
        self.fifo.clear()
        self.fifo_count = 0
        self.next_sample = None

    def _fifo_packet(self):
        sample = self._sample()
        if self.registers[self.USER_CTRL] & 0x80:
            # The driver's startup drains a DMP packet. Provide a stationary orientation
            # plus the current axes in the packet positions used by the supplied driver.
            packet = bytearray(42)
            packet[0:2] = b"\x40\x00"
            for source, dest in zip((8, 10, 12, 0, 2, 4), (16, 20, 24, 28, 32, 36)):
                packet[dest:dest + 2] = sample[source:source + 2]
            return packet
        enabled = self.registers[0x23]
        fields = ((0x80, 6, 8), (0x40, 8, 10), (0x20, 10, 12), (0x10, 12, 14), (0x08, 0, 6))
        return b"".join(sample[first:last] for mask, first, last in fields if enabled & mask)

    def _update_fifo(self, now):
        if not self.registers[self.USER_CTRL] & 0x40 or self.registers[self.POWER] & 0x40:
            self.next_sample = None
            return
        period = (1 + self.registers[self.SAMPLE_DIV]) * 1_000_000
        if self.next_sample is None:
            self.next_sample = now + period
            return
        if now >= self.next_sample:
            count = (now - self.next_sample) // period + 1
            self.next_sample += count * period
            packet = self._fifo_packet()
            if not packet:
                return
            if len(self.fifo) + count * len(packet) > self.FIFO_CAPACITY:
                self.registers[self.INT_STATUS] |= 0x10
            self.fifo.extend(packet * min(count, self.FIFO_CAPACITY // len(packet) + 1))
            self.registers[self.INT_STATUS] |= 0x02 if self.registers[self.USER_CTRL] & 0x80 else 0x01

    def write(self, value, now):
        if self.expect_register:
            self.register = value & 0x7F
            self.expect_register = False
            return True
        register = self.register
        if register == self.POWER and value & 0x80:
            self.reset()
            return True
        if register == self.USER_CTRL:
            self._update_fifo(now)
            if value & 0x0C:
                self._clear_fifo()
            self.registers[register] = value & 0xF0
        elif register == self.MEM_DATA:
            self.memory[self._memory_index()] = value
        elif register == self.FIFO_DATA:
            self.fifo.append(value)
        elif register != self.WHO_AM_I and not self.INT_STATUS <= register < self.ACCEL_DATA + len(self.sample):
            self.registers[register] = value
        self._advance()
        return True

    def read(self, now):
        register = self.register
        if self.ACCEL_DATA <= register < self.ACCEL_DATA + len(self.sample):
            value = self.sample[register - self.ACCEL_DATA]
        elif register == self.MEM_DATA:
            value = self.memory[self._memory_index()]
        elif register in (self.FIFO_HIGH, self.FIFO_LOW, self.FIFO_DATA, self.INT_STATUS):
            self._update_fifo(now)
            if register == self.FIFO_HIGH:
                self.fifo_count = len(self.fifo)
                value = self.fifo_count >> 8
            elif register == self.FIFO_LOW:
                value = self.fifo_count & 0xFF
            elif register == self.FIFO_DATA:
                value = self.fifo.popleft() if self.fifo else 0
            else:
                value = self.registers[register]
                self.registers[register] = 0
        else:
            value = self.registers[register]
        self._advance()
        return value
