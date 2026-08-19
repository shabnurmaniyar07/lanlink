"""The application theme: light, dark, system, and remembering the choice."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lanlink.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Keep QSettings inside the test's own folder, not the real user config."""
    path = tmp_path / "settings.ini"

    def local() -> QSettings:
        return QSettings(str(path), QSettings.Format.IniFormat)

    monkeypatch.setattr(theme, "settings", local)
    return local


@pytest.fixture(autouse=True)
def restore_stylesheet(qapp):
    original = qapp.styleSheet()
    yield
    qapp.setStyleSheet(original)


# ------------------------------------------------------------------- accepted


@pytest.mark.parametrize("mode", ["system", "light", "dark"])
def test_every_mode_is_accepted_and_paints_something(qapp, mode: str) -> None:
    painted = theme.apply_theme(qapp, mode)

    assert painted in {theme.LIGHT, theme.DARK}
    assert qapp.styleSheet(), f"{mode} installed an empty stylesheet"


def test_system_resolves_to_a_concrete_theme(qapp) -> None:
    assert theme.detect_system_theme(qapp) in {theme.LIGHT, theme.DARK}
    assert theme.resolve(theme.SYSTEM, qapp) == theme.detect_system_theme(qapp)


def test_light_and_dark_are_taken_literally(qapp) -> None:
    assert theme.resolve("light", qapp) == theme.LIGHT
    assert theme.resolve("dark", qapp) == theme.DARK
    assert theme.apply_theme(qapp, "dark") == theme.DARK
    assert theme.apply_theme(qapp, "light") == theme.LIGHT


@pytest.mark.parametrize(
    "value", ["", "  ", "solarized", "Dark Mode", None, 7, "systemm", "LIGHTS"]
)
def test_an_invalid_theme_falls_back_to_system(value: object) -> None:
    assert theme.normalise(value) == theme.SYSTEM


@pytest.mark.parametrize(
    ("value", "expected"), [("LIGHT", "light"), (" Dark ", "dark"), ("System", "system")]
)
def test_case_and_spacing_are_forgiven(value: str, expected: str) -> None:
    assert theme.normalise(value) == expected


def test_an_invalid_theme_still_paints_rather_than_crashing(qapp) -> None:
    painted = theme.apply_theme(qapp, "chartreuse")
    assert painted in {theme.LIGHT, theme.DARK}
    assert qapp.styleSheet()


def test_applying_a_theme_without_an_application_does_not_crash() -> None:
    assert theme.apply_theme(None, "light") == theme.LIGHT


# ---------------------------------------------------------------- persistence


def test_the_choice_is_saved_and_read_back(store) -> None:
    assert theme.saved_theme() == theme.DEFAULT_MODE  # nothing stored yet

    assert theme.save_theme("dark") == "dark"
    assert theme.saved_theme() == "dark"

    theme.save_theme("light")
    assert theme.saved_theme() == "light"


def test_the_saved_value_survives_a_restart(store) -> None:
    """A fresh QSettings object is what the next launch will see."""
    theme.save_theme("dark")

    reopened = store()
    assert reopened.value(theme.SETTINGS_KEY) == "dark"
    assert theme.saved_theme() == "dark"


def test_an_invalid_stored_value_is_read_as_system(store) -> None:
    store().setValue(theme.SETTINGS_KEY, "neon")
    assert theme.saved_theme() == theme.SYSTEM


def test_an_invalid_value_is_never_written(store) -> None:
    assert theme.save_theme("neon") == theme.SYSTEM
    assert store().value(theme.SETTINGS_KEY) == theme.SYSTEM


def test_the_settings_key_and_names_are_the_documented_ones() -> None:
    assert theme.SETTINGS_KEY == "appearance/theme"
    assert theme.ORGANISATION == "LanLink"
    assert theme.APPLICATION == "LanLink"
    assert theme.MODES == ("system", "light", "dark")
    assert theme.DEFAULT_MODE == "system"
    assert theme.theme_choices() == [
        ("System Default", "system"),
        ("Light", "light"),
        ("Dark", "dark"),
    ]


# ------------------------------------------------------------------ at runtime


