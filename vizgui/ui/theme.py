"""The Qt stylesheet (dark theme)."""
QSS = """
* { font-family: "Inter", "SF Pro Text", "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif; }
QMainWindow, QWidget { background: #0d0f13; color: #e9ebef; font-size: 14px; }

QLabel#title { font-size: 12px; font-weight: 700; color: #7f8794; }
QLabel#status { color: #8b929e; font-size: 12px; }
QLabel#now { color: #f2f4f7; font-size: 15px; font-weight: 600; }
QLabel#dim { color: #6f7682; font-size: 12px; }
QLabel#tvpill { background: #141821; color: #aab1bd; border: 1px solid #242a33;
    border-radius: 13px; padding: 6px 14px; font-size: 12px; font-weight: 600; }
QLabel#badge { background: #e5484d; color: #ffffff; font-size: 10px; font-weight: 800;
    border-radius: 6px; padding: 3px 8px; }

#card { background: #171a20; border: 1px solid #242a33; border-radius: 16px; }
#divider { background: #2a313c; }

QPushButton {
    background: #232832; color: #e9ebef; border: 1px solid #2d343f;
    border-radius: 11px; padding: 9px 12px; font-size: 14px;
}
QPushButton:hover { background: #2c323e; border-color: #3a4250; }
QPushButton:pressed { background: #353d4b; }
QPushButton:disabled { color: #565d68; background: #171b22; border-color: #222831; }
QPushButton.accent { background: #3f7bff; border: 1px solid #5a8dff; color: #ffffff; font-weight: 600; }
QPushButton.accent:hover { background: #5a8dff; }
QPushButton.accent:pressed { background: #356bea; }

#remote { border: 1px solid #2a313c; border-radius: 34px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1b212b, stop:1 #0f131a); }
QPushButton.rkey { background: #1b2029; border: 1px solid #2a313c; }
QPushButton.rkey:hover { background: #262d39; }
QPushButton.rkey:pressed { background: #313a48; }
QPushButton#power { background: #e5484d; border: 1px solid #f05b60; color: #ffffff;
    border-radius: 23px; font-size: 18px; }
QPushButton#power:hover { background: #f05b60; }

QTreeView, QListWidget {
    background: #0f1217; border: 1px solid #242a33; border-radius: 12px;
    padding: 4px; outline: 0;
}
QTreeView::item, QListWidget::item { padding: 5px 6px; border-radius: 6px; }
QTreeView::item:hover, QListWidget::item:hover { background: #191e27; }
QTreeView::item:selected, QListWidget::item:selected { background: #3f7bff; color: #ffffff; }

QComboBox { background: #232832; border: 1px solid #2d343f; border-radius: 9px;
    padding: 5px 10px; min-height: 18px; }
QComboBox:hover { border-color: #3a4250; }
QComboBox::drop-down { border: 0; width: 18px; }
QComboBox QAbstractItemView { background: #171a20; border: 1px solid #2d343f;
    border-radius: 8px; selection-background-color: #3f7bff; outline: 0; padding: 4px; }

QLineEdit { background: #0f1217; border: 1px solid #2d343f; border-radius: 9px;
    padding: 7px 10px; color: #e9ebef; }
QLineEdit:focus { border-color: #3f7bff; }
QCheckBox { spacing: 8px; }

QProgressBar { background: #0f1217; border: none; border-radius: 4px; }
QProgressBar::chunk { background: #3f7bff; border-radius: 4px; }

QDialog { background: #12151b; }
QToolTip { background: #171a20; color: #e9ebef; border: 1px solid #2d343f; padding: 4px 6px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2d343f; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #3a4250; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #2d343f; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
"""
