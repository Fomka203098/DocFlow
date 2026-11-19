"""Keep Windows awake only while the tunnel process is alive."""
import ctypes
import sys

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
kernel.OpenProcess.restype = ctypes.c_void_p
kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
kernel.SetThreadExecutionState.argtypes = (ctypes.c_uint32,)
kernel.SetThreadExecutionState.restype = ctypes.c_uint32
handle = kernel.OpenProcess(0x00100000, False, int(sys.argv[1]))
if not handle:
    raise OSError('Could not monitor tunnel process')
if not kernel.SetThreadExecutionState(0x80000001):
    kernel.CloseHandle(handle)
    raise OSError('Could not request system wakefulness')
try:
    kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
finally:
    kernel.SetThreadExecutionState(0x80000000)
    kernel.CloseHandle(handle)
