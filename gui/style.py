"""
style.py — app-wide dark theme: a QPalette + QSS stylesheet modeled after
Apple Photos / Google Photos (dark chrome, card-based grids, blue accent,
generous spacing). Call apply_theme(app) once at startup.

Why a QPalette *and* a stylesheet: on Windows, the default "windowsvista"
widget style only partially respects QSS, so unstyled widgets (native
QMessageBox/QFileDialog chrome, default-state QLabel text, etc.) fall back to
whatever the OS palette says. When Windows is in dark mode that fallback
palette is dark-text-on-dark or white-text-on-white in enough places to make
the app unreadable. Forcing the "Fusion" style plus an explicit dark
QPalette means every widget — styled or not — starts from colors we chose,
so the stylesheet below is decoration on top of a theme that's already
correct, not the only thing standing between the app and invisible text.
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

# --- palette --------------------------------------------------------------
BG = "#1c1c1e"           # window background
BG_ELEVATED = "#242426"  # toolbar, status bar, panes
BG_CARD = "#2c2c2e"      # cards, tiles, inputs
BG_CARD_HOVER = "#333335"
BORDER = "#3a3a3c"
BORDER_SUBTLE = "#333335"
TEXT = "#f5f5f7"
TEXT_SECONDARY = "#aeaeb2"
TEXT_TERTIARY = "#8e8e93"  # >= 4.5:1 on BG for placeholder text
ACCENT = "#0a84ff"
ACCENT_HOVER = "#3d9aff"
ACCENT_PRESSED = "#0a6fd6"
ACCENT_SOFT = "rgba(10, 132, 255, 0.22)"
ACCENT_SOFT_BORDER = "rgba(10, 132, 255, 0.55)"
DANGER = "#ff453a"


def apply_theme(app: QApplication):
    """Forces the Fusion style and a matching dark QPalette, then layers the
    QSS stylesheet on top. Call once, right after QApplication is created."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(BG_CARD))
    palette.setColor(QPalette.AlternateBase, QColor(BG_ELEVATED))
    palette.setColor(QPalette.ToolTipBase, QColor(BG_CARD))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(BG_CARD))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.BrightText, QColor(DANGER))
    palette.setColor(QPalette.Link, QColor(ACCENT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.PlaceholderText, QColor(TEXT_TERTIARY))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_TERTIARY))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_TERTIARY))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(TEXT_TERTIARY))
    app.setPalette(palette)

    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
* {{
    outline: none;
}}

QWidget {{
    color: {TEXT};
    font-size: 13px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

QMainWindow, QDialog {{
    background: {BG};
}}

/* --- toolbar --- */
QToolBar {{
    background: {BG_ELEVATED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 10px;
    spacing: 10px;
}}

QToolBar QLabel {{
    color: {TEXT_SECONDARY};
}}

QToolButton {{
    color: {TEXT};
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 12px;
}}

QToolButton:hover {{
    background: {BG_CARD_HOVER};
    border: 1px solid {BORDER};
}}

QToolButton:pressed {{
    background: {BG_CARD};
}}

/* --- tabs (top-level nav, styled like a segmented control) --- */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {BORDER};
    background: {BG};
}}

QTabBar {{
    background: {BG_ELEVATED};
}}

QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 10px 20px;
    margin: 0;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}}

QTabBar::tab:hover {{
    color: {TEXT};
}}

QTabBar::tab:selected {{
    color: {TEXT};
    font-weight: 600;
    border-bottom: 2px solid {ACCENT};
}}

/* --- sidebar nav (Apple/Google Photos style) --- */
QListWidget#sidebar {{
    background: {BG_ELEVATED};
    border: none;
    border-right: 1px solid {BORDER};
    padding: 10px 8px;
    font-size: 13px;
}}

QListWidget#sidebar::item {{
    color: {TEXT_SECONDARY};
    padding: 9px 14px;
    border-radius: 8px;
    margin: 1px 0;
}}

QListWidget#sidebar::item:hover {{
    background: {BG_CARD_HOVER};
    color: {TEXT};
}}

QListWidget#sidebar::item:selected {{
    background: {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}

