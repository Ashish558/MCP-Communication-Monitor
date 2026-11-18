"""Windows Job Object helper: child processes die when the shim dies.

Without this, killing a shim (e.g. Claude Desktop closing) could leave the real
MCP server running as an orphan. On other platforms this is a no-op.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


class KillOnCloseJob:
    def __init__(self) -> None:
        self._handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            handle = k32.CreateJobObjectW(None, None)
            if not handle:
                return

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in
                            ("ReadOp", "WriteOp", "OtherOp", "ReadBytes", "WriteBytes", "OtherBytes")]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                return
            self._handle = handle
            self._k32 = k32
        except Exception as exc:  # never let this helper break the shim
            log.warning("job object unavailable: %s", exc)

    def add(self, process) -> None:
        """Put a ``subprocess.Popen`` into the job."""
        if self._handle is None:
            return
        try:
            self._k32.AssignProcessToJobObject(self._handle, int(process._handle))
        except Exception as exc:
            log.warning("could not assign child to job object: %s", exc)
