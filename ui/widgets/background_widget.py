'''启动器背景绘制控件'''

import math
from pathlib import Path
from PySide6.QtCore import (QRect, QRectF, Qt, QUrl, Signal)
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap)
from PySide6.QtWidgets import (QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget)
from core.background import (VIDEO_EXTENSIONS, current_image, get_settings)
from ui.design import WINDOW_DARK, WINDOW_LIGHT

class BackgroundWidget(QWidget):
    video_state_changed = Signal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._source_path = ''
        self._blurred = None
        self._blur_radius = -1
        self._scaled = None
        self._scaled_key = None
        self._video_player = None
        self._video_sink = None
        self._video_audio = None
        self._video_image = None
        self._video_path = ''
        self._video_volume = 0.7
        self._video_source_cache = None
        self._video_source_key = None
        self._video_auto_cache = None
        self._video_auto_key = None
        self._freeze_updates = False
        self._light = False
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def freeze_video_updates(self, freeze: bool):
        '''侧栏动画期间冻结视频帧更新，避免每帧重绘 1080p 视频拖慢动画。'''

        freeze = bool(freeze)
        if freeze == self._freeze_updates:
            return
        self._freeze_updates = freeze
        if not freeze:
            self.update()

    def set_light(self, light: bool):
        self._light = bool(light)
        self.update()

    def reload(self, force: bool=False):
        settings = get_settings()
        path = current_image() if settings['enabled'] else None
        is_video = bool(path and Path(path).is_file() and Path(path).suffix.lower() in VIDEO_EXTENSIONS)
        if is_video:
            self._source = None
            self._source_path = ''
            self._blurred = None
            self._blur_radius = -1
            self._scaled = None
            self._scaled_key = None
            if force or str(path) != self._video_path or self._video_player is None:
                self._start_video(path)
            else:
                self._video_player.play()
            self.update()
            return
        self._stop_video()
        new_path = str(path) if path and Path(path).is_file() else ''
        if force or new_path != self._source_path:
            self._source = None
            self._source_path = ''
            self._blurred = None
            self._blur_radius = -1
            self._scaled = None
            self._scaled_key = None
            if new_path:
                pix = QPixmap(new_path)
                if not pix.isNull():
                    self._source = pix
                    self._source_path = new_path
        self.update()

    def video_available(self) -> bool:
        return self._video_player is not None

    def video_playing(self) -> bool:
        player = self._video_player
        if player is None:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            return player.playbackState() == QMediaPlayer.PlayingState
        except Exception:
            return False

    def set_video_audio_enabled(self, enabled: bool):
        audio = self._video_audio
        if audio is None:
            return
        try:
            audio.setMuted(not enabled)
            if enabled:
                audio.setVolume(self._video_volume)
        except RuntimeError:
            pass

    def set_video_volume(self, percent):
        '''设置背景视频音量（0-100）。'''

        try:
            value = max(0.0, min(1.0, float(percent) / 100.0))
        except (TypeError, ValueError):
            return
        self._video_volume = value
        audio = self._video_audio
        if audio is None:
            return
        try:
            from core.config import ConfigManager
            enabled = bool(ConfigManager().get('background_video_audio', False))
            audio.setVolume(value)
            audio.setMuted(not enabled)
        except RuntimeError:
            pass
        except Exception:
            pass

    def video_audio_enabled(self) -> bool:
        audio = self._video_audio
        if audio is None:
            return False
        try:
            return not audio.isMuted()
        except RuntimeError:
            return False

    def toggle_video(self) -> bool:
        '''切换背景视频播放/暂停，返回切换后是否正在播放。'''

        player = self._video_player
        if player is None:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            if player.playbackState() == QMediaPlayer.PlayingState:
                player.pause()
                self.video_state_changed.emit(False)
                return False
            player.play()
            self.video_state_changed.emit(True)
            return True
        except Exception:
            return False

    def pause_video(self):
        player = self._video_player
        if player is None:
            return
        try:
            player.pause()
            self.video_state_changed.emit(False)
        except RuntimeError:
            pass

    def play_video(self):
        player = self._video_player
        if player is None:
            return
        try:
            player.play()
            self.video_state_changed.emit(True)
        except RuntimeError:
            pass

    def _start_video(self, path):
        self._stop_video()
        try:
            from PySide6.QtMultimedia import (QAudioOutput, QMediaPlayer, QVideoSink)
            player = QMediaPlayer(self)
            sink = QVideoSink(self)
            audio = QAudioOutput(self)
            try:
                from core.config import ConfigManager
                audio_enabled = bool(
                    ConfigManager().get('background_video_audio', False)
                )
            except Exception:
                audio_enabled = False
            audio.setMuted(not audio_enabled)
            audio.setVolume(self._video_volume)
            player.setAudioOutput(audio)
            player.setVideoOutput(sink)
            sink.videoFrameChanged.connect(self._on_video_frame)
            player.playbackStateChanged.connect(self._on_video_playback_state)
            player.setSource(QUrl.fromLocalFile(str(path)))
            player.setLoops(QMediaPlayer.Infinite)
            self._video_player = player
            self._video_sink = sink
            self._video_audio = audio
            self._video_path = str(path)
            player.play()
        except Exception:
            self._stop_video()

    def _on_video_playback_state(self, state):
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            self.video_state_changed.emit(state == QMediaPlayer.PlayingState)
        except Exception:
            pass

    def _stop_video(self):
        player = self._video_player
        sink = self._video_sink
        self._video_player = None
        self._video_sink = None
        self._video_audio = None
        self._video_image = None
        self._video_path = ''
        self._invalidate_video_cache()
        if sink is not None:
            try:
                sink.videoFrameChanged.disconnect(self._on_video_frame)
            except (RuntimeError, TypeError):
                pass
        if player is not None:
            try:
                player.stop()
                player.setSource(QUrl())
                player.deleteLater()
            except RuntimeError:
                pass
        self.video_state_changed.emit(False)

    def _invalidate_video_cache(self):
        self._video_source_cache = None
        self._video_source_key = None
        self._video_auto_cache = None
        self._video_auto_key = None

    def _on_video_frame(self, frame):
        if self._video_player is None or self._freeze_updates:
            return
        try:
            image = frame.toImage()
            if not image.isNull():
                self._video_image = image
                self._invalidate_video_cache()
                self.update()
            return
        except Exception:
            pass

    def showEvent(self, event):
        if self._video_player is not None:
            try:
                self._video_player.play()
                self.video_state_changed.emit(True)
            except RuntimeError:
                pass
        super().showEvent(event)

    def hideEvent(self, event):
        if self._video_player is not None:
            try:
                self._video_player.pause()
                self.video_state_changed.emit(False)
            except RuntimeError:
                pass
        super().hideEvent(event)

    def resizeEvent(self, event):
        self._scaled = None
        self._scaled_key = None
        self._video_auto_cache = None
        self._video_auto_key = None
        super().resizeEvent(event)

    def _video_source_pixmap(self, blur: int) -> QPixmap:
        if self._video_image is None:
            return QPixmap()
        key = (self._video_image.cacheKey(), int(blur))
        if self._video_source_cache is not None and self._video_source_key == key:
            return self._video_source_cache
        source = QPixmap.fromImage(self._video_image)
        source = self._blur_video_frame(source, blur)
        self._video_source_cache = source
        self._video_source_key = key
        return source

    def _video_auto_pixmap(self, source: QPixmap, size) -> QPixmap:
        key = (source.cacheKey(), int(size.width()), int(size.height()))
        if self._video_auto_cache is not None and self._video_auto_key == key:
            return self._video_auto_cache
        scaled = source.scaled(
            size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        self._video_auto_cache = scaled
        self._video_auto_key = key
        return scaled

    def _scaled_source(self) -> QPixmap:
        if self._source is None:
            return QPixmap()
        key = self.width(), self.height()
        if self._scaled is not None and self._scaled_key == key:
            return self._scaled
        max_w = max(self.width(), 640)
        max_h = max(self.height(), 480)
        if self._source.width() > max_w * 2 or self._source.height() > max_h * 2:
            self._scaled = self._source.scaled(max_w * 2, max_h * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            self._scaled = self._source
        self._scaled_key = key
        return self._scaled

    def _blur_pixmap(self, pix: QPixmap, radius: int) -> QPixmap:
        if radius <= 0 or pix.isNull():
            return pix
        if self._blurred is not None and self._blur_radius == radius:
            return self._blurred
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pix)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        out = QImage(pix.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        scene.render(painter, QRectF(out.rect()), QRectF(pix.rect()))
        painter.end()
        self._blurred = QPixmap.fromImage(out)
        self._blur_radius = radius
        return self._blurred

    def _blur_video_frame(self, pix: QPixmap, radius: int) -> QPixmap:
        '''视频逐帧模糊：降采样再放大，避免 QGraphicsBlurEffect 拖慢播放。'''

        if radius <= 0 or pix.isNull():
            return pix
        factor = max(0.06, 1.0 / (1.0 + radius * 0.35))
        small_w = max(32, int(pix.width() * factor))
        small_h = max(18, int(pix.height() * factor))
        small = pix.scaled(small_w, small_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return small.scaled(pix.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(rect), 14, 14)
        painter.setClipPath(clip_path)
        painter.fillRect(rect, QColor(WINDOW_LIGHT if self._light else WINDOW_DARK))
        settings = get_settings()
        video_source = self._video_image is not None
        if video_source:
            source = self._video_source_pixmap(settings['blur'])
        elif self._source is not None:
            source = self._scaled_source()
            source = self._blur_pixmap(source, settings['blur'])
        else:
            painter.end()
            return
        opacity = max(0, min(100, settings['opacity'])) / 100.0
        painter.setOpacity(opacity)
        mode = settings['fit']
        if mode == 'auto':
            if video_source:
                scaled = self._video_auto_pixmap(source, rect.size())
            else:
                scaled = source.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            sx = max(0, (scaled.width() - rect.width()) // 2)
            sy = max(0, (scaled.height() - rect.height()) // 2)
            painter.drawPixmap(rect.topLeft(), scaled, QRect(sx, sy, rect.width(), rect.height()))
        else:
            if mode == 'tile':
                for x in range(0, rect.width(), max(1, source.width())):
                    for y in range(0, rect.height(), max(1, source.height())):
                        painter.drawPixmap(x, y, source)
            if mode == 'center':
                x = (rect.width() - source.width()) // 2
                y = (rect.height() - source.height()) // 2
                painter.drawPixmap(x, y, source)
            elif mode == 'stretch':
                painter.drawPixmap(rect, source)
            else:
                scaled = source.scaled(rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                positions = {'top_left': (0, 0), 'top_center': ((rect.width() - scaled.width()) // 2, 0), 'top_right': (rect.width() - scaled.width(), 0), 'middle_left': (0, (rect.height() - scaled.height()) // 2), 'middle_center': ((rect.width() - scaled.width()) // 2, (rect.height() - scaled.height()) // 2), 'middle_right': (rect.width() - scaled.width(), (rect.height() - scaled.height()) // 2), 'bottom_left': (0, rect.height() - scaled.height()), 'bottom_center': ((rect.width() - scaled.width()) // 2, rect.height() - scaled.height()), 'bottom_right': (rect.width() - scaled.width(), rect.height() - scaled.height())}
                x, y = positions.get(mode, positions['middle_center'])
                painter.drawPixmap(x, y, scaled)
        painter.setOpacity(1.0)
        if self._light:
            painter.fillRect(rect, QColor(248, 251, 251, 50))
            if settings['overlay']:
                gradient = QLinearGradient(0, 0, 0, rect.height())
                gradient.setColorAt(0, QColor(248, 251, 251, 28))
                gradient.setColorAt(1, QColor(230, 243, 243, 92))
                painter.fillRect(rect, gradient)
        else:
            painter.fillRect(rect, QColor(18, 23, 23, 92))
            if settings['overlay']:
                gradient = QLinearGradient(0, 0, 0, rect.height())
                gradient.setColorAt(0, QColor(18, 23, 23, 108))
                gradient.setColorAt(1, QColor(15, 76, 76, 150))
                painter.fillRect(rect, gradient)
        painter.end()


