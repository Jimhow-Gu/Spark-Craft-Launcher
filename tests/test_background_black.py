"""Background video black-frame fallback tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from ui.widgets.background_widget import BackgroundWidget


class BackgroundBlackFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _image(self, color):
        image = QImage(64, 36, QImage.Format_RGB32)
        image.fill(QColor(*color))
        return image

    def test_black_image_detection(self):
        self.assertTrue(BackgroundWidget._is_black_image(self._image((0, 0, 0))))
        self.assertTrue(BackgroundWidget._is_black_image(self._image((8, 8, 8))))
        self.assertFalse(BackgroundWidget._is_black_image(self._image((40, 20, 20))))


    def test_black_frame_does_not_replace_fallback(self):
        from PySide6.QtMultimedia import QVideoFrame
        widget = BackgroundWidget()
        widget._video_player = object()
        widget._video_image = None
        frame = QVideoFrame(self._image((0, 0, 0)))
        widget._on_video_frame(frame)
        self.assertIsNone(widget._video_image)
        self.assertTrue(widget._video_black_timer.isActive())
        widget.deleteLater()


    def test_stop_resets_black_state(self):
        widget = BackgroundWidget()
        widget._video_frame_ready = True
        widget._video_black_timer.start()
        widget._stop_video()
        self.assertFalse(widget._video_black_timer.isActive())
        self.assertFalse(widget._video_frame_ready)

    def test_black_timeout_marks_fallback_failed(self):
        widget = BackgroundWidget()
        widget._video_path = 'bad.mp4'
        widget._video_failed_path = ''
        widget._on_video_black_timeout()
        self.assertEqual(widget._video_failed_path, 'bad.mp4')
        widget.deleteLater()

    def test_invalid_media_marks_fallback_failed(self):
        from PySide6.QtMultimedia import QMediaPlayer
        widget = BackgroundWidget()
        widget._video_path = 'bad.mp4'
        widget._video_failed_path = ''
        widget._on_video_media_status(QMediaPlayer.InvalidMedia)
        self.assertEqual(widget._video_failed_path, 'bad.mp4')
        widget.deleteLater()


if __name__ == '__main__':
    unittest.main()
