'''无边框窗口的等比缩放边缘。

QMainWindow 使用 FramelessWindowHint 后没有系统缩放边框，这里在窗口外圈
放一层只覆盖 7px 边缘的透明热区，拖动任意边或角都能按 16:9 等比缩放。
'''

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QWidget


class WindowResizeOverlay(QWidget):
    EDGE = 7
    ASPECT_W = 16
    ASPECT_H = 9
    MIN_WIDTH = 816
    MIN_HEIGHT = 459

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._resizing = False
        self._edges = ()
        self._start_geometry = QRect()
        self._start_mouse = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet('background: transparent;')
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self._update_mask()

    def _update_mask(self):
        rect = self.rect()
        if rect.width() < self.EDGE * 3 or rect.height() < self.EDGE * 3:
            self.clearMask()
            return True
        outer = QRegion(rect)
        inner = QRegion(rect.adjusted(self.EDGE, self.EDGE, -self.EDGE, -self.EDGE))
        self.setMask(outer.subtracted(inner))
        # 某些平台不支持子控件遮罩；此时宁可放弃边缘缩放，也不能挡住整页。
        mask = self.mask()
        if mask.isEmpty() or mask.contains(rect.center()):
            self.hide()
            return False
        return True

    def _edges_at(self, pos):
        left = pos.x() <= self.EDGE
        right = pos.x() >= self.width() - self.EDGE
        top = pos.y() <= self.EDGE
        bottom = pos.y() >= self.height() - self.EDGE
        return (left, right, top, bottom)

    @staticmethod
    def _cursor_for(edges):
        left, right, top, bottom = edges
        horizontal = left or right
        vertical = top or bottom
        if horizontal and vertical:
            if (left and top) or (right and bottom):
                return Qt.SizeFDiagCursor
            return Qt.SizeBDiagCursor
        if horizontal:
            return Qt.SizeHorCursor
        if vertical:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        edges = self._edges_at(event.position().toPoint())
        if not any(edges):
            super().mousePressEvent(event)
            return
        self._resizing = True
        self._edges = edges
        self._start_geometry = QRect(self._window.geometry())
        self._start_mouse = event.globalPosition().toPoint()
        self.setCursor(self._cursor_for(edges))
        event.accept()

    def mouseMoveEvent(self, event):
        if self._resizing and not (event.buttons() & Qt.LeftButton):
            self._resizing = False
            self._edges = ()
            self._start_mouse = None
        if self._resizing and event.buttons() & Qt.LeftButton:
            self._apply_resize(event.globalPosition().toPoint())
            event.accept()
            return
        cursor = self._cursor_for(self._edges_at(event.position().toPoint()))
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing and event.button() == Qt.LeftButton:
            self._resizing = False
            self._edges = ()
            self._start_geometry = QRect()
            self._start_mouse = None
            self.setCursor(self._cursor_for(self._edges_at(event.position().toPoint())))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _screen_limits(self):
        screen = self._window.screen()
        if screen is None:
            return None
        try:
            available = screen.availableGeometry()
        except Exception:
            return None
        if available.width() < self.MIN_WIDTH or available.height() < self.MIN_HEIGHT:
            return None
        return available

    def _apply_resize(self, global_pos):
        if self._start_mouse is None:
            return
        geometry = self._start_geometry
        if geometry.width() <= 0 or geometry.height() <= 0:
            return
        dx = global_pos.x() - self._start_mouse.x()
        dy = global_pos.y() - self._start_mouse.y()
        left, right, top, bottom = self._edges
        horizontal = left or right
        vertical = top or bottom

        # 单边拖动时按该方向缩放，另一边保持中心不动；拖角时取移动更大的
        # 那个方向作为缩放依据，避免 16:9 比例在斜向拖动时来回跳动。
        if horizontal and not vertical:
            scale = (geometry.width() + (dx if right else -dx)) / geometry.width()
        elif vertical and not horizontal:
            scale = (geometry.height() + (dy if bottom else -dy)) / geometry.height()
        else:
            width_scale = (geometry.width() + (dx if right else -dx)) / geometry.width()
            height_scale = (geometry.height() + (dy if bottom else -dy)) / geometry.height()
            if abs(width_scale - 1.0) >= abs(height_scale - 1.0):
                scale = width_scale
            else:
                scale = height_scale

        min_scale = max(
            self.MIN_WIDTH / geometry.width(),
            self.MIN_HEIGHT / geometry.height(),
        )
        scale = max(scale, min_scale)
        available = self._screen_limits()
        if available is not None:
            max_scale = min(
                available.width() / geometry.width(),
                available.height() / geometry.height(),
            )
            scale = min(scale, max_scale)

        width = max(self.MIN_WIDTH, int(round(geometry.width() * scale)))
        height = int(round(width * self.ASPECT_H / self.ASPECT_W))
        if height < self.MIN_HEIGHT:
            height = self.MIN_HEIGHT
            width = int(round(height * self.ASPECT_W / self.ASPECT_H))
        if available is not None:
            width = min(width, available.width())
            height = min(height, available.height())

        x = geometry.x()
        y = geometry.y()
        if horizontal:
            if left:
                x = geometry.x() + geometry.width() - width
        if vertical:
            if top:
                y = geometry.y() + geometry.height() - height

        if available is not None:
            x = max(available.left(), min(x, available.right() - width + 1))
            y = max(available.top(), min(y, available.bottom() - height + 1))
        self._window.setGeometry(x, y, width, height)

    def update_geometry(self):
        self.setGeometry(self._window.rect())
        self.raise_()
        if not self._update_mask():
            return
        if not self.isVisible():
            self.show()
