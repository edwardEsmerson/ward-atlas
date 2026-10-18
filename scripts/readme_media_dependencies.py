"""Load the pinned image decoder; bootstrap locally in source-only checkouts."""
from __future__ import annotations

import fcntl
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILLOW = "Pillow==11.3.0"


def image_module():
    try:
        return importlib.import_module("PIL.Image")
    except ModuleNotFoundError:
        pass
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    env = local / "readme-media-venv"
    python = env / "bin/python"
    site = env / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    # Parallel checks may share a checkout, but never a partial installation.
    with (local / "readme-media.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (site / "PIL/Image.py").exists():
            uv = shutil.which("uv")
            if uv:
                subprocess.run([uv, "venv", "--python", sys.executable, str(env)], check=True)
                command = [uv, "pip", "install", "--python", str(python)]
            else:
                subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
                command = [str(python), "-m", "pip", "install"]
            subprocess.run(command + ["--only-binary=:all:", PILLOW], check=True)
    sys.path.insert(0, str(site))
    importlib.invalidate_caches()
    return importlib.import_module("PIL.Image")
