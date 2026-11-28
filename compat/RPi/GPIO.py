"""Desktop implementation of the digital I/O subset of RPi.GPIO."""

import itertools
import sys
import threading
import traceback
import warnings
from contextlib import suppress

from rpi_sim_client import bridge
from rpi_sim_client.gpio import BCM, BOARD, BOTH, FALLING, HIGH, IN, LOW, OUT, PUD_DOWN, PUD_OFF, PUD_UP, RISING

_connection = None
_connection_lock = threading.Lock()
_registrations = {}
_registration_ids = itertools.count(1)
_callback_lock = threading.RLock()
_warnings_enabled = True
_pulse_pins = {}


def _request(operation, *args):
    global _connection
    with _connection_lock:
        if _connection is None:
            _connection = bridge.get_connection()
            threading.Thread(
                target=_dispatch_edges, args=(_connection,), name="GPIO callbacks", daemon=True
            ).start()
    return _connection.request(operation, *args)


def setmode(mode):
    _request("setmode", mode)


def getmode():
    return _request("getmode")


def setwarnings(enabled):
    global _warnings_enabled
    _warnings_enabled = bool(enabled)


def setup(channel, direction, pull_up_down=PUD_OFF, initial=None):
    with _callback_lock:
        sources = _request("setup", channel, direction, pull_up_down, initial) or {}
        for item in channel if isinstance(channel, (tuple, list)) else (channel,):
            source = sources.get(str(item))
            previous = _pulse_pins.pop(item, None)
            if source is not None:
                _pulse_pins[item] = source
            elif previous is not None:
                _connection.pulses.clear(previous[0])
            if direction == OUT:
                _registrations.pop(item, None)


def input(channel):
    source = _pulse_pins.get(channel) if type(channel) is int else None
    if source is not None:
        return _connection.pulses.sample(*source)
    return _request("input", channel)


def capture_pulses(channel, *, timeout=0.1):
    """Capture one simulated sensor frame as (level, microseconds) pairs."""
    source = _pulse_pins.get(channel) if type(channel) is int else None
    if source is None:
        raise RuntimeError("Set up a simulated pulse sensor on this GPIO input first")
    if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 60:
        raise ValueError("Pulse capture timeout must be between 0 and 60 seconds")
    return _connection.pulses.capture(source[0], timeout)


def output(channel, value):
    _request("output", channel, value)


def cleanup(channel=None):
    with _callback_lock:
        cleaned = _request("cleanup", channel)
        if channel is None:
            _registrations.clear()
            for pin, _ in _pulse_pins.values():
                _connection.pulses.clear(pin)
            _pulse_pins.clear()
        else:
            for item in channel if isinstance(channel, (tuple, list)) else (channel,):
                _registrations.pop(item, None)
                source = _pulse_pins.pop(item, None)
                if source is not None:
                    _connection.pulses.clear(source[0])
    if not cleaned and _warnings_enabled:
        warnings.warn("No channels have been set up yet; use GPIO.setup() first", RuntimeWarning, stacklevel=2)


def gpio_function(channel):
    return _request("gpio_function", channel)


def add_event_detect(channel, edge, callback=None, bouncetime=None):
    if callback is not None and not callable(callback):
        raise TypeError("GPIO callback must be callable")
    with _callback_lock:
        registration = next(_registration_ids)
        _request("add_event_detect", channel, edge, bouncetime, registration)
        _registrations[channel] = (registration, [callback] if callback is not None else [])


def add_event_callback(channel, callback):
    if not callable(callback):
        raise TypeError("GPIO callback must be callable")
    with _callback_lock:
        if channel not in _registrations:
            raise RuntimeError("Enable edge detection before adding a callback")
        _registrations[channel][1].append(callback)


def remove_event_detect(channel):
    with _callback_lock:
        _request("remove_event_detect", channel)
        _registrations.pop(channel, None)


def event_detected(channel):
    return _request("event_detected", channel)


class PWM:
    def __init__(self, channel, frequency):
        self._channel = channel
        self._registration = None
        self._registration = _request("pwm_create", channel, frequency)

    def start(self, duty_cycle):
        _request("pwm_start", self._channel, self._registration, duty_cycle)

    def ChangeDutyCycle(self, duty_cycle):
        _request("pwm_duty", self._channel, self._registration, duty_cycle)

    def ChangeFrequency(self, frequency):
        _request("pwm_frequency", self._channel, self._registration, frequency)

    def stop(self):
        _request("pwm_stop", self._channel, self._registration)

    def __del__(self):
        if self._registration is not None and not sys.is_finalizing():
            with suppress(OSError, RuntimeError, TypeError, ValueError):
                _connection.notify("pwm_release", self._channel, self._registration)


def _dispatch_edges(connection):
    while event := connection.events.get():
        channel, registration = event
        with _callback_lock:
            current, callbacks = _registrations.get(channel, (None, ()))
            callbacks = tuple(callbacks) if current == registration else ()
        for callback in callbacks:
            try:
                callback(channel)
            except BaseException:
                traceback.print_exc(file=sys.stderr)
