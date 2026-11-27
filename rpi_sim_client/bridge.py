"""Script-side connection to the simulator over TCP or child process pipes."""

import _thread
import io
import itertools
import os
import queue
import socket
import threading
from contextlib import suppress

from .protocol import HOST, VERSION, MessageReader, ProtocolError, encode_message, get_port

CONNECT_TIMEOUT = 3
REQUEST_TIMEOUT = 5


class ConsoleInput(io.TextIOBase):
    def __init__(self):
        super().__init__()
        self.lines = queue.Queue()
        self.remainder = ""
        self.eof = False

    @property
    def encoding(self):
        return "utf-8"

    def readable(self):
        return True

    def readline(self, size=-1):
        if size == 0 or self.eof:
            return ""
        if not self.remainder:
            self.remainder = self.lines.get()
            if self.remainder is None:
                self.eof = True
                return ""
        if size < 0:
            line, self.remainder = self.remainder, ""
        else:
            line, self.remainder = self.remainder[:size], self.remainder[size:]
        return line

    def read(self, size=-1):
        if size < 0:
            return "".join(iter(self.readline, ""))
        chunks = []
        while size:
            line = self.readline(size)
            if not line:
                break
            chunks.append(line)
            size -= len(line)
        return "".join(chunks)


class Bridge:
    def __init__(self, read, write, close=None, console=None, timeout=None):
        self.read = read
        self.write = write
        self.close_transport = close
        self.console = console
        self.timeout = timeout
        self.events = queue.Queue()
        self.pending = {}
        self.lock = threading.Lock()
        self.connected = True
        self.ids = itertools.count(1)
        threading.Thread(target=self._read_messages, name="simulator connection", daemon=True).start()

    def request(self, operation, *args):
        reply = queue.Queue(maxsize=1)
        try:
            with self.lock:
                if not self.connected:
                    raise RuntimeError("The simulator has disconnected")
                request_id = next(self.ids)
                data = encode_message({"id": request_id, "op": operation, "args": args})
                self.pending[request_id] = reply
                try:
                    self.write(data)
                except BaseException:
                    self.pending.pop(request_id, None)
                    raise
        except OSError as error:
            self.close()
            raise RuntimeError("The simulator has disconnected") from error
        try:
            message = reply.get(timeout=self.timeout)
        except queue.Empty as error:
            self.close()
            raise RuntimeError("The simulator did not respond in time") from error
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
        if message is None:
            raise RuntimeError("The simulator has disconnected")
        if "error" in message:
            error_type = {"TypeError": TypeError, "ValueError": ValueError}.get(message["type"], RuntimeError)
            raise error_type(message["error"])
        return message.get("result")

    def _read_messages(self):
        reader = MessageReader()
        try:
            while chunk := self.read(4096):
                for message in reader.feed(chunk):
                    self._receive(message)
        except (OSError, ProtocolError, KeyError, TypeError):
            pass
        finally:
            self.close()

    def close(self):
        with self.lock:
            if not self.connected:
                return
            self.connected = False
            for reply in self.pending.values():
                reply.put_nowait(None)
            self.pending.clear()
        self.events.put(None)
        if self.console is not None:
            self.console.lines.put(None)
        if self.close_transport is not None:
            self.close_transport()

    def _receive(self, message):
        kind = message["type"]
        if kind == "result" or "error" in message:
            with self.lock:
                reply = self.pending.pop(message["id"], None)
                if reply is not None:
                    reply.put_nowait(message)
        elif kind == "console" and self.console is not None:
            self.console.lines.put(message["text"] + "\n")
        elif kind == "edge":
            self.events.put((message["channel"], message["registration"]))
        elif kind == "stop" and self.console is not None:
            _thread.interrupt_main()
            self.console.lines.put(None)


pipe_connection = None


def connect_pipes(input_fd, writer):
    global pipe_connection

    def write(data):
        writer.write(data)
        writer.flush()

    # Use an unbuffered read so the daemon thread cannot hold stdin's buffer
    # lock while Python shuts down.
    pipe_connection = Bridge(lambda size: os.read(input_fd, size), write, console=ConsoleInput())
    return pipe_connection


def connect_tcp():
    address = (HOST, get_port())
    try:
        sock = socket.create_connection(address, timeout=CONNECT_TIMEOUT)
    except OSError as error:
        raise RuntimeError(f"Cannot connect to RPi Simulator at {address[0]}:{address[1]}. Open the app and check its port.") from error

    def read(size):
        while True:
            try:
                return sock.recv(size)
            except socket.timeout:
                continue

    def close():
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        sock.close()

    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(REQUEST_TIMEOUT)
        connection = Bridge(read, sock.sendall, close=close, timeout=REQUEST_TIMEOUT)
    except BaseException:
        close()
        raise
    try:
        connection.request("hello", VERSION)
    except BaseException:
        connection.close()
        raise
    return connection
