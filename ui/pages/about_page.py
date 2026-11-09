'''关于页面（含特别鸣谢卡片）。'''

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPalette, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.app_font import ui_font
from core.paths import scl_dir
from core.update import VERSION_DISPLAY
from ui.design import ACCENT, DEEP_ACCENT_LIGHT, MUTED_DARK, MUTED_LIGHT, TEXT_DARK, TEXT_LIGHT
from ui.widgets.settings_widgets import hidpi_pixmap, rounded_pixmap, trim_transparent
from ui.widgets.collapsible_section import CollapsibleSection

_IMAGES_DIR = Path(__file__).resolve().parent.parent / 'imgs'
_VERSION_TEXT = VERSION_DISPLAY
_THANKS = (
    ('bangbang93', '提供 BMCLAPI 镜像源和 Forge 安装工具，详见 ', 'https://bmclapi.bangbang93.com'),
    ('白咻', '制作了 SCL 的启动器图片和加载图标！', ''),
    ('Ruiovo', '测试启动器找到一堆 bug！', ''),
    ('MC百科', '提供了 Mod 名称的中文翻译和更多相关信息！', ''),
    ('龙腾猫跃', 'PCL 制作者，他的仓库为我们的启动器提供了很多参考', ''),
    ('huanghongxun', 'HMCL 制作者，他的仓库为我们的启动器提供了很多参考', ''),
)
_LICENSES = (
    (
        'Python', 'Copyright © Python Software Foundation',
        'Licensed under the PSF License.',
        'https://www.python.org/', 'https://docs.python.org/3/license.html',
    ),
    (
        'PySide6', 'Copyright © The Qt Company Ltd.',
        'Licensed under the LGPL v3.',
        'https://www.qt.io/qt-for-python', 'https://www.gnu.org/licenses/lgpl-3.0.html',
    ),
    (
        'Qt 6', 'Copyright © The Qt Company Ltd.',
        'Licensed under the LGPL v3.',
        'https://www.qt.io/', 'https://www.gnu.org/licenses/lgpl-3.0.html',
    ),
    (
        'FFmpeg', 'Copyright © FFmpeg developers',
        'Licensed under the LGPL v2.1 or later.',
        'https://ffmpeg.org/', 'https://ffmpeg.org/legal.html',
    ),
    (
        'minecraft_launcher_lib', 'Copyright © JakobDev',
        'Licensed under the BSD 2-Clause License.',
        'https://github.com/JakobDev/minecraft-launcher-lib',
        'https://github.com/JakobDev/minecraft-launcher-lib/blob/master/LICENSE',
    ),
    (
        'requests', 'Copyright © Kenneth Reitz',
        'Licensed under the Apache License, Version 2.0.',
        'https://requests.readthedocs.io/', 'https://www.apache.org/licenses/LICENSE-2.0',
    ),
    (
        'urllib3', 'Copyright © Andrey Petrov and contributors',
        'Licensed under the MIT License.',
        'https://urllib3.readthedocs.io/',
        'https://github.com/urllib3/urllib3/blob/main/LICENSE.txt',
    ),
    (
        'certifi', 'Copyright © Kenneth Reitz',
        'Licensed under the Mozilla Public License 2.0.',
        'https://github.com/certifi/python-certifi',
        'https://github.com/certifi/python-certifi/blob/master/LICENSE',
    ),
    (
        'authlib-injector', 'Copyright © yushijinhun',
        'Licensed under the AGPL v3.0.',
        'https://github.com/yushijinhun/authlib-injector',
        'https://github.com/yushijinhun/authlib-injector/blob/develop/COPYING.md',
    ),
    (
        'HarmonyOS Sans', 'Copyright © Huawei Device Co., Ltd.',
        'Licensed under the HarmonyOS Sans Fonts License Agreement.',
        'https://developer.harmonyos.com/', '',
    ),
)
def _card_style(object_name):
    return (
        f'QWidget#{object_name} {{ background: rgba(30,41,41,0.84);'
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
    '''从皮肤纹理合成双层头部（底层 + Hat 覆盖层）。'''

    if pixmap.width() >= 64 and pixmap.height() >= 32:
        from PySide6.QtGui import QImage
        scale = max(1, int(round(pixmap.width() / 64.0)))
        out = QImage(8, 8, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        base = pixmap.copy(8 * scale, 8 * scale, 8 * scale, 8 * scale).scaled(
            8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation
        )
        painter.drawPixmap(0, 0, base)
        # 64x32 的旧皮肤也有 Hat 层，只是高度不足 64；不能只看高度跳过。
        if pixmap.height() >= 16 * scale:
            hat = pixmap.copy(40 * scale, 8 * scale, 8 * scale, 8 * scale).scaled(
                8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation
            )
            painter.drawPixmap(0, 0, hat)
        painter.end()
        pixmap = QPixmap.fromImage(out)
    return hidpi_pixmap(pixmap, size, size, smooth=False)


class _ThanksLineLabel(QLabel):
    '''特别鸣谢行：富文本颜色随主题切换，避免浅色主题下白字不可见。'''

    def __init__(self, name, desc, url='', parent=None):
        super().__init__(parent)
        self._name = str(name or '')
        self._desc = str(desc or '')
        self._url = str(url or '')
        self._light = None
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setOpenExternalLinks(True)
        self.setContentsMargins(0, 4, 0, 4)
        self.setStyleSheet('font-size:12px; background:transparent;')
        light = self.palette().color(QPalette.Window).lightness() > 128
        self.set_light(light)

    def set_light(self, light):
        light = bool(light)
        if light == self._light:
            return
        self._light = light
        if light:
            name_color = TEXT_LIGHT
            desc_color = MUTED_LIGHT
            link_color = DEEP_ACCENT_LIGHT
        else:
            name_color = TEXT_DARK
            desc_color = MUTED_DARK
            link_color = ACCENT
        text = (
            f'<span style="color:{name_color}; font-weight:700;">'
            + html.escape(self._name)
            + f'</span><span style="color:{desc_color};">——'
            + html.escape(self._desc)
        )
        if self._url:
            text += (
                '<a href="' + html.escape(self._url, quote=True)
                + f'" style="color:{link_color};">' + html.escape(self._url) + '</a>'
            )
        self.setText(text + '</span>')


class AboutPage(QWidget):
    open_settings = Signal()
    check_update = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('AboutPage')
        self._net = QNetworkAccessManager(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }'
            'QScrollArea > QWidget > QWidget { background: transparent; }'
        )
        content = QWidget()
        content.setStyleSheet('background: transparent;')
        root = QVBoxLayout(content)
        root.setContentsMargins(40, 24, 40, 24)
        root.setSpacing(16)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)
        group = QWidget()
        group.setObjectName('AboutCard')
        group.setAttribute(Qt.WA_StyledBackground, True)
        group.setStyleSheet(_card_style('AboutCard'))
        layout = QVBoxLayout(group)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        card_title = QLabel('关于')
        card_title.setStyleSheet('color:rgba(255,255,255,0.6); font-size:13px; font-weight:900;')
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
        gubwon_row = self._row(gubwon, 'Gubwon', 'Spark Craft Launcher 的开发者')
        self.sponsor_btn = QPushButton('赞助SCL！')
        self.sponsor_btn.setCursor(Qt.PointingHandCursor)
        self.sponsor_btn.setFixedSize(128, 34)
        self.sponsor_btn.setStyleSheet(
            'QPushButton { background:rgba(255,255,255,0.08); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.18); border-radius:7px; padding:6px 14px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.72); }'
        )
        self.sponsor_btn.clicked.connect(
            (lambda: QDesktopServices.openUrl(QUrl('https://ifdian.net/a/200811GJH')))
        )
        gubwon_row.layout().addWidget(self.sponsor_btn)
        layout.addWidget(gubwon_row)
        layout.addWidget(self._row(deepseek, 'DeepSeek', 'Spark Craft Launcher 的协助开发者'))
        layout.addWidget(self._row(zhipu, '智谱AI', 'Spark Craft Launcher 的协助开发者'))
        scl_row = self._row(scl, 'Spark Craft Launcher', f'当前版本: {_VERSION_TEXT}')
        self.check_update_btn = QPushButton('检查更新')
        self.check_update_btn.setCursor(Qt.PointingHandCursor)
        self.check_update_btn.setFixedSize(128, 34)
        self.check_update_btn.setStyleSheet(
            'QPushButton { background:rgba(255,255,255,0.08); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.18); border-radius:7px; padding:6px 14px; }'
            'QPushButton:hover { background:rgba(136,221,221,0.72); }'
        )
        self.check_update_btn.clicked.connect(self.check_update.emit)
        scl_row.layout().addWidget(self.check_update_btn)
        layout.addWidget(scl_row)

        root.addWidget(group)

        thanks_group = QWidget()
        thanks_group.setObjectName('ThanksCard')
        thanks_group.setAttribute(Qt.WA_StyledBackground, True)
        thanks_group.setStyleSheet(_card_style('ThanksCard'))
        thanks_layout = QVBoxLayout(thanks_group)
        thanks_layout.setContentsMargins(24, 20, 24, 20)
        thanks_layout.setSpacing(14)
        thanks_title = QLabel('特别鸣谢')
        thanks_title.setStyleSheet('color:rgba(255,255,255,0.6); font-size:13px; font-weight:900;')
        thanks_layout.addWidget(thanks_title)
        for name, desc, url in _THANKS:
            thanks_layout.addWidget(self._thanks_line(name, desc, url))
        root.addWidget(thanks_group)

        self.license_section = CollapsibleSection('许可与版权声明', expanded=False)
        self.license_section.title_label.setStyleSheet(
            'color:#A8E6E6; font-size:15px; font-weight:900; background:transparent;'
        )
        self.license_section.content_layout.setSpacing(16)
        for entry in _LICENSES:
            self.license_section.content_layout.addLayout(self._license_row(*entry))
        root.addWidget(self.license_section)
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
        launcher_name.setStyleSheet('color:#88DDDD; font-size:16px; font-weight:900;')
        logo_box.addWidget(launcher_name, 0, Qt.AlignHCenter)
        logo_row.addLayout(logo_box)
        logo_row.addStretch(1)
        root.addLayout(logo_row)
        root.addStretch(1)

        self._load_deepseek(deepseek)
        self._load_zhipu(zhipu)
        self._load_gubwon(gubwon)

    def _row(self, icon_label, name, desc):
        row_widget = QWidget()
        row_widget.setStyleSheet('background:transparent;')
        icon_label.setFixedSize(44, 44)
        icon_label.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 5, 0, 5)
        row.addWidget(icon_label)
        row.addSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(4)
        name_label = QLabel(str(name or ''))
        name_label.setStyleSheet('color:rgba(255,255,255,0.9); font-size:14px; font-weight:900;')
        desc_label = QLabel(str(desc or ''))
        desc_label.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')
        text.addWidget(name_label)
        text.addWidget(desc_label)
        row.addLayout(text, 1)
        return row_widget

    def _thanks_line(self, name, desc, url=''):
        return _ThanksLineLabel(name, desc, url)

    def _license_row(self, name, copyright_text, license_text, site_url, license_url):
        row = QHBoxLayout()
        row.setSpacing(18)
        name_label = QLabel(str(name))
        name_label.setFixedWidth(170)
        name_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        name_label.setWordWrap(True)
        name_label.setStyleSheet(
            'color:rgba(255,255,255,0.9); font-size:13px; font-weight:900;'
        )
        row.addWidget(name_label, 0, Qt.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        copyright_label = QLabel(str(copyright_text))
        copyright_label.setWordWrap(True)
        copyright_label.setStyleSheet(
            'color:rgba(255,255,255,0.78); font-size:12px;'
        )
        text_col.addWidget(copyright_label)
        license_label = QLabel(str(license_text))
        license_label.setWordWrap(True)
        license_label.setStyleSheet(
            'color:rgba(255,255,255,0.55); font-size:12px;'
        )
        text_col.addWidget(license_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for label, url in (('查看来源网站', site_url), ('查看许可文档', license_url)):
            if not url:
                continue
            button = QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(118, 30)
            button.setStyleSheet(
                'QPushButton { background:transparent; color:#A8E6E6;'
                ' border:1px solid rgba(136,221,221,0.48); border-radius:5px;'
                ' font-size:11px; }'
                'QPushButton:hover { background:rgba(136,221,221,0.18); }'
            )
            button.clicked.connect(
                lambda checked=False, target=url: QDesktopServices.openUrl(QUrl(target))
            )
            buttons.addWidget(button)
        buttons.addStretch(1)
        text_col.addLayout(buttons)
        row.addLayout(text_col, 1)
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
        # 先显示预缓存头像，避免机房网络不可用时直接变成史蒂夫；
        # 同时继续请求官方资料，网络恢复后自动替换为最新头像。
        cache = scl_dir() / 'icon_cache' / 'gubwon_head.png'
        bundled = _IMAGES_DIR / 'gubwon_head.png'
        # 内置头像已合成 Hat 覆盖层；旧缓存可能是单层，优先使用内置版本，
        # 网络恢复后再用 Mojang 的最新皮肤覆盖。
        initial = bundled if bundled.is_file() else cache
        pixmap = QPixmap(str(initial)) if initial.is_file() else QPixmap()
        if pixmap.isNull():
            steve = _IMAGES_DIR / 'steve_head.png'
            pixmap = QPixmap(str(steve)) if steve.is_file() else QPixmap()
        self._set_icon(label, pixmap)
        self._request(
            'https://api.mojang.com/users/profiles/minecraft/Gubwon',
            lambda reply: self._on_uuid_reply(label, reply),
        )

    def _request(self, url, done):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'User-Agent', b'PMGL-Launcher/1.1')
        reply = self._net.get(request)
        try:
            from core.config import ConfigManager
            if not bool(ConfigManager().get('verify_ssl', False)):
                reply.sslErrors.connect(
                    lambda _errors, r=reply: r.ignoreSslErrors()
                )
        except Exception:
            pass
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
            'https://mc-heads.net/avatar/Gubwon/44',
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
