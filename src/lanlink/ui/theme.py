"""One global stylesheet for the whole application: light, dark, or the system's.

Every page is styled from here rather than widget by widget, so a new page
cannot end up light while the rest of the window is dark. Widgets keep only
their own layout rules — padding, font weight — and take every colour from the
sheet installed on the QApplication.

The choice is remembered in QSettings under ``appearance/theme`` and applied
before the window is shown.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

ORGANISATION = "LanLink"
APPLICATION = "LanLink"
SETTINGS_KEY = "appearance/theme"
UPDATE_REPOSITORY_KEY = "updates/repository"
UPDATE_AT_STARTUP_KEY = "updates/check_at_startup"

SYSTEM = "system"
LIGHT = "light"
DARK = "dark"
MODES = (SYSTEM, LIGHT, DARK)
DEFAULT_MODE = SYSTEM

# Shown in the Settings combo, in this order.
CHOICES: list[tuple[str, str]] = [
    ("System Default", SYSTEM),
    ("Light", LIGHT),
    ("Dark", DARK),
]

# The LanLink blue. Kept recognisable in both themes; the dark variant is a
# little brighter so a selected row stays legible against a dark background.
ACCENT_LIGHT = "#2457d6"
ACCENT_DARK = "#3d7ae8"

PALETTES: dict[str, dict[str, str]] = {
    LIGHT: {
        "window": "#ffffff",
        "sidebar": "#f2f4f8",
        "sidebar_hover": "#e2e7f1",
        "base": "#ffffff",
        "alternate": "#f7f9fc",
        "card": "#f7f9fc",
        "text": "#1c2330",
        "muted": "#5c6473",
        "border": "#dfe4ef",
        "hover": "#eef1f8",
        "accent": ACCENT_LIGHT,
        "accent_text": "#ffffff",
        "disabled": "#a2a9b8",
        "scrollbar": "#c7cede",
    },
    DARK: {
        "window": "#1b1f27",
        "sidebar": "#232833",
        "sidebar_hover": "#2d3542",
        "base": "#21262f",
        "alternate": "#242a34",
        "card": "#232833",
        "text": "#e6e9ef",
        "muted": "#98a1b2",
        "border": "#343c4b",
        "hover": "#2c3340",
        "accent": ACCENT_DARK,
        "accent_text": "#ffffff",
        "disabled": "#6b7382",
        "scrollbar": "#3d4655",
    },
}

# Every rule lives here so the pages cannot drift apart. Widgets that need a
# themed colour ask for it by object name: "muted", "sidebar", "qrCode".
_TEMPLATE = """
QWidget {{
    background: {window};
    color: {text};
}}
QMainWindow, QDialog, QStackedWidget, QSplitter {{ background: {window}; }}
QSplitter::handle {{ background: {border}; width: 1px; }}

QLabel {{ background: transparent; color: {text}; }}
QLabel#muted {{ color: {muted}; }}
QLabel:disabled {{ color: {disabled}; }}

QListWidget#sidebar {{
    border: none;
    background: {sidebar};
    padding-top: 8px;
    font-size: 14px;
}}
QListWidget#sidebar::item {{
    padding: 11px 14px;
    border-radius: 6px;
    margin: 2px 6px;
    color: {text};
}}
QListWidget#sidebar::item:selected {{ background: {accent}; color: {accent_text}; }}
QListWidget#sidebar::item:hover:!selected {{ background: {sidebar_hover}; }}

QPushButton {{
    background: {card};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {accent}; color: {accent_text}; }}
QPushButton:disabled {{ color: {disabled}; border-color: {border}; background: {window}; }}
/* Flat buttons are the breadcrumb trail. The padding above would swallow the
   separator, which is only 18 pixels wide. */
QPushButton:flat, QPushButton:flat:hover {{
    border: none;
    background: transparent;
    padding: 3px 5px;
}}
QPushButton:flat:disabled {{ color: {muted}; background: transparent; border: none; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {base};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {accent};
    selection-color: {accent_text};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {disabled}; background: {window}; }}
/* The spin buttons are left to the style: a stylesheet cannot draw their
   arrows without a resource file, and the palette above colours them. */
QSpinBox {{ padding-right: 20px; }}
QComboBox QAbstractItemView {{
    background: {base};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {accent};
    selection-color: {accent_text};
}}

QCheckBox, QRadioButton {{ background: transparent; color: {text}; spacing: 7px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {disabled}; }}

QTreeView, QListView, QTableView, QListWidget {{
    background: {base};
    alternate-background-color: {alternate};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    selection-background-color: {accent};
    selection-color: {accent_text};
    outline: none;
}}
QTreeView::item:hover, QListView::item:hover, QListWidget::item:hover {{ background: {hover}; }}
QTreeView::item:selected, QListView::item:selected, QListWidget::item:selected,
QTableView::item:selected {{
    background: {accent};
    color: {accent_text};
}}
QHeaderView::section {{
    background: {card};
    color: {muted};
    border: none;
    border-bottom: 1px solid {border};
    border-right: 1px solid {border};
    padding: 6px;
}}
QHeaderView {{ background: {card}; }}
QTableCornerButton::section {{ background: {card}; border: none; }}

QMenu {{
    background: {base};
    color: {text};
    border: 1px solid {border};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {accent}; color: {accent_text}; }}
