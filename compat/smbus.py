"""SMBus and raw combined I²C transfers through the Raspberry Pi simulator."""

import errno

from rpi_sim_client import bridge

__all__ = ["SMBus", "i2c_msg", "I2C_M_RD", "I2C_M_TEN", "I2C_M_NOSTART", "I2C_M_STOP"]

I2C_M_RD = 0x0001
I2C_M_TEN = 0x0010
I2C_M_NOSTART = 0x4000
I2C_M_STOP = 0x8000


def _byte(value):
    if not isinstance(value, int) or not -128 <= value <= 255:
        raise ValueError("Expected an 8-bit byte")
    return value & 0xFF


def _word(value):
    if not isinstance(value, int) or not -32768 <= value <= 65535:
        raise ValueError("Expected a 16-bit word")
    return [value & 0xFF, (value >> 8) & 0xFF]


def _block(values):
    data = [_byte(value) for value in values]
    if not 1 <= len(data) <= 32:
        raise ValueError("An SMBus block must contain one to 32 bytes")
    return data


def _crc(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xFF
    return crc


class i2c_msg:
    """A raw read or write used by SMBus.i2c_rdwr()."""

    def __init__(self, address, flags, data):
        self.addr = address
        self.flags = flags
        self.buf = bytearray(data)

    @classmethod
    def read(cls, address, length, *, tenbit=False, nostart=False, stop=False):
        if type(length) is not int or not 0 <= length <= 4096:
            raise ValueError("I²C read length must be between 0 and 4096 bytes")
        return cls(address, I2C_M_RD | cls._flags(address, tenbit, nostart, stop), length)

    @classmethod
    def write(cls, address, values, *, tenbit=False, nostart=False, stop=False):
        return cls(address, cls._flags(address, tenbit, nostart, stop), bytes(values))

    @staticmethod
    def _flags(address, tenbit, nostart, stop):
        flags = I2C_M_TEN if tenbit or address > 0x7F else 0
        return flags | (I2C_M_NOSTART if nostart else 0) | (I2C_M_STOP if stop else 0)

    @property
    def len(self):
        return len(self.buf)

    def __len__(self):
        return len(self.buf)

    def __iter__(self):
        return iter(self.buf)

    def __bytes__(self):
        return bytes(self.buf)

    def _record(self):
        if self.flags & ~(I2C_M_RD | I2C_M_TEN | I2C_M_NOSTART | I2C_M_STOP):
            raise ValueError("Unsupported raw I²C message flags")
        read = bool(self.flags & I2C_M_RD)
        record = {
            "address": self.addr, "read": read, "tenbit": bool(self.flags & I2C_M_TEN),
            "nostart": bool(self.flags & I2C_M_NOSTART), "stop": bool(self.flags & I2C_M_STOP),
        }
        record.update(length=len(self) if read else 0, data=[] if read else list(self))
        return record


class SMBus:
    def __init__(self, bus=None, force=False):
        self.bus = None
        self.pec = 0
        self.tenbit = False
        if bus is not None:
            self.open(bus)

    def open(self, bus):
        bridge.get_connection().request("i2c_open", bus)
        self.bus = bus

    def close(self):
        self.bus = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _request(self, operation, *args):
        if self.bus is None:
            raise OSError(errno.EBADF, "I²C bus is closed")
        return bridge.get_connection().request(operation, self.bus, *args)

    def i2c_rdwr(self, *messages):
        reads = self._request("i2c_transfer", [message._record() for message in messages])
        self._store_reads(messages, reads)

    @staticmethod
    def _store_reads(messages, reads):
        for message, data in zip(messages, reads):
            if message.flags & I2C_M_RD:
                message.buf[:] = bytes(data)

    def configure(self, *, mode=None, trace=None, start_byte=None):
        """Select simulated bus speed and optional tracing."""
        self._request("i2c_configure", mode, trace, start_byte)

    def trace(self, limit=256, *, clear=False):
        """Return [nanoseconds, SCL, SDA, event] records from the bus."""
        return self._request("i2c_trace", limit, clear)

    def contend(self, *transfers, clocks=None):
        """Begin several raw transfers at the same time, with optional (SCL low, high) times in ns."""
        groups = [tuple(parts) for parts in transfers]
        results = self._request("i2c_contend", [[message._record() for message in parts] for parts in groups], clocks)
        for parts, result in zip(groups, results):
            if "reads" in result:
                self._store_reads(parts, result["reads"])
        return results

    def recover(self):
        """Attempt digital bus recovery by pulsing SCL."""
        self._request("i2c_recover")

    def _exchange(self, address, data=None, length=None, *, block=False, pec=True):
        if not isinstance(address, int) or not 0 <= address <= (0x3FF if self.tenbit else 0x7F):
            raise ValueError("Invalid I²C address")
        use_pec = bool(self.pec and pec)
        if use_pec and self.tenbit:
            raise ValueError("SMBus PEC requires a 7-bit address")
        reading = length is not None or block
        header = {"address": address, "tenbit": self.tenbit}
        messages, crc_data = [], []
        if data is not None:
            values = list(data)
            crc_data = [address << 1, *values]
            if use_pec and not reading:
                values.append(_crc(crc_data))
            messages.append({**header, "data": values})
        if reading:
            messages.append({
                **header, "read": True, "block": block,
                "length": 0 if block else length + use_pec,
                "extra": int(block and use_pec),
            })
        result = self._request("i2c_transfer", messages)
        if reading:
            values = result[-1]
            if use_pec:
                actual = values.pop()
                if actual != _crc([*crc_data, (address << 1) | 1, *values]):
                    raise OSError(errno.EBADMSG, "SMBus packet error check failed")
            return values[1:] if block else values

    def write_quick(self, address, force=None):
        self._exchange(address, [], pec=False)

    def write_byte(self, address, value, force=None):
        self._exchange(address, [_byte(value)])

    def read_byte(self, address, force=None):
        return self._exchange(address, length=1)[0]

    def write_byte_data(self, address, register, value, force=None):
        self._exchange(address, [_byte(register), _byte(value)])

    def read_byte_data(self, address, register, force=None):
        return self._exchange(address, [_byte(register)], 1)[0]

    def write_word_data(self, address, register, value, force=None):
        self._exchange(address, [_byte(register), *_word(value)])

    def read_word_data(self, address, register, force=None):
        low, high = self._exchange(address, [_byte(register)], 2)
        return low | high << 8

    def process_call(self, address, register, value, force=None):
        low, high = self._exchange(address, [_byte(register), *_word(value)], 2)
        return low | high << 8

    def write_block_data(self, address, register, values, force=None):
        data = _block(values)
        self._exchange(address, [_byte(register), len(data), *data])

    def read_block_data(self, address, register, force=None):
        return self._exchange(address, [_byte(register)], block=True)

    def block_process_call(self, address, register, values, force=None):
        data = _block(values)
        return self._exchange(address, [_byte(register), len(data), *data], block=True)

    def write_i2c_block_data(self, address, register, values, force=None):
        self._exchange(address, [_byte(register), *_block(values)], pec=False)

    def read_i2c_block_data(self, address, register, length=32, force=None):
        if type(length) is not int or not 1 <= length <= 32:
            raise ValueError("An I²C block must contain one to 32 bytes")
        return self._exchange(address, [_byte(register)], length, pec=False)
