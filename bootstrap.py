import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPS = ROOT / ".deps"
if DEPS.is_dir():
    sys.path.insert(0, str(DEPS))
    if os.name == "nt" and (DEPS / "pywin32_system32").is_dir():
        WIN32_DLLS = os.add_dll_directory(str(DEPS / "pywin32_system32"))
        sys.path[:0] = [str(DEPS / "win32"), str(DEPS / "win32/lib")]
