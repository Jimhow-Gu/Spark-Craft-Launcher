'''本地敏感数据保护

Windows 下使用 DPAPI（当前用户）加密，其他平台退化为 Base64 编码。
只用于“记住第三方账号密码”这类用户主动勾选的功能。
'''

from __future__ import annotations
import base64
import ctypes
import sys
from ctypes import wintypes
_DPAPI_PREFIX = 'dpapi:'
_B64_PREFIX = 'b64:'

class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

def _protect_windows(data):
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data)
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    ok = crypt32.CryptProtectData(ctypes.byref(blob_in), 'PMGL', None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.GetLastError(), 'CryptProtectData 失败')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)

def _unprotect_windows(data):
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data)
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.GetLastError(), 'CryptUnprotectData 失败')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)

def protect_secret(text):
    '''加密文本；失败时使用可逆编码，保证功能可用。'''

    raw = (text or '').encode('utf-8')
    if not raw:
        return ''
    if sys.platform == 'win32':
        try:
            return _DPAPI_PREFIX + base64.b64encode(_protect_windows(raw)).decode('ascii')
        except Exception:
            pass
    return _B64_PREFIX + base64.b64encode(raw).decode('ascii')

def unprotect_secret(value):
    value = value or ''
    if not value:
        return ''
    if value.startswith(_DPAPI_PREFIX):
        blob = base64.b64decode(value[len(_DPAPI_PREFIX):])
        if sys.platform == 'win32':
            try:
                return _unprotect_windows(blob).decode('utf-8', 'replace')
            except Exception:
                return ''
        return ''
    if value.startswith(_B64_PREFIX):
        try:
            return base64.b64decode(value[len(_B64_PREFIX):]).decode('utf-8', 'replace')
        except Exception:
            return ''
    return value

