"""凭据安全存储（v0.18 Phase 2.8-B）。

正式生产路径禁止明文 API Key 落盘。Windows 下优先级：
1. keyring / Windows Credential Manager（service=TJU_Info_Retrieval_AI）；
2. Windows DPAPI 加密本地文件（%LOCALAPPDATA%\\TJU_Info_Retrieval\\
   config\\credential.dat）——DPAPI 由当前 Windows 用户账户派生密钥，
   不自行设计 AES、不用固定密码、不用 Base64 冒充加密；
3. 二者均不可用 → 仅进程内内存使用（memory-only），提示“本次仅临时使用”。

禁止：明文 API Key 写入 JSON / txt / log / report。
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from tju_info_retrieval import app_paths

KEYRING_SERVICE = "TJU_Info_Retrieval_AI"
KEYRING_USERNAME = "default"
# DPAPI 加密凭据文件（内容为 DPAPI 加密后 bytes 的 base64 包装，
# 不含明文；仅 Windows）
CREDENTIAL_FILENAME = "credential.dat"

# 凭据后端标识（写入配置 JSON 的 credential_backend 字段）
BACKEND_KEYRING = "keyring"
BACKEND_DPAPI = "dpapi"
BACKEND_MEMORY = "memory"


def config_dir() -> Path:
    """凭据目录：frozen/测试隔离 → LocalAppData/config；开发态 → runtime/。"""
    if app_paths.is_frozen() or bool(
            os.environ.get(app_paths.TEST_USER_DATA_ROOT_ENV, "").strip()):
        return app_paths.user_data_root() / "config"
    return app_paths.project_root() / "runtime"


def credential_file() -> Path:
    return config_dir() / CREDENTIAL_FILENAME


# ---------------------------------------------------------------------------
# Windows DPAPI（win32crypt 优先；ctypes 兜底，均无新依赖）
# ---------------------------------------------------------------------------

def _dpapi_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32crypt  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _dpapi_encrypt(plaintext: str) -> bytes:
    """用当前 Windows 用户账户 DPAPI 加密（无固定密钥）。"""
    data = plaintext.encode("utf-8")
    if os.name != "nt":
        raise RuntimeError("DPAPI 仅支持 Windows")
    try:
        import win32crypt

        blob = win32crypt.CryptProtectData(
            data, None, None, None, None, 0)
        return blob
    except Exception:  # noqa: BLE001 - 回退 ctypes
        pass
    # ctypes 兜底：CryptProtectData
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB(len(data), ctypes.cast(
        ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0,
            ctypes.byref(out_blob)):
        raise RuntimeError("DPAPI CryptProtectData failed")
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(blob: bytes) -> str:
    """DPAPI 解密（当前 Windows 用户账户）。"""
    if os.name != "nt":
        raise RuntimeError("DPAPI 仅支持 Windows")
    try:
        import win32crypt

        data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data[1].decode("utf-8")
    except Exception:  # noqa: BLE001 - 回退 ctypes
        pass
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(blob)
    in_blob = DATA_BLOB(len(blob), ctypes.cast(
        buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0,
            ctypes.byref(out_blob)):
        raise RuntimeError("DPAPI CryptUnprotectData failed")
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw.decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ---------------------------------------------------------------------------
# CredentialStore：keyring → DPAPI → memory-only
# ---------------------------------------------------------------------------

class CredentialStore:
    """凭据存取：keyring → DPAPI credential.dat → memory-only。

    所有写操作绝不落明文；memory-only 时仅存于进程内存，
    并可通过 backend 属性告知 UI 提示“本次仅临时使用”。
    """

    def __init__(self) -> None:
        self._memory_key: str = ""
        self._backend: str = BACKEND_MEMORY
        # 探测实际可用后端（惰性：首次读写时再判定更稳）
        self._probed = False

    def _probe(self) -> None:
        if self._probed:
            return
        if _keyring_available():
            self._backend = BACKEND_KEYRING
        elif _dpapi_available():
            self._backend = BACKEND_DPAPI
        else:
            self._backend = BACKEND_MEMORY
        self._probed = True

    @property
    def backend(self) -> str:
        self._probe()
        return self._backend

    @property
    def is_persistent(self) -> bool:
        """是否可持久化（keyring/dpapi 均可跨重启）。"""
        return self.backend in (BACKEND_KEYRING, BACKEND_DPAPI)

    # ---- keyring ----
    def _keyring_get(self) -> str:
        try:
            return str(_get_keyring().get_password(
                KEYRING_SERVICE, KEYRING_USERNAME) or "")
        except Exception:  # noqa: BLE001
            return ""

    def _keyring_set(self, value: str) -> bool:
        try:
            _get_keyring().set_password(
                KEYRING_SERVICE, KEYRING_USERNAME, value)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _keyring_delete(self) -> None:
        try:
            _get_keyring().delete_password(
                KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:  # noqa: BLE001
            pass

    # ---- DPAPI ----
    def _dpapi_get(self) -> str:
        path = credential_file()
        if not path.is_file():
            return ""
        try:
            blob = base64.b64decode(
                path.read_bytes())  # 文件存 DPAPI bytes 的 b64 包装
            return _dpapi_decrypt(blob)
        except Exception:  # noqa: BLE001
            return ""

    def _dpapi_set(self, value: str) -> bool:
        try:
            blob = _dpapi_encrypt(value)
            path = credential_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64encode(blob))
            return True
        except Exception:  # noqa: BLE001
            return False

    def _dpapi_delete(self) -> None:
        path = credential_file()
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass

    # ---- 公开接口 ----
    def get(self) -> str:
        self._probe()
        if self._backend == BACKEND_KEYRING:
            value = self._keyring_get()
            if value:
                return value
            # keyring 有后端但无记录 → 回退 DPAPI
            if _dpapi_available():
                value = self._dpapi_get()
                if value:
                    return value
            return ""
        if self._backend == BACKEND_DPAPI:
            return self._dpapi_get()
        return self._memory_key

    def save(self, value: str) -> str:
        """保存凭据，返回实际使用的后端标识。"""
        value = (value or "").strip()
        self._probe()
        if self._backend == BACKEND_KEYRING:
            if self._keyring_set(value):
                return BACKEND_KEYRING
            if _dpapi_available() and self._dpapi_set(value):
                self._backend = BACKEND_DPAPI
                return BACKEND_DPAPI
            self._backend = BACKEND_MEMORY
            self._memory_key = value
            return BACKEND_MEMORY
        if self._backend == BACKEND_DPAPI:
            if self._dpapi_set(value):
                return BACKEND_DPAPI
            self._backend = BACKEND_MEMORY
            self._memory_key = value
            return BACKEND_MEMORY
        self._memory_key = value
        return BACKEND_MEMORY

    def clear(self) -> None:
        self._probe()
        self._keyring_delete()
        self._dpapi_delete()
        self._memory_key = ""


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _get_keyring():
    import keyring

    return keyring