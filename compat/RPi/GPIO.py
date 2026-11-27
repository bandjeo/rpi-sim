"""Desktop implementation of the digital I/O subset of RPi.GPIO."""

import itertools
import sys
import threading
import traceback
import warnings

from rpi_sim_client import bridge
from rpi_sim_client.gpio import BCM, BOARD, BOTH, FALLING, HIGH, IN, LOW, OUT, PUD_DOWN, PUD_OFF, PUD_UP, RISING

_connection = None
_connection_lock = threading.Lock()
_registrations = {}
_registration_ids = itertools.count(1)
_callback_lock = threading.RLock()
_warnings_enabled = True


def _request(operation, *args):
    global _connection
    with _connection_lock:
        if _connection is None:
            _connection = bridge.pipe_connection or bridge.connect_tcp()
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
        _request("setup", channel, direction, pull_up_down, initial)
        if direction == OUT:
            for item in channel if isinstance(channel, (tuple, list)) else (channel,):
                _registrations.pop(item, None)


def input(channel):
    return _request("input", channel)


def output(channel, value):
    _request("output", channel, value)


def cleanup(channel=None):
    with _callback_lock:
        cleaned = _request("cleanup", channel)
        if channel is None:
            _registrations.clear()
        else:
            for item in channel if isinstance(channel, (tuple, list)) else (channel,):
                _registrations.pop(item, None)
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
