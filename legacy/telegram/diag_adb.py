"""Diagnóstico: comprueba que el bot encuentra adb. Solo lee, no toca la pantalla.
    python diag_adb.py
"""
import os
import shutil
import subprocess
import sys

import adb_utils

print("python      :", sys.executable)
print("cwd         :", os.getcwd())
print("ADB_PATH    :", repr(adb_utils.ADB_PATH))
print("existe      :", os.path.exists(adb_utils.ADB_PATH))
print("which(adb)  :", shutil.which("adb"))
print("env ADB_PATH:", repr(os.environ.get("ADB_PATH")))
try:
    r = subprocess.run([adb_utils.ADB_PATH, "version"], capture_output=True, text=True)
    print("adb version :", r.stdout.splitlines()[0] if r.stdout else r.stderr)
except Exception as exc:
    print("FALLA       :", type(exc).__name__, exc)
