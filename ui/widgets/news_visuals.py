'''新闻页共用的图片和加载占位控件。'''

from __future__ import annotations

from PySide6.QtCore import (
    Property, QBuffer, QByteArray, QPointF, QRectF, QTimer, Qt,
    QEasingCurve, QPropertyAnimation,
)
from PySide6.QtGui import (
    QColor, QImageReader, QLinearGradient, QPainter, QPalette, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ui.design import IMAGE_PLACEHOLDER_DARK, IMAGE_PLACEHOLDER_LIGHT
from ui.widgets.fireworks_loader import FireworksLoader

MAX_IMAGE_PIXELS = 40_000_000


def pixmap_from_data(data, max_pixels=MAX_IMAGE_PIXELS):
    '''限制解码后的图片像素数，避免异常图片耗尽内存。'''

    if not data:
        return QPixmap()
    buffer = QBuffer()
    try:
        buffer.setData(QByteArray(data))
        if not buffer.open(QBuffer.ReadOnly):
            return QPixmap()
        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid() and size.width() * size.height() > max_pixels:
            return QPixmap()
        image = reader.read()
        if image.isNull():
            return QPixmap()
        return QPixmap.fromImage(image)
    except Exception:
        return QPixmap()
    finally:
        try:
            buffer.close()
        except Exception:
            pass


class NewsImageLabel(QWidget):
    '''按完整宽度铺满新闻图；加载时显示主题占位背景、动画和文字。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._light = self.palette().color(QPalette.Window).lightness() > 128
        self.setFixedHeight(180)
        self.setMinimumWidth(240)
        self.setProperty('_scl_scalable', True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._placeholder = QWidget(self)
        self._placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._placeholder.setStyleSheet('background: transparent;')
        layout = QHBoxLayout(self._placeholder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addStretch(1)
        self._loading_icon = FireworksLoader(56)
        self._loading_text = QLabel('正在加载新闻图片')
        self._loading_text.setProperty('_theme_ignore', True)
        self._loading_text.setStyleSheet('color:#e3fcfc; font-size:14px; font-weight:600;')
        layout.addWidget(self._loading_icon, 0, Qt.AlignVCenter)
        layout.addWidget(self._loading_text, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        self.set_light(self._light)

    def _placeholder_color(self):
        return IMAGE_PLACEHOLDER_LIGHT if self._light else IMAGE_PLACEHOLDER_DARK

    def set_light(self, light):
        light = bool(light)
        self._light = light
        self._loading_text.setStyleSheet(
            f'color:{"#1b1b1b" if light else "#e3fcfc"}; '
            'font-size:14px; font-weight:600;'
        )
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._placeholder.setGeometry(self.rect())

    def set_source(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._source = pixmap
        self._placeholder.hide()
        self.update()

    def setText(self, text):
        self._source = None
        self._placeholder.show()
        show_icon = text in ('', '正在加载新闻图片', '正在加载新闻图片...')
        self._loading_icon.setVisible(show_icon)
        self._loading_text.setText('正在加载新闻图片' if show_icon else str(text))
        self.update()

    def stop(self):
        self._loading_icon.stop()

    def set_loading_text(self, text):
        '''保持烟花动画，只替换加载提示文字。'''

        self._source = None
        self._placeholder.show()
        self._loading_icon.show()
        self._loading_text.setText(str(text))
        self.update()

    def paintEvent(self, event):
        if self._source is None or self._source.isNull():
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(self._placeholder_color()))
            painter.end()
            return
        painter = QPainter(self)
        target = self.rect()
        source = self._source
        if source.width() <= 0 or source.height() <= 0:
            painter.end()
            return
        scale = max(target.width() / source.width(), target.height() / source.height())
        crop_w = max(1, int(target.width() / scale))
        crop_h = max(1, int(target.height() / scale))
        crop_x = max(0, (source.width() - crop_w) // 2)
        crop_y = max(0, (source.height() - crop_h) // 2)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(
            target,
            source,
            source.rect().adjusted(
                crop_x,
                crop_y,
                -(source.width() - crop_x - crop_w),
                -(source.height() - crop_y - crop_h),
            ),
        )
        painter.end()


class TranslationPending(QWidget):
    '''新闻正文尚未翻译时显示清晰的加载动画。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background: transparent;')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 20)
        layout.setSpacing(7)
        layout.addWidget(FireworksLoader(72), 0, Qt.AlignHCenter)
        self.label = QLabel('翻译施工中')
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setProperty('_theme_ignore', True)
        layout.addWidget(self.label)
        self._light = self.palette().color(QPalette.Window).lightness() > 128
        self.set_light(self._light)

    def set_light(self, light):
        self._light = bool(light)
        self.label.setStyleSheet(
            f'color:{"#1b1b1b" if self._light else "#e3fcfc"};'
            ' font-size:15px; font-weight:700;'
        )


class BackToTopButton(QAbstractButton):
    '''新闻页右下角的回到顶部按钮，显示和隐藏都使用缩放动画。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip('回到顶部')
        self.setAccessibleName('回到顶部')
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._reveal = 0.0
        self._interactive = False
        self._hover = False
        self._light = self.palette().color(QPalette.Window).lightness() > 128
        self._anim = QPropertyAnimation(self, b'reveal', self)
        self._anim.finished.connect(self._on_anim_finished)
        self.hide()

    def get_reveal(self):
        return self._reveal

    def set_reveal(self, value):
        self._reveal = max(0.0, min(1.0, float(value)))
        self.update()

    reveal = Property(float, get_reveal, set_reveal)

    def set_light(self, light):
        light = bool(light)
        if light == self._light:
            return
        self._light = light
        self.update()

    def show_animated(self):
        self._anim.stop()
        self._interactive = True
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.show()
        self.raise_()
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.setStartValue(float(self._reveal))
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_animated(self):
        if not self.isVisible() and self._reveal <= 0.001:
            return
        self._interactive = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._anim.stop()
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.setStartValue(float(self._reveal))
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_finished(self):
        if not self._interactive and self._reveal <= 0.001:
            self.hide()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._reveal <= 0.001:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        side = max(1.0, min(self.width(), self.height()) - 2.0)
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.scale(self._reveal, self._reveal)
        if self._light:
            if self.isDown():
                top_color, bottom_color = '#3A8F8F', '#276F6F'
            elif self._hover or self.underMouse():
                top_color, bottom_color = '#5DBDBD', '#3F9595'
            else:
                top_color, bottom_color = '#4FAFAF', '#338282'
            arrow_color = QColor(248, 251, 251, 255)
            rim_color = QColor(255, 255, 255, 90)
        else:
            if self.isDown():
                top_color, bottom_color = '#79D0D0', '#57ACAC'
            elif self._hover or self.underMouse():
                top_color, bottom_color = '#B4F3F3', '#8BDCDC'
            else:
                top_color, bottom_color = '#9DE7E7', '#72CACA'
            arrow_color = QColor(18, 40, 40, 255)
            rim_color = QColor(255, 255, 255, 55)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 45))
        painter.drawEllipse(QRectF(-side / 2.0, -side / 2.0 + 1.5, side, side))
        circle = QRectF(-side / 2.0, -side / 2.0, side, side)
        gradient = QLinearGradient(circle.topLeft(), circle.bottomLeft())
        gradient.setColorAt(0.0, QColor(top_color))
        gradient.setColorAt(1.0, QColor(bottom_color))
        painter.setBrush(gradient)
        painter.drawEllipse(circle)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(rim_color, 1.0))
        painter.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))
        pen = QPen(
            arrow_color,
            max(2.0, side * 0.075),
            Qt.SolidLine,
            Qt.RoundCap,
            Qt.RoundJoin,
        )
        painter.setPen(pen)
        painter.drawLine(
            QPointF(-side * 0.20, -side * 0.20),
            QPointF(side * 0.20, -side * 0.20),
        )
        painter.drawLine(
            QPointF(0.0, -side * 0.09),
            QPointF(0.0, side * 0.24),
        )
        painter.drawLine(
            QPointF(0.0, -side * 0.09),
            QPointF(-side * 0.17, side * 0.07),
        )
        painter.drawLine(
            QPointF(0.0, -side * 0.09),
            QPointF(side * 0.17, side * 0.07),
        )
        painter.end()


class NewsContentBrowser(QTextBrowser):
    '''自适应高度的新闻正文，不使用内部滚动条。'''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            'QTextBrowser { background:transparent; border:none; '
            'padding:0; color:#f0f1f3; }'
        )
        self._last_width = -1

    def set_html(self, html_text, width_hint=0):
        self.setHtml(str(html_text or ''))
        self._fit_height(width_hint)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self, width_hint=0):
        try:
            width = int(self.viewport().width())
            if width < 80:
                width = int(width_hint) if width_hint else 0
            if width < 80:
                width = max(80, self.width())
            document = self.document()
            document.setTextWidth(width)
            height = max(1, int(document.size().height()) + 2)
            if abs(height - self.height()) > 1:
                self.setFixedHeight(height)
            self._last_width = width
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = int(self.viewport().width())
        if width != self._last_width:
            self._last_width = width
            QTimer.singleShot(0, self._fit_height)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height)
