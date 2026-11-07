'''元旦与农历新年期间标题栏上的烟花粒子。'''

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


@dataclass
class _Spark:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    size: float
    color: QColor
    glow: bool


class TitleFireworksOverlay(QWidget):
    '''只覆盖标题栏的轻量烟花层，始终位于标题文字和按钮下方。'''

    COLORS = ('#FFD166', '#FF7B72', '#7EE7FF', '#FFFFFF', '#FFA94D', '#C7A6FF')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet('background: transparent;')
        self._active = False
        self._enabled = False
        self._paused = False
        self._sparks = []
        self._burst_delay = 0.0
        self._burst_slot = 1
        self._last_time = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_active(self, active):
        active = bool(active)
        if active == self._active:
            if active and not self._paused and self._enabled:
                self._last_time = time.monotonic()
                self._timer.start()
                self.show()
            return
        self._active = active
        if active:
            self._enabled = True
            self._sparks = []
            self._burst_delay = 0.1
            self._burst_slot = 1
            self._last_time = time.monotonic()
            self.show()
            if not self._paused:
                self._timer.start()
        else:
            self._enabled = False
            self._sparks = []
            self._timer.stop()
            self.hide()

    def set_enabled(self, enabled):
        '''关闭后停止产生新烟花，已生成的烟花自然播放完。'''

        enabled = bool(enabled)
        if enabled:
            self._enabled = True
            self._active = True
            self._burst_delay = 0.1
            self._last_time = time.monotonic()
            self.show()
            if not self._paused:
                self._timer.start()
            return
        self._enabled = False
        if not self._sparks:
            self._active = False
            self._timer.stop()
            self.hide()

    def is_active(self):
        return self._active

    def set_paused(self, paused):
        self._paused = bool(paused)
        if self._paused:
            self._timer.stop()
        elif self._active and self.isVisible():
            self._last_time = time.monotonic()
            self._timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.lower()
        if self._active and self._enabled and not self._paused:
            self._last_time = time.monotonic()
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _add_burst(self):
        width = max(1, self.width())
        height = max(1, self.height())
        slots = 5
        slot = self._burst_slot % slots
        self._burst_slot += 1
        center_x = width * ((slot + 0.5) / slots) + random.uniform(-width * 0.04, width * 0.04)
        center_y = random.uniform(height * 0.32, height * 0.72)
        count = random.randint(22, 34)
        base_color = QColor(random.choice(self.COLORS))
        for _ in range(count):
            angle = random.uniform(0, math.tau)
            speed = random.uniform(22.0, 82.0)
            life = random.uniform(1.0, 2.0)
            color = QColor(base_color)
            color.setAlpha(random.randint(150, 245))
            self._sparks.append(
                _Spark(
                    x=center_x,
                    y=center_y,
                    vx=math.cos(angle) * speed,
                    vy=math.sin(angle) * speed - 8.0,
                    life=life,
                    max_life=life,
                    size=random.uniform(1.1, 2.5),
                    color=color,
                    glow=random.random() < 0.35,
                )
            )
        if random.random() < 0.45:
            # 少量白色亮点，模拟烟花中心闪光。
            for _ in range(5):
                angle = random.uniform(0, math.tau)
                self._sparks.append(
                    _Spark(
                        x=center_x, y=center_y,
                        vx=math.cos(angle) * random.uniform(4, 18),
                        vy=math.sin(angle) * random.uniform(4, 18),
                        life=0.4, max_life=0.4,
                        size=1.4, color=QColor(255, 255, 255, 220), glow=True,
                    )
                )

    def _tick(self):
        if not self._active or self._paused:
            return
        now = time.monotonic()
        dt = max(0.0, min(0.12, now - self._last_time))
        self._last_time = now
        width = max(1, self.width())
        height = max(1, self.height())
        alive = []
        for spark in self._sparks:
            spark.life -= dt
            if spark.life <= 0:
                continue
            spark.vy += 14.0 * dt
            spark.vx *= max(0.0, 1.0 - 0.9 * dt)
            spark.x += spark.vx * dt
            spark.y += spark.vy * dt
            if -20 <= spark.x <= width + 20 and -20 <= spark.y <= height + 20:
                alive.append(spark)
        self._sparks = alive
        if self._enabled:
            self._burst_delay -= dt
            if self._burst_delay <= 0:
                self._add_burst()
                self._burst_delay = random.uniform(0.35, 0.9)
        if not self._enabled and not self._sparks:
            self._active = False
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, event):
        if not self._active or not self._sparks:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = max(1, self.width())
        height = max(1, self.height())
        radius = 14.0
        path = QPainterPath()
        path.moveTo(0, radius)
        path.quadTo(0, 0, radius, 0)
        path.lineTo(width - radius, 0)
        path.quadTo(width, 0, width, radius)
        path.lineTo(width, height)
        path.lineTo(0, height)
        path.closeSubpath()
        painter.setClipPath(path)
        painter.setPen(Qt.NoPen)
        for spark in self._sparks:
            ratio = max(0.0, min(1.0, spark.life / spark.max_life))
            alpha = int(255 * ratio)
            color = QColor(spark.color)
            color.setAlpha(min(255, alpha))
            tail_x = spark.x - spark.vx * 0.028
            tail_y = spark.y - spark.vy * 0.028 + 0.6
            pen = QPen(color, max(0.7, spark.size * 0.8), Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                int(round(tail_x)), int(round(tail_y)),
                int(round(spark.x)), int(round(spark.y)),
            )
            if spark.glow:
                glow = QColor(color)
                glow.setAlpha(max(0, alpha // 4))
                painter.setPen(Qt.NoPen)
                painter.setBrush(glow)
                painter.drawEllipse(
                    QRectF(spark.x - spark.size * 2.2, spark.y - spark.size * 2.2,
                           spark.size * 4.4, spark.size * 4.4)
                )
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(
                QRectF(spark.x - spark.size, spark.y - spark.size,
                       spark.size * 2, spark.size * 2)
            )
        painter.end()
