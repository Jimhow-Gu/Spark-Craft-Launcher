'''Windows 单实例锁；退出时可显式释放，避免残留进程导致“启动器已启动”。

除互斥锁外，再提供一个本地 socket IPC：第二个实例可以请求已有窗口
恢复并置顶，而不是只弹“已经运行”的提示。
'''

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_HANDLE = None
_MUTEX_NAME = 'Local\SparkCraftLauncher.SingleInstance.v1'
_IPC_NAME = 'SparkCraftLauncher.Activate.v1'
_SERVER = None


class SingleInstanceServer(QObject):
    activation_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        self._server.newConnection.connect(self._on_new_connection)
        self._pending = False

    def start(self):
        try:
            QLocalServer.removeServer(_IPC_NAME)
            return bool(self._server.listen(_IPC_NAME))
        except Exception:
            return False

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                break
            try:
                socket.readyRead.connect(lambda s=socket: self._read(s))
                socket.disconnected.connect(socket.deleteLater)
                if socket.bytesAvailable():
                    self._read(socket)
            except RuntimeError:
                pass

    def _read(self, socket):
        try:
            data = bytes(socket.readAll())
        except RuntimeError:
            return
        if b'activate' in data and not self._pending:
            self._pending = True
            self.activation_requested.emit()
        try:
            socket.disconnectFromServer()
            socket.deleteLater()
        except RuntimeError:
            pass

    def consume_pending(self):
        pending, self._pending = self._pending, False
        return pending

    def close(self):
        try:
            self._server.close()
        except Exception:
            pass


def acquire_single_instance() -> bool:
    global _HANDLE
    if sys.platform != 'win32':
        return True
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.restype = ctypes.c_void_p
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = create_mutex(None, False, _MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _HANDLE = handle
        return True
    except Exception:
        return True


def release_single_instance():
    '''退出/确认关闭时立即释放互斥锁，让新实例可以正常启动。'''

    global _HANDLE
    handle = _HANDLE
    _HANDLE = None
    if not handle or sys.platform != 'win32':
        return
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass


def start_activation_server() -> SingleInstanceServer:
    '''在首个实例中启动 IPC 监听；返回对象必须保持存活。'''

    global _SERVER
    if _SERVER is not None:
        return _SERVER
    server = SingleInstanceServer()
    server.start()
    _SERVER = server
    return server


def stop_activation_server():
    global _SERVER
    server = _SERVER
    _SERVER = None
    if server is not None:
        server.close()


def send_activation_request(timeout_ms=800) -> bool:
    '''第二个实例请求首实例恢复窗口；连接失败返回 False。'''

    try:
        socket = QLocalSocket()
        socket.connectToServer(_IPC_NAME)
        if not socket.waitForConnected(int(timeout_ms)):
            return False
        socket.write(b'activate\n')
        socket.flush()
        socket.waitForBytesWritten(int(timeout_ms))
        socket.disconnectFromServer()
        return True
    except Exception:
        return False