def test_the_theme_can_be_changed_while_running(qapp) -> None:
    theme.apply_theme(qapp, "light")
    light = qapp.styleSheet()

    theme.apply_theme(qapp, "dark")
    dark = qapp.styleSheet()

    assert light and dark
    assert light != dark, "switching theme did not change the stylesheet"
    assert theme.PALETTES["light"]["window"] in light
    assert theme.PALETTES["dark"]["window"] in dark


def test_system_follows_a_dark_desktop_palette(qapp, monkeypatch) -> None:
    """With no colour-scheme hint, the window colour's brightness decides."""
    monkeypatch.setattr(theme, "_scheme_name", lambda app: "")

    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    monkeypatch.setattr(qapp, "palette", lambda: dark)
    assert theme.detect_system_theme(qapp) == theme.DARK

    light = QPalette()
    light.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    monkeypatch.setattr(qapp, "palette", lambda: light)
    assert theme.detect_system_theme(qapp) == theme.LIGHT


def test_a_colour_scheme_hint_wins_over_the_palette(qapp, monkeypatch) -> None:
    monkeypatch.setattr(theme, "_scheme_name", lambda app: "Dark")
    light = QPalette()
    light.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    monkeypatch.setattr(qapp, "palette", lambda: light)

    assert theme.detect_system_theme(qapp) == theme.DARK


# -------------------------------------------------------------- the sheet itself


@pytest.mark.parametrize("name", ["light", "dark"])
def test_each_sheet_covers_every_widget_the_app_uses(name: str) -> None:
    sheet = theme.stylesheet(name)
    for selector in (
        "QWidget",
        "QListWidget#sidebar",
        "QPushButton",
        "QLineEdit",
        "QComboBox",
        "QCheckBox",
        "QTreeView",
        "QListView",
        "QTableView",
        "QHeaderView::section",
        "QMenu",
        "QScrollBar",
        "QProgressBar",
        "QStatusBar",
        "QToolTip",
        "QDialog",
    ):
        assert selector in sheet, f"{name} theme says nothing about {selector}"
    assert "{" in sheet and "}" in sheet
    assert "{window}" not in sheet, "a placeholder was left unformatted"


def test_the_lanlink_blue_selects_in_both_themes() -> None:
    for name in ("light", "dark"):
        accent = theme.PALETTES[name]["accent"]
        sheet = theme.stylesheet(name)
        assert accent.startswith("#")
        assert f"background: {accent}" in sheet
        assert sheet.count(accent) >= 4, "the accent is barely used"
    # Both are recognisably the same blue: blue channel dominates.
    for name in ("light", "dark"):
        colour = QColor(theme.PALETTES[name]["accent"])
        assert colour.blue() > colour.red() and colour.blue() > colour.green()


def test_dark_is_dark_but_not_black_and_the_sidebar_is_lighter() -> None:
    palette = theme.PALETTES["dark"]
    window = QColor(palette["window"])
    sidebar = QColor(palette["sidebar"])
    text = QColor(palette["text"])

    assert window.lightness() < 90, "the dark window is not dark"
    assert window.lightness() > 10, "the dark window is nearly pure black"
    assert sidebar.lightness() > window.lightness(), "the sidebar should lift off the background"
    assert text.lightness() > 180, "dark theme text is not readable"


def test_light_is_light_and_readable() -> None:
    palette = theme.PALETTES["light"]
    assert QColor(palette["window"]).lightness() > 230
    assert QColor(palette["text"]).lightness() < 80
    assert QColor(palette["muted"]).lightness() < QColor(palette["window"]).lightness()


def test_disabled_text_stays_visible_in_both_themes() -> None:
    for name in ("light", "dark"):
        palette = theme.PALETTES[name]
        disabled = QColor(palette["disabled"]).lightness()
        window = QColor(palette["window"]).lightness()
        assert abs(disabled - window) > 25, f"{name} disabled text blends into the background"


def test_both_palettes_define_the_same_tokens() -> None:
    assert set(theme.PALETTES["light"]) == set(theme.PALETTES["dark"])
    assert theme.colours("light")["window"] == theme.PALETTES["light"]["window"]
