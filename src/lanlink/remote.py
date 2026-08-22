"""Native OS remote control, screen capture, keyboard injection, and clipboard handlers for LanLink desktop."""

from __future__ import annotations

import ctypes
import sys
from typing import Any

# Windows Virtual-Key and Mouse Event Constants
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_HOME = 0x24
VK_END = 0x23
VK_CONTROL = 0x11
VK_LWIN = 0x5B

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

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

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
    elif action == "abs_move":
        x_ratio = float(data.get("x_ratio", 0.0))
        y_ratio = float(data.get("y_ratio", 0.0))
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        target_x = max(0, min(screen_w - 1, int(x_ratio * screen_w)))
        target_y = max(0, min(screen_h - 1, int(y_ratio * screen_h)))
        user32.SetCursorPos(target_x, target_y)
        if data.get("click"):
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif data.get("rclick"):
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        return True

    return False


def handle_keyboard_event(data: dict[str, Any]) -> bool:
    """Type text or trigger special key commands on the desktop host."""
    if sys.platform != "win32":
        return False

    action = data.get("action", "key")
    user32 = ctypes.windll.user32

    if action == "text":
        text = str(data.get("text", ""))
        for char in text:
            code = ord(char)
            user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
            user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
        return True

    key_map = {
        "enter": VK_RETURN,
        "backspace": VK_BACK,
        "tab": VK_TAB,
        "escape": VK_ESCAPE,
        "space": VK_SPACE,
        "up": VK_UP,
        "down": VK_DOWN,
        "left": VK_LEFT,
        "right": VK_RIGHT,
        "delete": VK_DELETE,
        "home": VK_HOME,
        "end": VK_END,
        "win": VK_LWIN,
    }

    key_name = str(data.get("key", "")).lower()

    if key_name in ("ctrl_c", "ctrl_v", "ctrl_a", "ctrl_z"):
        target_char = key_name.split("_")[1].upper()
        target_vk = ord(target_char)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(target_vk, 0, 0, 0)
        user32.keybd_event(target_vk, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True

    vk = key_map.get(key_name)
    if vk is not None:
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
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
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return True


def capture_screen_jpeg(quality: int = 55, max_width: int = 1280) -> bytes:
    """Capture current primary desktop display frame compressed as JPEG."""
    try:
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if not app:
            return b""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return b""
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            return b""
        if pixmap.width() > max_width:
            pixmap = pixmap.scaledToWidth(max_width)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "JPEG", quality)
        return bytes(buf.data())
    except Exception:
        return b""
