"""Local TCP listener for scripts started outside the simulator."""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QTcpServer

from rpi_sim_client.protocol import HOST, MAX_MESSAGE_BYTES, VERSION, MessageReader, ProtocolError, encode_message, get_port

from .session import failure, result, unpack_request


class GPIOServer(QObject):
    connection_changed = Signal(bool)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.listener = QTcpServer(self)
        self.listener.newConnection.connect(self._accept_connections)
        self.clients = set()
        self.error = ""

    def start(self):
        try:
            port = get_port()
        except ValueError as error:
            self.error = str(error)
            return False
        if not self.listener.listen(QHostAddress(HOST), port):
            self.error = self.listener.errorString()
            return False
        return True

    def close(self):
        self.listener.close()
        for client in tuple(self.clients):
            client.close()

    def _accept_connections(self):
        while self.listener.hasPendingConnections():
            socket = self.listener.nextPendingConnection()
            client = GPIOConnection(self, socket)
            self.clients.add(client)
            if socket.bytesAvailable():
                client.read_requests()


class GPIOConnection(QObject):
    def __init__(self, server, socket):
        super().__init__(server)
        self.server = server
        self.socket = socket
        self.socket.setParent(self)
        self.socket.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
        self.socket.setReadBufferSize(MAX_MESSAGE_BYTES)
        self.socket.readyRead.connect(self.read_requests)
        self.socket.disconnected.connect(self._disconnected)
        self.reader = MessageReader()
        self.active = False
        self.handshake_timer = QTimer(self, singleShot=True, interval=5000)
        self.handshake_timer.timeout.connect(self.close)
        self.handshake_timer.start()

    def close(self):
        self.socket.abort()

    def read_requests(self):
        if self.socket.state() != QAbstractSocket.SocketState.ConnectedState:
            return
        try:
            for message in self.reader.feed(bytes(self.socket.readAll())):
                request_id, operation, args = unpack_request(message)
                if not self.active:
                    if not self._handshake(request_id, operation, args):
                        break
                else:
                    self.send(self.server.session.handle_request(request_id, operation, args))
                if self.socket.state() != QAbstractSocket.SocketState.ConnectedState:
                    break
        except ProtocolError:
            self.close()

    def _handshake(self, request_id, operation, args):
        if operation != "hello" or len(args) != 1 or type(args[0]) is not int or args[0] != VERSION:
            reply = failure(request_id, f"The simulator requires GPIO protocol version {VERSION}")
        elif not self.server.session.claim(self.send):
            reply = failure(request_id, "The simulator board is already in use by another script")
        else:
            self.active = True
            self.handshake_timer.stop()
            self.send(result(request_id, VERSION))
            self.server.connection_changed.emit(True)
            return True
        self.send(reply)
        self.socket.disconnectFromHost()
        return False

    def send(self, message):
        if self.socket.bytesToWrite() >= 2 * MAX_MESSAGE_BYTES:
            self.close()
            return
        try:
            data = encode_message(message)
        except ProtocolError:
            self.close()
            return
        self.socket.write(data)

    def _disconnected(self):
        self.handshake_timer.stop()
        if self.server.session.release(self.send):
            self.server.connection_changed.emit(False)
        self.server.clients.discard(self)
        self.deleteLater()
