"""RoundedSelect dropdown behavior tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

from ui.widgets.rounded_select import RoundedSelect


class RoundedSelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._widgets = []

    def tearDown(self):
        for widget in reversed(self._widgets):
            try:
                widget.hidePopup()
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self.app.processEvents()

    def _make(self):
        select = RoundedSelect()
        self._widgets.append(select)
        select.addItem('全部', 'all')
        select.addItem('正式版', 'release')
        select.addItem('快照版', 'snapshot')
        select.move(100, 100)
        select.resize(180, 34)
        select.show()
        self.app.processEvents()
        return select

    def test_opens_on_release_and_reopens_same_position(self):
        select = self._make()
        QTest.mousePress(select, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(select.isPopupVisible())
        QTest.mouseRelease(select, Qt.LeftButton)
        self.app.processEvents()
        QTest.qWait(50)
        self.assertTrue(select.isPopupVisible())
        anchor = select.mapToGlobal(QPoint(0, select.height()))
        self.assertLessEqual(abs(select._popup.x() - anchor.x()), 4)
        self.assertLessEqual(abs(select._popup.y() - anchor.y()), 8)
        QTest.qWait(220)
        position = select._popup.pos()
        select.hidePopup()
        self.app.processEvents()
        select.showPopup()
        self.app.processEvents()
        QTest.qWait(220)
        self.assertEqual(select._popup.pos(), position)
        select.hidePopup()
        select.close()
        select.deleteLater()

    def test_hover_only_tracks_pointer_and_selection(self):
        select = self._make()
        changed = []
        select.currentIndexChanged.connect(changed.append)
        select.showPopup()
        self.app.processEvents()
        QTest.qWait(180)
        popup = select._popup
        view = popup.list
        QTest.mouseMove(view.viewport(), QPoint(2, 2))
        self.app.processEvents()
        QTest.qWait(20)
        for row in range(3):
            rect = view.visualRect(view.model().index(row, 0))
            local = QPointF(rect.center())
            global_pos = QPointF(view.viewport().mapToGlobal(rect.center()))
            QApplication.sendEvent(
                view.viewport(),
                QMouseEvent(
                    QEvent.MouseMove, local, global_pos,
                    Qt.NoButton, Qt.NoButton, Qt.NoModifier,
                ),
            )
            self.assertEqual(view._hover_index, row)
        rect = view.visualRect(view.model().index(1, 0))
        QTest.mouseClick(
            view.viewport(), Qt.LeftButton, pos=rect.center()
        )
        self.app.processEvents()
        self.assertEqual(select.currentData(), 'release')
        self.assertEqual(select.currentText(), '正式版')
        self.assertEqual(changed, [1])
        self.assertFalse(select.isPopupVisible())
        select.close()
        select.deleteLater()

    def test_popup_is_opaque_and_animates_arrow_and_position(self):
        select = self._make()
        select.showPopup()
        self.app.processEvents()
        self.assertTrue(select.isPopupVisible())
        QTest.qWait(220)
        self.assertAlmostEqual(select._arrow_angle, 180.0, places=1)
        final_pos = select._popup.pos()
        image = select._popup.grab().toImage()
        center = image.pixelColor(
            image.width() // 2, image.height() // 2
        )
        self.assertEqual(center.alpha(), 255)
        select.hidePopup()
        QTest.qWait(220)
        self.assertAlmostEqual(select._arrow_angle, 0.0, places=1)
        select.showPopup()
        QTest.qWait(220)
        self.assertEqual(select._popup.pos(), final_pos)
        select.hidePopup()
        select.close()
        select.deleteLater()

    def test_button_stays_opaque_inside_translucent_filter_frame(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        frame = QFrame()
        frame.setStyleSheet(
            'QFrame { background: rgba(30,41,41,0.84);'
            ' border: 1px solid rgba(255,255,255,0.18);'
            ' border-radius: 8px; }'
        )
        frame_layout = QVBoxLayout(frame)
        select = RoundedSelect(frame)
        select.addItems(['全部', '正式版'])
        select.resize(168, 34)
        frame_layout.addWidget(select)
        layout.addWidget(frame)
        host.show()
        self.app.processEvents()
        image = select.grab().toImage()
        center = image.pixelColor(
            image.width() // 2, image.height() // 2
        )
        self.assertEqual(center.alpha(), 255)
        host.close()
        host.deleteLater()

    def test_click_button_closes_open_popup(self):
        select = self._make()
        QTest.mouseClick(select, Qt.LeftButton)
        self.app.processEvents()
        QTest.qWait(50)
        self.assertTrue(select.isPopupVisible())
        QTest.qWait(350)
        QTest.mouseClick(select, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(select.isPopupVisible())
        select.close()
        select.deleteLater()

    def test_rapid_second_click_does_not_close_new_popup(self):
        select = self._make()
        QTest.mouseClick(select, Qt.LeftButton)
        self.app.processEvents()
        QTest.qWait(50)
        self.assertTrue(select.isPopupVisible())
        QTest.mouseClick(select, Qt.LeftButton)
        self.app.processEvents()
        QTest.qWait(50)
        self.assertTrue(select.isPopupVisible())
        QTest.qWait(350)
        QTest.mouseClick(select, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(select.isPopupVisible())
        select.close()
        select.deleteLater()


if __name__ == '__main__':
    unittest.main()
