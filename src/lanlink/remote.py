"""Native OS remote control and clipboard sync handlers for LanLink desktop."""

from __future__ import annotations

import ctypes
import sys
from typing import Any

# Windows Virtual-Key and Mouse Event Constants
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800

_last_clipboard_text: str = ""


def get_system_clipboard() -> str:
    global _last_clipboard_text
    return _last_clipboard_text


def set_system_clipboard(text: str) -> None:
    global _last_clipboard_text
    _last_clipboard_text = text


def handle_mouse_event(data: dict[str, Any]) -> bool:
    """Move cursor, perform clicks, or scroll on the desktop host."""
    if sys.platform != "win32":
        return False

    action = data.get("action", "move")
    dx = int(data.get("dx", 0))
    dy = int(data.get("dy", 0))

    user32 = ctypes.windll.user32

    if action == "move":
        user32.mouse_event(MOUSEEVENTF_MOVE, dx, dy, 0, 0)
        return True
    elif action == "click":
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True
    elif action == "rclick":
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        return True
    elif action == "scroll":
        delta = int(data.get("scroll", dy * 10))
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        return True

    return False


def handle_media_event(action: str) -> bool:
    """Send native media playback key events on the desktop host."""
    if sys.platform != "win32":
        return False

    key_map = {
        "play_pause": VK_MEDIA_PLAY_PAUSE,
        "volume_up": VK_VOLUME_UP,
        "volume_down": VK_VOLUME_DOWN,
        "mute": VK_VOLUME_MUTE,
        "next": VK_MEDIA_NEXT_TRACK,
        "prev": VK_MEDIA_PREV_TRACK,
    }

    vk = key_map.get(action)
    if vk is None:
        return False

    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return True
