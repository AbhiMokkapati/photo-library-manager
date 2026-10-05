"""
single_instance.py — stops a second copy of the app from launching, whether
started via run_gui.py or run_tray.py. Uses a QLocalServer as the lock: the
first process to successfully `listen()` on a well-known name owns the
instance; every later launch fails to listen (name's taken), so it instead
connects as a client, tells the running instance to raise its window, and
exits itself.

Why QLocalServer over a plain lock file: a lock file left behind by a crash
or `taskkill` would permanently block every future launch until someone
deletes it by hand. QLocalServer's name is released by the OS the moment the
owning process dies, so a crashed instance never leaves the app unlaunchable.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "PhotoLibraryManager_SingleInstance"


class SingleInstanceGuard(QObject):
    activate_requested = Signal()

    def __init__(self, server_name: str = SERVER_NAME):
        super().__init__()
        self._server_name = server_name
        self._server = None

    def try_lock(self) -> bool:
        """Returns True if this process now owns the instance. Returns False
        if another instance is already running (and pings it to come to
        front before returning)."""
        probe = QLocalSocket()
        probe.connectToServer(self._server_name)
        if probe.waitForConnected(200):
            probe.write(b"activate")
            probe.waitForBytesWritten(200)
            probe.disconnectFromServer()
            return False

        # No live instance responded. A prior instance may have crashed
        # without cleaning up the OS-level name — reclaim it before listening.
        QLocalServer.removeServer(self._server_name)
        self._server = QLocalServer()
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(self._server_name)
        return True

    def _on_new_connection(self):
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda c=conn: self._handle_message(c))

    def _handle_message(self, conn):
        conn.readAll()
        self.activate_requested.emit()
        conn.disconnectFromServer()
