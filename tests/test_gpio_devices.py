import unittest
from unittest.mock import patch

from rpi_sim.board import Board, Component, validate_components
from rpi_sim.gpio_devices import DIGIT_PINS, IR_KEYS, SEGMENT_PINS
from rpi_sim_client import gpio
from rpi_sim_client.pulses import PulseFrame, PulseInputs


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sent = []
        self.board = Board(
            lambda _pin: None, lambda *_args: None,
            pulse_sent=lambda *frame: self.sent.append(frame), clock_ns=lambda: self.now,
        )
        self.board.setmode(gpio.BCM)

    def test_display_remembers_each_multiplexed_digit(self):
        board = self.board
        display = Component("display", "seven_segment", name="Display", segment_pins=SEGMENT_PINS,
                            digit_pins=DIGIT_PINS)
        board.replace_components([display])
        board.setup(list(SEGMENT_PINS), gpio.OUT, initial=gpio.LOW)
        board.setup(list(DIGIT_PINS), gpio.OUT, initial=gpio.HIGH)
        for digit, mask in zip(DIGIT_PINS, (0x06, 0xDB, 0x4F, 0x66)):
            board.output(list(SEGMENT_PINS), [int(bool(mask & (1 << bit))) for bit in range(8)])
            board.output(digit, gpio.LOW)
            self.now += 1_000_000
            board.output(digit, gpio.HIGH)
        self.assertEqual(board.display_segments("display"), (0x06, 0xDB, 0x4F, 0x66))
        self.now += 120_000_000
        self.assertEqual(board.display_segments("display"), (0, 0, 0, 0))

    def test_display_active_high_digit_and_active_low_segments(self):
        board = self.board
        board.replace_components([Component(
            "display", "seven_segment", name="Display", active_low=True, segment_pins=SEGMENT_PINS,
            digit_pins=DIGIT_PINS, digit_active_low=False,
        )])
        board.setup(list(SEGMENT_PINS), gpio.OUT, initial=gpio.HIGH)
        board.setup(list(DIGIT_PINS), gpio.OUT, initial=gpio.LOW)
        board.output(SEGMENT_PINS[2], gpio.LOW)
        board.output(DIGIT_PINS[3], gpio.HIGH)
        self.now += 200_000_000
        self.assertEqual(board.display_segments("display"), (0, 0, 0, 4))

    def test_dht_wakeup_and_payload(self):
        board = self.board
        board.replace_components([Component("dht", "dht11", 17, "DHT11")])
        board.gpio_devices["dht"].configure(27.4, 63)
        board.setup(17, gpio.OUT, initial=gpio.LOW)
        self.now = 17_999_999
        board.setup(17, gpio.IN)
        self.assertEqual(self.sent, [])
        board.setup(17, gpio.OUT, initial=gpio.LOW)
        self.now += 18_000_000
        board.output(17, gpio.HIGH)
        self.assertEqual(board.setup(17, gpio.IN), {"17": [17, 1]})
        pin, waveform, delay = self.sent.pop()
        self.assertEqual((pin, delay), (17, 0))
        self.assertEqual(waveform[:2], [(0, 80), (1, 80)])
        self.assertEqual(waveform[-1], (0, 50))
        bits = "".join("1" if width == 70 else "0" for level, width in waveform[3:82:2] if level == 1)
        self.assertEqual([int(bits[index:index + 8], 2) for index in range(0, 40, 8)], [63, 0, 27, 4, 94])

    def test_ultrasonic_distance_and_missing_echo(self):
        board = self.board
        board.replace_components([Component("sonar", "ultrasonic", 4, "Sonar", echo_pin=17)])
        board.cleanup()
        board.setmode(gpio.BOARD)
        board.setup(7, gpio.OUT)
        self.assertEqual(board.setup(11, gpio.IN), {"11": [17, 0]})
        for pulse_ns, expected in ((9_999, False), (10_000, True)):
            board.output(7, gpio.HIGH)
            self.now += pulse_ns
            board.output(7, gpio.LOW)
            self.assertEqual(bool(self.sent), expected)
        pin, waveform, delay = self.sent.pop()
        self.assertEqual((pin, delay), (17, 200))
        self.assertAlmostEqual(waveform[0][1] * 34300 / 2_000_000, 50.0, places=1)
        board.gpio_devices["sonar"].configure(100.0, True)
        board.output(7, gpio.HIGH)
        self.now += 10_000
        board.output(7, gpio.LOW)
        self.assertEqual(self.sent, [])

    def test_remote_encodes_all_keys_and_requires_an_input(self):
        board = self.board
        board.replace_components([Component("ir", "ir", 17, "Remote")])
        board.press_remote("ir", "OK")
        self.assertEqual(self.sent, [])
        self.assertEqual(board.setup(17, gpio.IN), {"17": [17, 1]})
        self.assertEqual(board.input(17), 1)
        for key, value in IR_KEYS.items():
            board.press_remote("ir", key)
            pin, waveform, _ = self.sent.pop()
            self.assertEqual(pin, 17)
            self.assertEqual(waveform[:2], [(0, 9000), (1, 4500)])
            bits = "".join("1" if width > 1000 else "0" for _, width in waveform[3:66:2])
            self.assertEqual(int(bits, 2), value)
            self.assertEqual(board.gpio_devices["ir"].last_key, key)

    def test_layout_cannot_reuse_a_display_or_echo_pin(self):
        display = Component("display", "seven_segment", name="Display",
                            segment_pins=SEGMENT_PINS, digit_pins=DIGIT_PINS)
        with self.assertRaises(ValueError):
            validate_components([display, Component("dht", "dht11", 17, "DHT11")])
        with self.assertRaises(ValueError):
            validate_components([Component("sonar", "ultrasonic", 17, "Sonar", echo_pin=17)])


class CaptureTests(unittest.TestCase):
    def test_frame_edges_at_exact_boundaries(self):
        frame = PulseFrame(((1, 563), (0, 20)), 200, 0)
        self.assertEqual([frame.level(now, 0) for now in (0, 199_999, 200_000, 762_999, 763_000)], [0, 0, 1, 1, 0])

    def test_local_input_and_capture(self):
        receiver = PulseInputs()
        receiver.deliver(17, [(0, 80), (1, 70)], 0)
        with patch("rpi_sim_client.pulses.monotonic_ns", side_effect=[0, 100_000, 200_000, 200_000]):
            self.assertEqual(receiver.sample(17, 1), 0)
            self.assertEqual(receiver.sample(17, 1), 1)
            self.assertEqual(receiver.capture(17, 0), ((0, 80), (1, 70)))
        self.assertIsNone(receiver.capture(17, 0))
        receiver.close()
        with self.assertRaises(RuntimeError):
            receiver.capture(17, 0)


if __name__ == "__main__":
    unittest.main()
