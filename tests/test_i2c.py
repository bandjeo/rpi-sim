import errno
import unittest
from unittest.mock import patch

from compat.smbus import SMBus, _crc, i2c_msg
from rpi_sim.board import Board, Component, layout_json, validate_components
from rpi_sim.i2c import MODES, I2CBus, I2CTarget
from rpi_sim.i2c_devices import LCD1602, MPU6050


def write(address, *values, **flags):
    return {"address": address, "data": list(values), **flags}


def read(address, length, **flags):
    return {"address": address, "read": True, "length": length, **flags}


class Target(I2CTarget):
    def __init__(self, *, response=(0x12, 0x34), delay=0, accept=True, stuck=0):
        self.writes = []
        self.reads = iter(response)
        self.replies = []
        self.delay = delay
        self.accept = accept
        self.stuck = stuck
        self.stops = 0

    def write(self, value, now):
        self.writes.append(value)
        return self.accept

    def read(self, now):
        return next(self.reads, 0xFF)

    def read_ack(self, acknowledged, now):
        self.replies.append(acknowledged)

    def stretch(self, now):
        return self.delay

    def holds_sda(self):
        return self.stuck > 0

    def recovery_clock(self):
        self.stuck -= 1

    def stop(self, now):
        self.stops += 1


class BusTests(unittest.TestCase):
    def setUp(self):
        self.bus = I2CBus()
        self.bus.configure(trace=True)

    def events(self):
        return [event for _, _, _, event in self.bus.trace(1024) if event]

    def decoded(self):
        clocks = []
        previous_scl = 1
        for _, scl, sda, event in self.bus.trace(1024):
            if event in ("START", "RESTART", "STOP"):
                if clocks and len(clocks) % 9 == 1:
                    clocks.pop()  # SCL rose to prepare the next bus condition.
                previous_scl = scl
                continue
            if scl and not previous_scl:
                clocks.append(sda)
            previous_scl = scl
        self.assertEqual(len(clocks) % 9, 0)
        return [(sum(bit << (7 - shift) for shift, bit in enumerate(clocks[index:index + 8])),
                 clocks[index + 8]) for index in range(0, len(clocks), 9)]

    def test_combined_transfer_and_read_ack(self):
        device = Target()
        self.bus.attach(0x68, device)
        self.assertEqual(self.bus.transfer([write(0x68, 59), read(0x68, 2)]), [[], [0x12, 0x34]])
        self.assertEqual(self.decoded(), [(0xD0, 0), (59, 0), (0xD1, 0), (0x12, 0), (0x34, 1)])
        self.assertEqual(device.replies, [True, False])
        self.assertEqual(device.stops, 1)
        self.assertEqual(self.events(), ["START", "ACK", "ACK", "RESTART", "ACK", "ACK", "NACK", "STOP"])

    def test_ten_bit_address_and_shortened_repeated_read(self):
        device = Target()
        self.bus.attach(0x2AA, device, tenbit=True)
        self.bus.transfer([write(0x2AA, 5, tenbit=True), read(0x2AA, 1, tenbit=True)])
        self.assertEqual(self.decoded(), [(0xF4, 0), (0xAA, 0), (5, 0), (0xF5, 0), (0x12, 1)])
        self.bus.trace(clear=True)
        self.bus.transfer([read(0x2AA, 1, tenbit=True)])
        self.assertEqual(self.decoded(), [(0xF4, 0), (0xAA, 0), (0xF5, 0), (0x34, 1)])

    def test_nack_releases_bus(self):
        with self.assertRaises(OSError) as missing:
            self.bus.transfer([write(0x27, 1)])
        self.assertEqual(missing.exception.errno, errno.ENXIO)
        self.assertEqual(self.events(), ["START", "NACK", "STOP"])
        device = Target(accept=False)
        self.bus.attach(0x27, device)
        with self.assertRaises(OSError) as refused:
            self.bus.transfer([write(0x27, 1, 2)])
        self.assertEqual(refused.exception.errno, getattr(errno, "EREMOTEIO", errno.EIO))
        self.assertEqual(device.writes, [1])
        self.assertEqual((self.bus.wire.scl, self.bus.wire.sda), (1, 1))

    def test_stretching_extends_the_clock_without_wall_time(self):
        self.bus.attach(0x27, Target(delay=120_000))
        self.bus.transfer([write(0x27, 9)])
        trace = self.bus.trace()
        index = next(index for index, event in enumerate(trace) if event[3] == "STRETCH")
        self.assertEqual(trace[index][1], 0)
        self.assertEqual(trace[index + 1][0] - trace[index][0], 125_000)

    def test_write_and_read_arbitration(self):
        device = Target()
        self.bus.attach(0x27, device)
        outcomes = self.bus.contend([[write(0x27, 0x81)], [write(0x27, 0x80)]])
        self.assertEqual(outcomes[0]["errno"], errno.EAGAIN)
        self.assertEqual(outcomes[1]["reads"], [[]])
        self.assertEqual(device.writes, [0x80])
        outcomes = self.bus.contend([[read(0x27, 1)], [read(0x27, 2)]])
        self.assertEqual(outcomes[0]["errno"], errno.EAGAIN)
        self.assertEqual(outcomes[1]["reads"], [[0x12, 0x34]])
        self.assertEqual(device.replies, [True, False])

    def test_clock_synchronization(self):
        self.bus.attach(0x27, Target())
        same_message = [write(0x27)]
        outcomes = self.bus.contend([same_message, same_message], [[6_000, 7_000], [9_000, 6_000]])
        self.assertEqual(outcomes, [{"reads": [[]]}, {"reads": [[]]}])
        rises = [time for time, scl, _, event in self.bus.trace() if scl and not event]
        self.assertEqual(rises[1] - rises[0], 15_000)

    def test_high_speed_arbitrates_before_fast_clocks(self):
        self.bus.configure(mode="high-speed")
        device = Target()
        self.bus.attach(0x27, device)
        outcomes = self.bus.contend([[write(0x27, 5)], [write(0x27, 2)]])
        self.assertEqual(outcomes[1]["errno"], errno.EAGAIN)
        self.assertEqual(device.writes, [5])
        self.assertEqual(self.decoded()[:2], [(0x08, 1), (0x4E, 0)])
        self.assertIn("HS CODE", self.events())

    def test_ultra_fast_is_write_only_and_ninth_bit_is_high(self):
        self.bus.configure(mode="ultra-fast")
        device = Target()
        device.ultra_fast = True
        self.bus.attach(0x27, device)
        self.bus.transfer([write(0x27, 3)])
        self.assertEqual(self.decoded(), [(0x4E, 1), (3, 1)])
        self.assertEqual(device.writes, [3])
        with self.assertRaises(ValueError):
            self.bus.transfer([read(0x27, 1)])

    def test_start_byte_general_call_and_requested_stop(self):
        self.bus.configure(start_byte=True, mode="fast-plus")
        first, second = Target(), Target()
        first.general_call = second.general_call = True
        self.bus.attach(0x27, first)
        self.bus.attach(0x68, second)
        self.bus.transfer([write(0, 6, stop=True), write(0x68, 4)])
        self.assertEqual(self.decoded(), [(1, 1), (0, 0), (6, 0), (1, 1), (0xD0, 0), (4, 0)])
        self.assertEqual(first.writes, [6])
        self.assertEqual(second.writes, [6, 4])
        self.assertEqual(self.events().count("STOP"), 2)
        self.assertEqual(self.bus.wire.timing, MODES["fast-plus"])

    def test_no_start_continues_the_previous_read(self):
        device = Target()
        self.bus.attach(0x27, device)
        self.assertEqual(self.bus.transfer([read(0x27, 1), read(0x27, 1, nostart=True)]), [[0x12], [0x34]])
        self.assertNotIn("RESTART", self.events())
        self.assertEqual(device.replies, [True, False])

    def test_bus_recovery(self):
        device = Target(stuck=3)
        self.bus.attach(0x27, device)
        with self.assertRaises(OSError) as busy:
            self.bus.transfer([write(0x27, 5)])
        self.assertEqual(busy.exception.errno, errno.EBUSY)
        self.bus.recover()
        self.assertEqual(self.events().count("RECOVERY"), 3)
        self.bus.transfer([write(0x27, 5)])
        self.assertEqual(device.writes, [5])

    def test_optional_device_id_reads_and_restarts(self):
        device = Target()
        device.device_id = 0x123456
        self.bus.attach(0x27, device)
        self.assertEqual(self.bus.transfer([write(0x7C, 0x4E), read(0x7C, 4)]),
                         [[], [0x12, 0x34, 0x56, 0x12]])
        with self.assertRaises(OSError):
            self.bus.transfer([write(0x7C, 0x4E, stop=True), read(0x7C, 3)])


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.board = Board(lambda _: None, lambda *_: None)
        self.target = MPU6050()
        self.board.i2c_bus(1).attach(0x68, self.target)
        self.transport = patch("rpi_sim_client.bridge.get_connection")
        connection = self.transport.start()
        connection.return_value.request.side_effect = self.request
        self.addCleanup(self.transport.stop)
        self.bus = SMBus(1)

    def request(self, operation, *args):
        return self.board.commands[operation](*args)

    def test_range_scaling_and_raw_combined_read(self):
        self.bus.write_byte_data(0x68, 0x6B, 0)
        self.bus.write_byte_data(0x68, 0x1B, 0x18)
        self.target.rotation = (-100.0, 200.0, -0.0)
        reply = i2c_msg.read(0x68, 6)
        self.bus.i2c_rdwr(i2c_msg.write(0x68, [0x43]), reply)
        data = bytes(reply)
        self.assertEqual([int.from_bytes(data[index:index + 2], "big", signed=True) for index in (0, 2, 4)],
                         [-1640, 3280, 0])

    def test_banked_memory_reset_and_fifo(self):
        self.bus.write_byte_data(0x68, 0x6D, 2)
        self.bus.write_byte_data(0x68, 0x6E, 0xFE)
        self.bus.write_i2c_block_data(0x68, 0x6F, [12, 34, 56])
        self.bus.write_byte_data(0x68, 0x6E, 0xFE)
        self.assertEqual(self.bus.read_i2c_block_data(0x68, 0x6F, 3), [12, 34, 56])
        self.bus.write_byte_data(0x68, 0x6B, 0)
        self.bus.write_byte_data(0x68, 0x6A, 0xC0)
        self.bus.read_byte_data(0x68, 0x72)
        self.board.i2c_bus(1).wire.now += 2_000_000
        self.assertEqual(self.bus.read_i2c_block_data(0x68, 0x72, 2), [0, 84])
        self.bus.write_byte_data(0x68, 0x6A, 0)
        self.bus.write_byte_data(0x68, 0x6B, 0x80)
        self.assertEqual(self.bus.read_byte_data(0x68, 0x6B), 0x40)

    def test_lcd_enable_edges_display_and_backlight(self):
        display = LCD1602(lambda: None)
        self.board.i2c_bus(1).attach(0x27, display)

        def pulse(nibble, rs=0):
            value = (nibble << 4) | 8 | rs
            self.bus.write_byte(0x27, value | 4)
            self.bus.write_byte(0x27, value)

        def send(value, rs=0):
            pulse(value >> 4, rs)
            pulse(value & 15, rs)

        self.bus.write_byte(0x27, 0)
        for nibble in (3, 3, 3, 2):
            pulse(nibble)
        send(0x28)
        send(0x0C)
        send(0x06)
        send(ord("A"), 1)
        send(0xC0)
        send(ord("B"), 1)
        self.assertEqual(display.lcd.lines(), ("A".ljust(16), "B".ljust(16)))
        self.assertTrue(display.backlight)
        send(0x01)
        self.assertEqual(display.lcd.lines(), (" " * 16, " " * 16))
        self.bus.write_byte(0x27, 0)
        self.assertFalse(display.backlight)

    def test_addresses_are_unique_per_bus_and_gpio_layout_is_compatible(self):
        first = Component("lcd", "lcd", name="LCD", bus=1, address=0x27)
        duplicate = Component("lcd2", "lcd", name="Other LCD", bus=1, address=0x27)
        other_bus = Component("lcd2", "lcd", name="Other LCD", bus=2, address=0x27)
        with self.assertRaises(ValueError):
            validate_components([first, duplicate])
        validate_components([first, other_bus, Component("led", "led", 18, "LED")])
        self.assertNotIn('"bus"', layout_json([Component("led", "led", 18, "LED")]))

    def test_smbus_block_and_packet_error_checking(self):
        data = [2, 0xA1, 0xB2]
        checksum = _crc([0x4E, 0x23, 0x4F, *data])
        self.assertEqual(_crc(b"123456789"), 0xF4)
        self.board.i2c_bus(1).attach(0x27, Target(response=[*data, checksum, *data, checksum ^ 1]))
        self.bus.pec = 1
        self.assertEqual(self.bus.read_block_data(0x27, 0x23), data[1:])
        with self.assertRaises(OSError) as failure:
            self.bus.read_block_data(0x27, 0x23)
        self.assertEqual(failure.exception.errno, errno.EBADMSG)


if __name__ == "__main__":
    unittest.main()
