'''立冬与圣诞节期间的雪花粒子及冬季界面装饰。'''

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QWidget


@dataclass
class _Flake:
    x: float
    y: float
    radius: float
    speed: float
    sway: float
    phase: float
    alpha: int
    spin: float
    crystal: bool


class SnowOverlay(QWidget):
    '''透明雪花层；覆盖在界面上方，但不拦截鼠标事件。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet('background: transparent;')
        self._active = False
        self._enabled = False
        self._paused = False
        self._flakes = []
        self._last_time = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_active(self, active):
        active = bool(active)
        if active == self._active:
            if active and not self._paused:
                self._ensure_flakes()
                self._timer.start()
                self.show()
                self.raise_()
            return
        self._active = active
        if active:
            self._enabled = True
            self._ensure_flakes()
            self._last_time = time.monotonic()
            self.show()
            self.raise_()
            if not self._paused:
                self._timer.start()
        else:
            self._enabled = False
            self._flakes = []
            self._timer.stop()
            self.hide()

    def set_enabled(self, enabled):
        '''关闭时停止生成新雪花，让现有雪花自然落完。'''

        enabled = bool(enabled)
        if enabled:
            self._enabled = True
            self._active = True
            # 重新开启时补回完整密度；关闭期间只让旧雪花自然落完。
            self._build_flakes()
            self._last_time = time.monotonic()
            self.show()
            self.raise_()
            if not self._paused:
                self._timer.start()
            return
        self._enabled = False
        # 关闭瞬间移除还在画面上方的“待入场”雪花，只保留已经可见的粒子。
        # 这样不会再从顶部出现新雪花，视觉上也不会一直像在下新雪。
        self._flakes = [flake for flake in self._flakes if flake.y >= 0.0]
        if not self._flakes:
            self._active = False
            self._timer.stop()
            self.hide()

    def set_paused(self, paused):
        self._paused = bool(paused)
        if self._paused:
            self._timer.stop()
        elif self._active and self.isVisible():
            self._last_time = time.monotonic()
            self._timer.start()

    def is_active(self):
        return self._active

    def showEvent(self, event):
        super().showEvent(event)
        if self._active and not self._paused:
            self._ensure_flakes()
            self._last_time = time.monotonic()
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._active and self._enabled:
            self._build_flakes()

    def _ensure_flakes(self):
        if not self._flakes or self.width() <= 0 or self.height() <= 0:
            self._build_flakes()

    def _build_flakes(self):
        width = max(1, self.width())
        height = max(1, self.height())
        count = max(70, min(180, int(width * height / 7000)))
        flakes = []
        for _ in range(count):
            crystal = random.random() < 0.12
            # 六角雪花保证有可见的自转；普通圆点不转也看不出来。
            spin = (
                random.choice((-1.0, 1.0)) * random.uniform(0.35, 0.75)
                if crystal else random.uniform(-0.8, 0.8)
            )
            flakes.append(
                _Flake(
                    x=random.uniform(0, width),
                    y=random.uniform(-height, height),
                    radius=random.uniform(0.8, 2.8),
                    speed=random.uniform(16.0, 46.0),
                    sway=random.uniform(3.0, 16.0),
                    phase=random.uniform(0, math.tau),
                    alpha=random.randint(90, 205),
                    spin=spin,
                    crystal=crystal,
                )
            )
        self._flakes = flakes

    def _tick(self):
        if not self._active or self._paused:
            return
        now = time.monotonic()
        dt = max(0.0, min(0.12, now - self._last_time))
        self._last_time = now
        width = max(1, self.width())
        height = max(1, self.height())
        alive = []
        for flake in self._flakes:
            flake.y += flake.speed * dt
            flake.phase += flake.spin * dt
            if flake.y > height + 12:
                if not self._enabled:
                    continue
                flake.y = random.uniform(-60, -8)
                flake.x = random.uniform(0, width)
                flake.radius = random.uniform(0.8, 2.8)
                flake.speed = random.uniform(16.0, 46.0)
                flake.alpha = random.randint(90, 205)
            alive.append(flake)
        self._flakes = alive
        if not self._enabled and not self._flakes:
            self._active = False
            self._timer.stop()
            self.hide()
            return
        self.update()

    def _is_light(self):
        try:
            return self.palette().color(QPalette.Window).lightness() > 128
        except Exception:
            return False

    def paintEvent(self, event):
        if not self._active or not self._flakes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        light = self._is_light()
        width = self.width()
        height = self.height()

        # 顶部和底部各有一层很淡的霜色，作为冬季界面的细节。
        frost = QColor(100, 155, 190, 20) if light else QColor(215, 242, 255, 15)
        top = QLinearGradient(0, 0, 0, 92)
        top.setColorAt(0.0, frost)
        top.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(QRectF(0, 0, width, 92), top)
        bottom = QLinearGradient(0, height - 76, 0, height)
        bottom.setColorAt(0.0, QColor(255, 255, 255, 0))
        bottom.setColorAt(1.0, QColor(frost.red(), frost.green(), frost.blue(), 24 if light else 12))
        painter.fillRect(QRectF(0, height - 76, width, 76), bottom)

        for flake in self._flakes:
            x = flake.x + math.sin(flake.phase) * flake.sway
            y = flake.y
            alpha = flake.alpha
            if light:
                core = QColor(255, 255, 255, min(255, alpha + 25))
                shadow = QColor(55, 92, 120, 60)
            else:
                core = QColor(255, 255, 255, alpha)
                shadow = QColor(140, 195, 225, 45)

            if flake.crystal:
                self._draw_crystal(painter, x, y, flake.radius, core, shadow, flake.phase)
            else:
                painter.setPen(Qt.NoPen)
                painter.setBrush(shadow)
                painter.drawEllipse(
                    QRectF(x - flake.radius - 0.8, y - flake.radius - 0.8,
                           (flake.radius + 0.8) * 2, (flake.radius + 0.8) * 2)
                )
                painter.setBrush(core)
                painter.drawEllipse(
                    QRectF(x - flake.radius, y - flake.radius,
                           flake.radius * 2, flake.radius * 2)
                )
        painter.end()

    @staticmethod
    def _draw_crystal(painter, x, y, radius, core, shadow, rotation):
        length = max(2.2, radius * 2.6)
        for color, offset in ((shadow, 0.8), (core, 0.0)):
            pen = QPen(color, max(0.7, radius * 0.42), Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            painter.save()
            painter.translate(x + offset, y + offset)
            # 六角雪花只做缓慢自转，避免部分粒子看起来像在快速打转。
            painter.rotate(math.degrees(rotation) * 0.65)
            for index in range(3):
                angle = math.radians(index * 60.0)
                dx = math.cos(angle) * length
                dy = math.sin(angle) * length
                painter.drawLine(
                    int(round(-dx)), int(round(-dy)),
                    int(round(dx)), int(round(dy)),
                )
            painter.restore()