/* --- buttons --- */
QPushButton {{
    padding: 7px 16px;
    border: 1px solid {BORDER};
    border-radius: 7px;
    background: {BG_CARD};
    color: {TEXT};
}}

QPushButton:hover {{
    background: {BG_CARD_HOVER};
    border-color: #4a4a4d;
}}

QPushButton:pressed {{
    background: #202022;
}}

QPushButton:disabled {{
    color: {TEXT_TERTIARY};
    background: {BG_ELEVATED};
    border-color: {BORDER_SUBTLE};
}}

QPushButton#primaryButton {{
    background: {ACCENT};
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 9px 20px;
}}

QPushButton#primaryButton:hover {{
    background: {ACCENT_HOVER};
}}

QPushButton#primaryButton:pressed {{
    background: {ACCENT_PRESSED};
}}

/* --- dashboard --- */
QLabel#dashboardTitle {{
    font-size: 24px;
    font-weight: 700;
    padding: 4px 0 2px 0;
    color: {TEXT};
}}

QLabel#sectionLabel {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.4px;
    padding: 4px 0;
}}

QFrame#statTile {{
    background: {BG_CARD};
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 14px;
    min-width: 120px;
}}

QLabel#statValue {{
    font-size: 26px;
    font-weight: 700;
    color: {ACCENT};
}}

QLabel#statTitle {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
}}

QFrame#driveRow {{
    background: {BG_CARD};
    border: 1px solid transparent;
    border-radius: 10px;
}}

QFrame#driveRow:hover {{
    background: {BG_CARD_HOVER};
}}

/* --- people stack thumbnails --- */
QFrame#stackBacker {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 10px;
}}

QLabel#stackFront {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

/* --- cards: people / duplicates --- */
QFrame#personCard, QFrame#duplicateCard {{
    background: {BG_CARD};
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 6px;
}}

QFrame#personCard:hover, QFrame#duplicateCard:hover {{
    background: {BG_CARD_HOVER};
}}

/* --- inputs --- */
QLineEdit, QComboBox, QSpinBox {{
    padding: 6px 10px;
    border: 1px solid {BORDER};
    border-radius: 7px;
    background: {BG_CARD};
    color: {TEXT};
    selection-background-color: {ACCENT};
}}

QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}

QLineEdit:hover, QComboBox:hover {{
    border-color: #4a4a4d;
}}

QLineEdit::placeholder {{
    color: {TEXT_TERTIARY};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
}}

QCheckBox, QRadioButton {{
    color: {TEXT};
    spacing: 8px;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    background: {BG_CARD};
}}

QCheckBox::indicator {{
    border-radius: 4px;
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
}}

/* --- photo grid (Library tab) --- */
QListWidget {{
    background: {BG};
    border: none;
    color: {TEXT};
}}

QListWidget::item {{
    border-radius: 10px;
    padding: 6px;
    color: {TEXT_SECONDARY};
    background: transparent;
}}

QListWidget::item:hover {{
    background: {BG_CARD};
}}

QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    border: 1px solid {ACCENT_SOFT_BORDER};
    color: {TEXT};
}}

/* --- tables (Organize tab) --- */
QTableWidget {{
    background: {BG_CARD};
    alternate-background-color: {BG_ELEVATED};
    gridline-color: {BORDER_SUBTLE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 10px;
    color: {TEXT};
}}

QTableWidget::item {{
    padding: 4px 6px;
}}

QTableWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}

QHeaderView::section {{
    background: {BG_ELEVATED};
    color: {TEXT_SECONDARY};
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}

/* --- scroll areas / scrollbars --- */
QScrollArea {{
    background: {BG};
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: #4a4a4d;
    min-height: 30px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background: #5a5a5d;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: #4a4a4d;
    min-width: 30px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #5a5a5d;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* --- slider (thumbnail size) --- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: {ACCENT};
    border-radius: 7px;
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}

/* --- status bar / progress --- */
QStatusBar {{
    background: {BG_ELEVATED};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
}}

QProgressBar {{
    background: {BG_CARD};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
    min-width: 160px;
    max-height: 14px;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}

QMessageBox {{
    background: {BG_ELEVATED};
}}

QToolTip {{
    background: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 6px;
    border-radius: 4px;
}}
"""
