'''关于页面（含特别鸣谢卡片）。'''

from __future__ import annotations

import base64
import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.app_font import ui_font
from core.paths import scl_dir
from core.update import VERSION_DISPLAY
from ui.widgets.settings_widgets import hidpi_pixmap, rounded_pixmap, trim_transparent

_IMAGES_DIR = Path(__file__).resolve().parent.parent / 'imgs'
_VERSION_TEXT = VERSION_DISPLAY
def _card_style(object_name):
    return (
        f'QWidget#{object_name} {{ background: rgba(18,20,26,0.58);'
        ' border: 1px solid rgba(255,255,255,0.22); border-radius: 12px; }'
        f'QWidget#{object_name} QLabel {{ background: transparent; border: none; }}'
    )



def _page_header(title):
    header = QWidget()
    layout = QHBoxLayout(header)
    layout.setContentsMargins(40, 24, 40, 8)
    label = QLabel(str(title))
    label.setFont(ui_font(24, 700))
    label.setStyleSheet('color:#f0f0f0;')
    layout.addWidget(label)
    layout.addStretch(1)
    return header


def _icon_label(size=44):
    label = QLabel()
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet('background: transparent;')
    return label


def _head_from_skin(pixmap, size=44):
    if pixmap.width() >= 64 and pixmap.height() >= 32:
        from PySide6.QtGui import QImage
        out = QImage(8, 8, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.drawPixmap(0, 0, pixmap.copy(8, 8, 8, 8))
        if pixmap.height() >= 64:
            painter.drawPixmap(0, 0, pixmap.copy(40, 8, 8, 8))
        painter.end()
        pixmap = QPixmap.fromImage(out)
    return hidpi_pixmap(pixmap, size, size, smooth=False)


class AboutPage(QWidget):
    open_settings = Signal()
    check_update = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('AboutPage')
        self._net = QNetworkAccessManager(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setContentsMargins(0, 24, 0, 24)
        group = QWidget()
        group.setObjectName('AboutCard')
        group.setAttribute(Qt.WA_StyledBackground, True)
        group.setStyleSheet(_card_style('AboutCard'))
        layout = QVBoxLayout(group)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(14)
        card_title = QLabel('关于')
        card_title.setStyleSheet('color:rgba(255,255,255,0.6); font-size:13px; font-weight:600;')
        layout.addWidget(card_title)

        gubwon = _icon_label()
        deepseek = _icon_label()
        zhipu = _icon_label()
        scl = _icon_label()
        scl_logo = _IMAGES_DIR / 'sparkler.png'
        if scl_logo.is_file():
            scl.setPixmap(rounded_pixmap(
                hidpi_pixmap(trim_transparent(QPixmap(str(scl_logo))), 44, 44)
            ))
        layout.addLayout(self._row(gubwon, 'Gubwon', 'Spark Craft Launcher 的开发者'))
        layout.addLayout(self._row(deepseek, 'DeepSeek', 'Spark Craft Launcher 的协助开发者'))
        layout.addLayout(self._row(zhipu, '智谱AI', 'Spark Craft Launcher 的协助开发者'))
        scl_row = self._row(scl, 'Spark Craft Launcher', f'当前版本: {_VERSION_TEXT}')
        self.check_update_btn = QPushButton('检查更新')
        self.check_update_btn.setCursor(Qt.PointingHandCursor)
        self.check_update_btn.setStyleSheet(
            'QPushButton { background:rgba(255,255,255,0.08); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.18); border-radius:7px; padding:6px 14px; }'
            'QPushButton:hover { background:rgba(0,120,212,0.72); }'
        )
        self.check_update_btn.clicked.connect(self.check_update.emit)
        scl_row.addWidget(self.check_update_btn)
        layout.addLayout(scl_row)

        root.addWidget(group)

        thanks_group = QWidget()
        thanks_group.setObjectName('ThanksCard')
        thanks_group.setAttribute(Qt.WA_StyledBackground, True)
        thanks_group.setStyleSheet(_card_style('ThanksCard'))
        thanks_layout = QVBoxLayout(thanks_group)
        thanks_layout.setContentsMargins(24, 18, 24, 18)
        thanks_layout.setSpacing(10)
        thanks_title = QLabel('特别鸣谢')
        thanks_title.setStyleSheet('color:rgba(255,255,255,0.6); font-size:13px; font-weight:600;')
        thanks_layout.addWidget(thanks_title)
        thanks_first = QLabel('24软件全体MC成员')
        thanks_first.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        thanks_layout.addWidget(thanks_first)
        thanks_second = QLabel('白咻')
        thanks_second.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px;')
        thanks_layout.addWidget(thanks_second)
        root.addWidget(thanks_group)
        root.addStretch(1)
        logo_row = QHBoxLayout()
        logo_row.addStretch(1)
        logo_box = QVBoxLayout()
        logo_box.setSpacing(6)
        launcher_logo = QLabel()
        launcher_logo.setFixedSize(72, 72)
        launcher_logo.setAlignment(Qt.AlignCenter)
        if scl_logo.is_file():
            launcher_logo.setPixmap(rounded_pixmap(
                hidpi_pixmap(trim_transparent(QPixmap(str(scl_logo))), 72, 72), 16
            ))
        logo_box.addWidget(launcher_logo, 0, Qt.AlignHCenter)
        launcher_name = QLabel('Spark Craft Launcher')
        launcher_name.setAlignment(Qt.AlignCenter)
        launcher_name.setStyleSheet('color:#88DDDD; font-size:16px; font-weight:700;')
        logo_box.addWidget(launcher_name, 0, Qt.AlignHCenter)
        logo_row.addLayout(logo_box)
        logo_row.addStretch(1)
        root.addLayout(logo_row)
        root.addStretch(1)

        self._load_deepseek(deepseek)
        self._load_zhipu(zhipu)
        self._load_gubwon(gubwon)

    def _row(self, icon_label, name, desc):
        icon_label.setFixedSize(44, 44)
        icon_label.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.addWidget(icon_label)
        row.addSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        name_label = QLabel(str(name or ''))
        name_label.setStyleSheet('color:rgba(255,255,255,0.9); font-size:14px; font-weight:600;')
        desc_label = QLabel(str(desc or ''))
        desc_label.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')
        text.addWidget(name_label)
        text.addWidget(desc_label)
        row.addLayout(text, 1)
        return row

    def _set_icon(self, label, pixmap):
        pixmap = trim_transparent(pixmap)
        label.setPixmap(rounded_pixmap(hidpi_pixmap(pixmap, 44, 44), 10))

    def _load_local(self, label, filename, fallback_text, fallback_color):
        path = _IMAGES_DIR / filename
        pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
        if pixmap.isNull():
            from ui.widgets.settings_widgets import initial_pixmap
            pixmap = initial_pixmap(fallback_text, fallback_color)
        self._set_icon(label, pixmap)

    def _load_deepseek(self, label):
        self._load_local(label, 'deepseek.png', 'DS', '#4D6BFE')

    def _load_zhipu(self, label):
        self._load_local(label, 'bigmodel.png', '智', '#3859FF')

    def _load_gubwon(self, label):
        cache = scl_dir() / 'icon_cache' / 'gubwon_head.png'
        if cache.is_file():
            pixmap = QPixmap(str(cache))
            if not pixmap.isNull():
                self._set_icon(label, pixmap)
                return
        steve = _IMAGES_DIR / 'steve_head.png'
        fallback = QPixmap(str(steve)) if steve.is_file() else QPixmap()
        self._set_icon(label, fallback)
        self._request(
            'https://api.mojang.com/users/profiles/minecraft/Gubwon',
            lambda reply: self._on_uuid_reply(label, reply),
        )

    def _request(self, url, done):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        reply = self._net.get(request)
        reply.finished.connect(lambda: done(reply))

    def _on_uuid_reply(self, label, reply):
        data = b''
        if reply.error() == QNetworkReply.NoError:
            data = reply.readAll()
        reply.deleteLater()
        if not data:
            return self._fallback_skin(label)
        try:
            uuid = str(json.loads(bytes(data).decode('utf-8')).get('id') or '').replace('-', '')
        except Exception:
            return self._fallback_skin(label)
        if not uuid:
            return self._fallback_skin(label)
        self._request(
            f'https://sessionserver.mojang.com/session/minecraft/profile/{uuid}',
            lambda r: self._on_profile_reply(label, r),
        )

    def _on_profile_reply(self, label, reply):
        data = b''
        if reply.error() == QNetworkReply.NoError:
            data = reply.readAll()
        reply.deleteLater()
        skin_url = ''
        try:
            profile = json.loads(bytes(data).decode('utf-8'))
            for prop in profile.get('properties') or []:
                if prop.get('name') == 'textures':
                    textures = json.loads(
                        base64.b64decode(str(prop.get('value') or '')).decode('utf-8')
                    )
                    skin_url = str(
                        (textures.get('textures') or {}).get('SKIN', {}).get('url') or ''
                    )
                    break
        except Exception:
            skin_url = ''
        if not skin_url:
            return self._fallback_skin(label)
        self._request(skin_url, lambda r: self._on_skin_reply(label, r))

    def _on_skin_reply(self, label, reply):
        data = b''
        if reply.error() == QNetworkReply.NoError:
            data = reply.readAll()
        reply.deleteLater()
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data) or pixmap.isNull():
            return self._fallback_skin(label)
        head = _head_from_skin(pixmap, 44)
        self._set_icon(label, head)
        try:
            cache_dir = scl_dir() / 'icon_cache'
            cache_dir.mkdir(parents=True, exist_ok=True)
            head.save(str(cache_dir / 'gubwon_head.png'), 'PNG')
        except OSError:
            pass

    def _fallback_skin(self, label):
        self._request(
            'https://mc-heads.net/Gubwon/44',
            lambda r: self._on_heads_reply(label, r),
        )

    def _on_heads_reply(self, label, reply):
        data = b''
        if reply.error() == QNetworkReply.NoError:
            data = reply.readAll()
        reply.deleteLater()
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data) and not pixmap.isNull():
            self._set_icon(label, pixmap)
            try:
                cache_dir = scl_dir() / 'icon_cache'
                cache_dir.mkdir(parents=True, exist_ok=True)
                pixmap.save(str(cache_dir / 'gubwon_head.png'), 'PNG')
            except OSError:
                pass
