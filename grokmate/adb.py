"""ADB helpers — device discovery, app checks, launching."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

GROK_PACKAGE = "ai.x.grok"


@dataclass
class DeviceInfo:
    serial: str
    state: str  # e.g. "device", "offline"


def list_devices() -> list[DeviceInfo]:
    """List connected ADB devices."""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return []
    devices = []
    for line in result.stdout.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append(DeviceInfo(serial=parts[0], state=parts[1]))
    return devices


def get_connected_device() -> Optional[DeviceInfo]:
    """Return the first connected (state=device) device or None."""
    for d in list_devices():
        if d.state == "device":
            return d
    return None


def is_grok_installed(serial: Optional[str] = None) -> bool:
    """Check if the Grok app is installed on the device."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += ["shell", "pm", "list", "packages"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return f"package:{GROK_PACKAGE}" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def launch_grok(serial: Optional[str] = None) -> None:
    """Launch the Grok app (or bring to foreground)."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += [
        "shell",
        "monkey",
        "-p",
        GROK_PACKAGE,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def scrcpy_available() -> bool:
    """Check if scrcpy binary is on PATH."""
    return shutil.which("scrcpy") is not None
