"""Small, dependency-free helpers for the Windows validation fixtures.

Large model sizes are represented by sparse files inside a test-owned
TemporaryDirectory. No model is downloaded or loaded into memory.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import time


def run_process(command: list[str], *, timeout: int = 90, cwd: Path | None = None,
                env: dict[str, str] | None = None) -> dict:
    start = time.monotonic()
    try:
        result = subprocess.run([str(item) for item in command], cwd=cwd, env=env,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
        return {"exit_code": result.returncode, "timed_out": False,
                "stdout": result.stdout, "stderr": result.stderr,
                "seconds": round(time.monotonic() - start, 3)}
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
        return {"exit_code": None, "timed_out": True,
                "stdout": decode(exc.stdout), "stderr": decode(exc.stderr),
                "seconds": round(time.monotonic() - start, 3)}
    except OSError as exc:
        return {"exit_code": None, "timed_out": False,
                "stdout": "", "stderr": str(exc),
                "seconds": round(time.monotonic() - start, 3)}


def allocation_bytes(path: Path) -> int:
    if os.name != "nt":
        return path.stat().st_blocks * 512
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    method = kernel.GetCompressedFileSizeW
    method.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    method.restype = wintypes.DWORD
    high = wintypes.DWORD()
    ctypes.set_last_error(0)
    low = method(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    return (high.value << 32) | low


def sparse_file(path: Path, size: int, prefix: bytes = b"TEST") -> dict:
    """Create a new small-allocation fixture, failing before a dense resize.

    Windows requires FSCTL_SET_SPARSE before extending the length. A filesystem
    that cannot make sparse files is an explicit test failure, never a reason
    to allocate a 16-GB fixture instead.
    """
    if size < len(prefix):
        prefix = prefix[:size]
    with path.open("xb") as stream:
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            method = kernel.DeviceIoControl
            method.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
                               wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
            method.restype = wintypes.BOOL
            count = wintypes.DWORD()
            if not method(msvcrt.get_osfhandle(stream.fileno()), 0x000900C4,
                          None, 0, None, 0, ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
        stream.write(prefix)
        stream.truncate(size)
    actual = path.stat().st_size
    allocated = allocation_bytes(path)
    if actual != size or allocated > 1024 * 1024:
        raise RuntimeError(f"Sparse fixture was not created as expected: {path.name}")
    return {"name": path.name, "logical_bytes": actual, "allocated_bytes": allocated,
            "contents": "size-only sparse fixture; not a model"}
