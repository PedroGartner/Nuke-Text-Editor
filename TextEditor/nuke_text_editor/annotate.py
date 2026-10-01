"""Draw arrows, circles, boxes and freehand marks on a captured frame."""

import math
import os

from .qt import QtCore, QtGui, QtWidgets, event_pos, qt_exec
from . import themes
from .widgets import HoverButton


class _Canvas(QtWidgets.QWidget):
    """Shows the image scaled to fit and records shapes in image pixels."""

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image
        self.shapes = []   # (tool, color, width, [QPointF...])
        self.tool = "Arrow"
        self.color = QtGui.QColor("#ff3030")
        self.pen_width = 4
        self._current = None
        self.setMinimumSize(400, 260)
        self.setCursor(QtCore.Qt.CrossCursor)

    def _scale_and_offset(self):
        if self.image.isNull():
            return 1.0, QtCore.QPointF(0, 0)
        scale = min(self.width() / float(self.image.width()), self.height() / float(self.image.height()))
        offset = QtCore.QPointF((self.width() - self.image.width() * scale) / 2.0,
                                (self.height() - self.image.height() * scale) / 2.0)
        return scale, offset

    def _to_image(self, pos):
        scale, offset = self._scale_and_offset()
        return QtCore.QPointF((pos.x() - offset.x()) / scale, (pos.y() - offset.y()) / scale)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            point = self._to_image(event_pos(event))
            self._current = (self.tool, QtGui.QColor(self.color), self.pen_width, [point, point])
            self.update()

    def mouseMoveEvent(self, event):
        if self._current is not None:
            point = self._to_image(event_pos(event))
            if self._current[0] == "Freehand":
                self._current[3].append(point)
            else:
                self._current[3][-1] = point
            self.update()

    def mouseReleaseEvent(self, event):
        if self._current is not None and event.button() == QtCore.Qt.LeftButton:
            self.shapes.append(self._current)
            self._current = None
            self.update()

    def undo(self):
        if self.shapes:
            self.shapes.pop()
            self.update()

    @staticmethod
    def draw_shapes(painter, shapes, scale=1.0):
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        for tool, color, width, points in shapes:
            pen = QtGui.QPen(color, width * scale)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            start, end = points[0], points[-1]
            if tool == "Freehand":
                path = QtGui.QPainterPath(points[0])
                for point in points[1:]:
                    path.lineTo(point)
                painter.drawPath(path)
            elif tool == "Box":
                painter.drawRect(QtCore.QRectF(start, end).normalized())
            elif tool == "Circle":
                painter.drawEllipse(QtCore.QRectF(start, end).normalized())
            else:  # Arrow
                painter.drawLine(start, end)
                angle = math.atan2(end.y() - start.y(), end.x() - start.x())
                size = max(12.0, width * 4.0) * scale
                for side in (-1, 1):
                    tip = QtCore.QPointF(end.x() - size * math.cos(angle + side * 0.45),
                                         end.y() - size * math.sin(angle + side * 0.45))
                    painter.drawLine(end, tip)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(themes.color("editor")))
        scale, offset = self._scale_and_offset()
        painter.translate(offset)
        painter.scale(scale, scale)
        painter.drawImage(QtCore.QPointF(0, 0), self.image)
        shapes = self.shapes + ([self._current] if self._current else [])
        self.draw_shapes(painter, shapes, 1.0)
        painter.end()


class AnnotateDialog(QtWidgets.QDialog):
    """Returns the path of the annotated image (or the original one)."""

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Annotate Capture")
        self.setStyleSheet(themes.dialog_style())
        self.image_path = image_path
        self.result_path = image_path
        self.canvas = _Canvas(QtGui.QImage(image_path))

        tools = QtWidgets.QComboBox()
        tools.addItems(["Arrow", "Circle", "Box", "Freehand"])
        tools.currentTextChanged.connect(lambda name: setattr(self.canvas, "tool", name))
        width = QtWidgets.QSpinBox()
        width.setRange(1, 30)
        width.setValue(self.canvas.pen_width)
        width.valueChanged.connect(lambda v: setattr(self.canvas, "pen_width", v))
        self.color_button = HoverButton("Color")
        self.color_button.clicked.connect(self._pick_color)
        self._update_color_button()
        undo = HoverButton("Undo")
        undo.clicked.connect(self.canvas.undo)
        skip = HoverButton("Insert Without Marks")
        skip.clicked.connect(self.reject)
        done = HoverButton("Insert")
        done.clicked.connect(self._save)

        bar = QtWidgets.QHBoxLayout()
        for widget in (QtWidgets.QLabel("Tool"), tools, QtWidgets.QLabel("Width"), width, self.color_button, undo):
            bar.addWidget(widget)
        bar.addStretch()
        bar.addWidget(skip)
        bar.addWidget(done)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.canvas, 1)
        self.resize(1000, 640)

    def _update_color_button(self):
        self.color_button.setStyleSheet(self.color_button.styleSheet() +
                                        "QPushButton {{ color: {}; }}".format(self.canvas.color.name()))

    def _pick_color(self):
        color = QtWidgets.QColorDialog.getColor(self.canvas.color, self)
        if color.isValid():
            self.canvas.color = color
            self._update_color_button()

    def _save(self):
        if not self.canvas.shapes:
            self.accept()
            return
        image = QtGui.QImage(self.canvas.image).convertToFormat(QtGui.QImage.Format_ARGB32)
        painter = QtGui.QPainter(image)
        _Canvas.draw_shapes(painter, self.canvas.shapes, 1.0)
        painter.end()
        base, _ext = os.path.splitext(self.image_path)
        path = base + "_marked.png"
        if image.save(path, "PNG"):
            self.result_path = path
        self.accept()


def annotate(image_path, parent=None):
    """Open the dialog; return the image path to insert."""
    dialog = AnnotateDialog(image_path, parent)
    qt_exec(dialog)
    return dialog.result_path