QMenu::item:disabled {{ color: {disabled}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}

QScrollBar:vertical, QScrollBar:horizontal {{ background: {window}; border: none; }}
QScrollBar:vertical {{ width: 11px; }}
QScrollBar:horizontal {{ height: 11px; }}
QScrollBar::handle {{ background: {scrollbar}; border-radius: 5px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{
    background: {alternate};
    color: {text};
    border: 1px solid {border};
    border-radius: 5px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}

QStatusBar {{ background: {card}; color: {muted}; border-top: 1px solid {border}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background: {card};
    color: {text};
    border: 1px solid {border};
    padding: 4px;
}}
QGroupBox {{ border: 1px solid {border}; border-radius: 6px; margin-top: 8px; padding-top: 8px; }}

QLabel#qrCode {{
    border: 1px solid {border};
    border-radius: 8px;
    background: #ffffff;
}}
QLabel#qrEmpty {{
    border: 1px dashed {border};
    border-radius: 8px;
    color: {muted};
    background: {card};
}}
"""


def theme_choices() -> list[tuple[str, str]]:
    """Label and stored value for the Settings combo, in display order."""
    return list(CHOICES)


def normalise(mode: object) -> str:
    """Any unrecognised value — including None from an empty QSettings — is system."""
    text = str(mode).strip().lower() if mode is not None else ""
    return text if text in MODES else DEFAULT_MODE


def settings() -> QSettings:
    return QSettings(ORGANISATION, APPLICATION)


def saved_theme() -> str:
    return normalise(settings().value(SETTINGS_KEY, DEFAULT_MODE))


def save_theme(mode: object) -> str:
    chosen = normalise(mode)
    store = settings()
    store.setValue(SETTINGS_KEY, chosen)
    store.sync()
    return chosen


def _application(app: object | None = None) -> QApplication | None:
    """The running QApplication, or None.

    QApplication.instance() is declared as returning the base QCoreApplication,
    which has no palette and no stylesheet. Narrowing to the real type once here
    keeps every caller honest without a cast or a suppression.
    """
    candidate = app if app is not None else QApplication.instance()
    return candidate if isinstance(candidate, QApplication) else None


def _scheme_name(app: QApplication) -> str:
    """What Qt says the desktop's colour scheme is, or "" when it does not know.

    Qt 6.5 answers this on Windows and macOS. Older versions and some Linux
    desktops do not have the hint at all, hence the tolerant lookup.
    """
    hints = getattr(app, "styleHints", None)
    if not callable(hints):
        return ""
    scheme = getattr(hints(), "colorScheme", None)
    if not callable(scheme):
        return ""
    value = scheme()
    return str(getattr(value, "name", value) or "").lower()


def saved_update_repository() -> str:
    return str(settings().value(UPDATE_REPOSITORY_KEY, "") or "").strip()


def save_update_repository(repository: str) -> str:
    cleaned = (repository or "").strip()
    store = settings()
    store.setValue(UPDATE_REPOSITORY_KEY, cleaned)
    store.sync()
    return cleaned


def checks_updates_at_startup() -> bool:
    value = settings().value(UPDATE_AT_STARTUP_KEY, False)
    return str(value).strip().lower() in {"true", "1", "yes"}


def save_check_at_startup(enabled: bool) -> bool:
    store = settings()
    store.setValue(UPDATE_AT_STARTUP_KEY, bool(enabled))
    store.sync()
    return bool(enabled)


def detect_system_theme(app: object | None = None) -> str:
    """Light or dark, from whatever the platform is telling Qt.

    No extra package and nothing platform-specific to import: the colour-scheme
    hint when it exists, otherwise how bright the window colour is.
    """
    application = _application(app)
    if application is None:
        return LIGHT

    name = _scheme_name(application).lower()
    if "dark" in name:
        return DARK
    if "light" in name:
        return LIGHT

    colour = application.palette().color(QPalette.ColorRole.Window)
    # Rec. 601 luma: a dim window colour means the desktop is in dark mode.
    luma = (0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()) / 255
    return DARK if luma < 0.5 else LIGHT


def resolve(mode: object, app: object | None = None) -> str:
    """The concrete theme to paint: system becomes light or dark."""
    chosen = normalise(mode)
    return detect_system_theme(app) if chosen == SYSTEM else chosen


def stylesheet(theme: str) -> str:
    """The whole application's sheet for a concrete theme."""
    palette = PALETTES.get(theme, PALETTES[LIGHT])
    return _TEMPLATE.format(**palette)


def colours(mode: object, app: object | None = None) -> dict[str, str]:
    return PALETTES[resolve(mode, app)]


def palette_for(theme: str) -> QPalette:
    """A matching QPalette, for the parts Qt draws itself.

    A stylesheet cannot supply the combo box arrow or the spin box buttons —
    those come from the style, which reads the palette. Without this they stay
    dark-on-dark.
    """
    from PySide6.QtGui import QColor

    values = PALETTES.get(theme, PALETTES[LIGHT])
    window = QColor(values["window"])
    base = QColor(values["base"])
    text = QColor(values["text"])
    accent = QColor(values["accent"])
    disabled = QColor(values["disabled"])

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(values["alternate"]))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor(values["card"]))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(values["card"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(values["accent_text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(values["muted"]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def apply_theme(app: object | None, mode: object) -> str:
    """Install the sheet for ``mode`` and return the theme actually painted."""
    application = _application(app)
    theme = resolve(mode, application)
    if application is not None:
        application.setPalette(palette_for(theme))
        application.setStyleSheet(stylesheet(theme))
    return theme
