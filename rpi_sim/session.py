"""One controlling GPIO connection per simulated board."""

from rpi_sim_client.protocol import ProtocolError


def unpack_request(message):
    request_id = message.get("id")
    operation = message.get("op")
    args = message.get("args")
    if type(request_id) is not int or request_id < 1 or not isinstance(operation, str) or not isinstance(args, list):
        raise ProtocolError("Expected a GPIO request with an ID, operation and arguments")
    return request_id, operation, args


def result(request_id, value=None):
    return {"id": request_id, "type": "result", "result": value}


def failure(request_id, message, error_type="RuntimeError"):
    return {"id": request_id, "type": error_type, "error": message}


class BoardSession:
    def __init__(self, board):
        self.board = board
        self._send = None

    @property
    def busy(self):
        return self._send is not None

    def claim(self, send):
        if self.busy:
            return False
        self._send = send
        return True

    def release(self, send):
        if self._send != send:
            return False
        self._send = None
        self.board.cleanup()
        return True

    def handle_request(self, request_id, operation, args):
        command = self.board.commands.get(operation)
        if command is None:
            return failure(request_id, f"Unknown GPIO operation {operation!r}")
        try:
            return result(request_id, command(*args))
        except OSError as error:
            reply = failure(request_id, error.strerror or str(error), "OSError")
            reply["errno"] = error.errno
            return reply
        except (RuntimeError, TypeError, ValueError) as error:
            return failure(request_id, str(error), type(error).__name__)

    def send_edge(self, channel, registration):
        if self._send is not None:
            self._send({"type": "edge", "channel": channel, "registration": registration})

    def send_pulses(self, pin, pulses, delay):
        if self._send is not None:
            self._send({"type": "pulses", "pin": pin, "pulses": pulses, "delay": delay})
