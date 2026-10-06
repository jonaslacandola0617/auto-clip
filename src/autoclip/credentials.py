from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Secure credential storage is available only on Windows.")
    source, source_buffer = _blob_from_bytes(value)
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not function(ctypes.byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(destination)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)
        del source_buffer


class CredentialStore:
    """Stores the Gemini key encrypted for the current Windows user using DPAPI."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def set_gemini_key(self, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Enter a Gemini API key.")
        encrypted = _crypt(cleaned.encode("utf-8"), protect=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_bytes(encrypted)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get_gemini_key(self) -> str | None:
        if not self.path.is_file():
            return None
        return _crypt(self.path.read_bytes(), protect=False).decode("utf-8")

    def clear_gemini_key(self) -> None:
        self.path.unlink(missing_ok=True)
