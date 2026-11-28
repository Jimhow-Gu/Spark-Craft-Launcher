"""Background video black-frame fallback tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import core.background as background
import ui.widgets.background_widget as background_widget
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

    def test_failed_video_is_persisted_and_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / 'background'
            media.mkdir()
            bad = media / 'Si.mp4'
            bad.write_bytes(b'bad')
            good = media / 'good.png'
            good.write_bytes(b'png')
            with patch.object(background, 'scl_dir', return_value=root):
                self.assertEqual(
                    [path.name for path in background.list_background_images()],
                    ['good.png', 'Si.mp4'],
                )
                background.mark_background_video_failed(
                    bad, '持续黑帧'
                )
                self.assertTrue(background.is_background_video_failed(bad))
                self.assertEqual(
                    [path.name for path in background.list_background_images()],
                    ['good.png'],
                )
                # 文件被替换后必须允许重新尝试，不能永久拉黑同名路径。
                bad.write_bytes(b'new-video')
                stat = bad.stat()
                os.utime(
                    bad,
                    ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
                )
                self.assertFalse(background.is_background_video_failed(bad))
                self.assertEqual(
                    [path.name for path in background.list_background_images()],
                    ['good.png', 'Si.mp4'],
                )

    def test_black_timeout_persists_failure_and_schedules_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'Si.mp4'
            bad.write_bytes(b'bad')
            widget = BackgroundWidget()
            widget._video_path = str(bad)
            with patch.object(
                background_widget, 'mark_background_video_failed'
            ) as mark, patch.object(
                widget, '_schedule_next_background'
            ) as schedule:
                widget._on_video_black_timeout()
            mark.assert_called_once()
            self.assertEqual(mark.call_args.args[0], str(bad))
            schedule.assert_called_once()
            self.assertEqual(widget._video_failed_path, str(bad))
            widget.deleteLater()

    def test_only_wmf_video_containers_are_listed(self):
        self.assertIn('.mp4', background.VIDEO_EXTENSIONS)
        self.assertIn('.m4v', background.VIDEO_EXTENSIONS)
        self.assertIn('.mov', background.VIDEO_EXTENSIONS)
        self.assertNotIn('.mkv', background.VIDEO_EXTENSIONS)
        self.assertNotIn('.webm', background.VIDEO_EXTENSIONS)


if __name__ == '__main__':
    unittest.main()
