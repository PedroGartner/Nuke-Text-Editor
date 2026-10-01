"""Small shared widgets and styles."""

from .qt import QtCore, QtGui, QtWidgets
from . import themes


def dialog_style():
    return themes.dialog_style()


class HoverButton(QtWidgets.QPushButton):
    """Push button with a consistent dark style and hover effect."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setStyleSheet(themes.button_style())


class ColorTabBar(QtWidgets.QTabBar):
    """Tab bar that draws a thin colored underline per tab (file type)."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        for index in range(self.count()):
            rect = self.tabRect(index)
            color = self.tabTextColor(index)
            if color.isValid():
                painter.fillRect(QtCore.QRect(rect.left() + 4, rect.bottom() - 2, rect.width() - 8, 2), color)
        painter.end()


def monospace_font(size=9):
    font = QtGui.QFont("Consolas")
    font.setStyleHint(QtGui.QFont.Monospace)
    font.setPointSize(size)
    return font


def small_label(text, color="#888888"):
    label = QtWidgets.QLabel(text)
    label.setStyleSheet("color: {}; font-size: 10px;".format(color))
    return label
