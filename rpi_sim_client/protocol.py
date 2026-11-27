"""Framing and endpoint configuration for the GPIO connection."""

import json
import os

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024


class ProtocolError(ValueError):
    pass


def get_port():
    port = os.environ.get("RPI_SIM_PORT", str(DEFAULT_PORT))
    if not port.isdecimal() or len(port) > 5 or not 1 <= int(port) <= 65535:
        raise ValueError("RPI_SIM_PORT must be an integer between 1 and 65535")
    return int(port)


def encode_message(message):
    data = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError("GPIO message is too large")
    return data + b"\n"


class MessageReader:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        last_newline = self.buffer.rfind(b"\n")
        if len(self.buffer) - last_newline - 1 > MAX_MESSAGE_BYTES:
            raise ProtocolError("GPIO message is too large")
        if last_newline < 0:
            return
        lines = self.buffer[:last_newline].split(b"\n")
        del self.buffer[:last_newline + 1]
        for line in lines:
            if len(line) > MAX_MESSAGE_BYTES:
                raise ProtocolError("GPIO message is too large")
            try:
                message = json.loads(line)
            except (ValueError, RecursionError) as error:
                raise ProtocolError("Invalid GPIO message") from error
            if not isinstance(message, dict):
                raise ProtocolError("Expected a GPIO message object")
            yield message
