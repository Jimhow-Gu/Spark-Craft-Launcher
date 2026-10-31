from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QListWidgetItem, QLabel, QMessageBox, QApplication, QSizeGrip, QGraphicsColorizeEffect, QPushButton, QGraphicsOpacityEffect, QFrame)
from PySide6.QtCore import (Qt, QPoint, QPropertyAnimation, QVariantAnimation, QEasingCurve, QTimer, QSize, QEvent, QRect)
from PySide6.QtGui import QColor, QCursor, QFont, QPixmap
import os
from pathlib import Path
from core.app_font import ui_font
from core.background import get_settings
from core.music import BackgroundMusicPlayer
from ui.pages import (HomePage, VersionPage, AccountPage, SettingsPage, GameSettingsPage)
from ui.pages.about_page import AboutPage
from ui.pages.download_manager_page import DownloadManagerPage
from ui.widgets.account_card import AccountCard
from ui.widgets.download_status_bar import DownloadStatusBar
from ui.widgets.painted_icons import set_button_icon
from ui.widgets.media_button import MediaButton
from ui.widgets.title_bar import WindowTitleBar
from ui.widgets.no_wheel import WheelGuard
from ui.widgets.background_widget import BackgroundWidget
from ui.widgets.window_border import WindowBorderOverlay
from ui.widgets.side_panel import SidePanel
from ui.widgets.side_nav import SideNavList, NAV_HEADER_ROLE, NAV_ICON_ROLE
from ui.widgets.brand_logo import BrandLogo
from ui.theme import ThemeManager
from core.download_manager import DownloadManager
from core.build_info import BUILD_LABEL, RELEASE_BUILD, RESOURCE_PACK_SEQUENCE, EXECUTABLE_BUILD
from core.update import CURRENT_VERSION

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowState(Qt.WindowNoState)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = 1500
        if available is not None:
            width = min(width, int(available.width() * 0.86), int(available.height() * 0.86 * 16 / 9))
        width = max(960, int(width * 6 / 7))
        width = width // 16 * 16
        height = width * 9 // 16
        self.setFixedSize(width, height)
        self.setWindowTitle('Spark Craft Launcher')
        app = QApplication.instance()
        if app is not None and not app.windowIcon().isNull():
            self.setWindowIcon(app.windowIcon())
        self.download_manager = DownloadManager.instance()
        self.music_player = BackgroundMusicPlayer.instance()
        self._media_backgrounded = False
        self._update_checker = None
        self._update_dialog = None
        self._pending_update_info = None
        self._pending_update_kind = 'version'
        self._update_notified_tag = ''
        self._update_prepared = False
        self._update_prepared_tag = ''
        self._update_prepare_error = ''
        self._video_resume_pending = False
        self._music_resume_pending = False
        self._music_auto_started = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.central = BackgroundWidget()
        self.central.setAcceptDrops(True)
        self.setAcceptDrops(True)
        self.setCentralWidget(self.central)
        self.border_overlay = WindowBorderOverlay(self)
        self.root_layout = QVBoxLayout(self.central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self.title_bar = WindowTitleBar(self)
        self.root_layout.addWidget(self.title_bar)
        self.body = QWidget()
        self.body.setStyleSheet('background:transparent; border:none;')
        self.main_layout = QHBoxLayout(self.body)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.root_layout.addWidget(self.body, 1)
        self.size_grip = QSizeGrip(self.central)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.setStyleSheet('background:transparent;')
        self.size_grip.hide()
        self._sidebar_collapsed_width = 90
        self._sidebar_expanded_width = 180
        self.title_bar.set_left_margin(self._sidebar_collapsed_width + 12)
        self._sidebar_expanded = False
        self._sidebar_anim = None
        self._sidebar_spacer_anim = None
        self._media_button_anims = []
        self._sidebar_collapse_timer = QTimer(self)
        self._sidebar_collapse_timer.setSingleShot(True)
        self._sidebar_collapse_timer.setInterval(140)
        self._sidebar_collapse_timer.timeout.connect(self._collapse_sidebar)

        # 侧栏默认是图标轨道；鼠标进入时以覆盖层方式展开，
        # 不改变右侧页面宽度，避免展开动画触发布局抖动。
        self.left_panel = SidePanel(self.central)
        self.left_panel.setStyleSheet('background: transparent;')
        self.left_panel.setGeometry(
            0, 0, self._sidebar_collapsed_width, self.central.height()
        )
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self._sidebar_header = QWidget()
        self._sidebar_header.setFixedHeight(56)
        self._sidebar_header.setStyleSheet('background:transparent;')
        self._sidebar_header_layout = QHBoxLayout(self._sidebar_header)
        self._sidebar_header_layout.setContentsMargins(31, 0, 0, 0)
        self._sidebar_header_layout.setSpacing(8)
        self.brand_logo = BrandLogo(28)
        self._sidebar_header_layout.addWidget(self.brand_logo)
        self.brand_label = QLabel('Spark Craft Launcher')
        self.brand_label.setFont(ui_font(12, QFont.DemiBold))
        self.brand_label.setStyleSheet('color:#88DDDD; background:transparent;')
        self.brand_label.setWordWrap(True)
        self.brand_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.brand_label.setMaximumWidth(162)
        self.brand_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._sidebar_brand_effect = QGraphicsOpacityEffect(self.brand_label)
        self._sidebar_brand_effect.setOpacity(0.0)
        self.brand_label.setGraphicsEffect(self._sidebar_brand_effect)
        self._sidebar_brand_anim = None
        self.brand_label.hide()
        self._sidebar_header_layout.addWidget(self.brand_label, 1)
        self._sidebar_header_layout.addStretch(0)
        left_layout.addWidget(self._sidebar_header)
        self.account_card = AccountCard()
        self.account_card.set_avatar_only(True)
        self.account_card.clicked.connect((lambda: self._switch_page(2)))
        self._avatar_fail_toast_shown = False
        self.account_card.avatar_failed.connect(self._on_avatar_failed)
        left_layout.addWidget(self.account_card, 0, Qt.AlignHCenter)
        self.nav_list = SideNavList()
        self._add_nav_item('启动', 0, None, 'play')
        self._add_nav_header('游戏版本')
        self._add_nav_item('下载', 1, 'available', 'download')
        self._add_nav_header('社区资源')
        self._add_nav_item('模组', 1, 'mod', 'components')
        self._add_nav_item('整合包', 1, 'modpack', 'package')
        self._add_nav_item('数据包', 1, 'datapack', 'database')
        self._add_nav_item('资源包', 1, 'resourcepack', 'palette')
        self._add_nav_item('光影包', 1, 'shader', 'sun')
        self._add_nav_header('通用')
        self._add_nav_item('设置', 3, None, 'gear')
        self._add_nav_item('关于', 4, None, 'info')
        left_layout.addWidget(self.nav_list, 1)
        media_row = QWidget()
        media_row.setFixedHeight(28)
        media_row.setStyleSheet('background:transparent;')
        self.video_toggle_btn = MediaButton(media_row)
        self.video_toggle_btn.setFixedSize(30, 28)
        self.video_toggle_btn.setIconSize(QSize(16, 16))
        self.video_toggle_btn.setToolTip('播放/暂停背景视频')
        self.video_toggle_btn.clicked.connect(self._toggle_background_video)
        self.music_toggle_btn = MediaButton(media_row)
        self.music_toggle_btn.setFixedSize(30, 28)
        self.music_toggle_btn.setIconSize(QSize(16, 16))
        self.music_toggle_btn.setToolTip('播放/暂停背景音乐')
        self.music_toggle_btn.clicked.connect(self._toggle_background_music)
        self.media_row = media_row
        left_layout.addWidget(media_row)
        self.status_label = QLabel(f'V{CURRENT_VERSION}')
        self.status_label.setStyleSheet('color:#9a9a9a; font-size:9px; background:transparent;')
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setToolTip(
            f'{BUILD_LABEL}' + chr(10) +
            f'Release 构建号：{RELEASE_BUILD}' + chr(10) +
            f'资源包序号：{RESOURCE_PACK_SEQUENCE}' + chr(10) +
            f'可执行程序/DLL 构建号：{EXECUTABLE_BUILD}'
        )
        self._version_effect = QGraphicsOpacityEffect(self.status_label)
        self._version_effect.setOpacity(1.0)
        self.status_label.setGraphicsEffect(self._version_effect)
        self._version_anim = None
        left_layout.addWidget(self.status_label)
        self.central.video_state_changed.connect((lambda _playing: self._update_media_buttons()))
        self.music_player.state_changed.connect(self._update_media_buttons)
        QTimer.singleShot(0, self._update_media_buttons)
        self.page_stack = QStackedWidget()
        self.page_stack.setFrameShape(QFrame.NoFrame)
        self.page_stack.setStyleSheet('QStackedWidget { border: none; background: transparent; }')
        self.download_bar = DownloadStatusBar()
        self.download_bar.clicked.connect(self.show_download_manager_page)
        self.download_manager.changed.connect(self._refresh_download_bar)
        self.right_panel = QWidget()
        self.right_panel.setStyleSheet('background:transparent; border:none;')
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(0)
        self.right_layout.addWidget(self.page_stack, 1)
        self.right_layout.addWidget(self.download_bar)
        self.sidebar_spacer = QWidget()
        self.sidebar_spacer.setFixedWidth(self._sidebar_collapsed_width)
        self.sidebar_spacer.setStyleSheet('background: transparent;')
        self.main_layout.addWidget(self.sidebar_spacer)
        self.main_layout.addWidget(self.right_panel, 1)
        self.left_panel.raise_()
        self._set_sidebar_expanded(False, animate=False)
        QTimer.singleShot(0, self._update_sidebar_geometry)
        self._download_return_widget = None
        self.game_settings_page = None
        self._wheel_guard = WheelGuard(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._wheel_guard)
        self.init_all_pages()
        self.nav_list.currentItemChanged.connect(self._on_page_changed)
        self.nav_list.itemClicked.connect(self._on_nav_clicked)
        self._fade_anim = None
        self.start_fade_anim()

    def _add_nav_header(self, text):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        item.setData(NAV_HEADER_ROLE, True)
        self.nav_list.addItem(item)

    def _add_nav_item(self, text, page_index, section, icon_name=None):
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, (page_index, section))
        item.setData(NAV_ICON_ROLE, icon_name or '')
        self.nav_list.addItem(item)

    def _on_nav_clicked(self, item):
        self._on_page_changed(item)

    def _is_sidebar_widget(self, widget):
        try:
            return bool(
                isinstance(widget, QWidget)
                and (
                    widget is self.left_panel
                    or self.left_panel.isAncestorOf(widget)
                )
            )
        except RuntimeError:
            return False

    def _set_sidebar_brand_visible(self, visible: bool, animate: bool=True):
        label = getattr(self, 'brand_label', None)
        effect = getattr(self, '_sidebar_brand_effect', None)
        if label is None or effect is None:
            return
        if self._sidebar_brand_anim is not None:
            try:
                self._sidebar_brand_anim.stop()
            except RuntimeError:
                pass
            self._sidebar_brand_anim = None
        if not animate:
            effect.setOpacity(1.0 if visible else 0.0)
            label.setVisible(visible)
            return
        if visible:
            label.show()
        animation = QPropertyAnimation(effect, b'opacity', self)
        animation.setDuration(120 if visible else 90)
        animation.setStartValue(effect.opacity())
        animation.setEndValue(1.0 if visible else 0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        if not visible:
            animation.finished.connect(label.hide)
        else:
            animation.finished.connect(lambda: effect.setOpacity(1.0))
        self._sidebar_brand_anim = animation
        animation.start()

    def _expand_sidebar(self):
        self._sidebar_collapse_timer.stop()
        if self._sidebar_expanded:
            return
        self._set_sidebar_expanded(True)

    def _collapse_sidebar(self):
        if not self._sidebar_expanded:
            return
        try:
            if self.left_panel.rect().contains(
                self.left_panel.mapFromGlobal(QCursor.pos())
            ):
                return
        except RuntimeError:
            return
        self._set_sidebar_expanded(False)

    def _animate_sidebar_spacer(self, target_width: int, animate: bool=True, duration: int=190):
        current = self.sidebar_spacer.width()
        if self._sidebar_spacer_anim is not None:
            try:
                self._sidebar_spacer_anim.stop()
            except RuntimeError:
                pass
            self._sidebar_spacer_anim = None
        if not animate or not self.isVisible():
            self.sidebar_spacer.setFixedWidth(int(target_width))
            return
        animation = QVariantAnimation(self)
        animation.setDuration(int(duration))
        animation.setStartValue(current)
        animation.setEndValue(int(target_width))
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.valueChanged.connect(
            lambda value: self.sidebar_spacer.setFixedWidth(int(value))
        )
        self._sidebar_spacer_anim = animation
        animation.start()

    def _set_version_label(self, expanded: bool, animate: bool=True):
        text = BUILD_LABEL if expanded else f'V{CURRENT_VERSION}'
        if self.status_label.text() == text:
            return
        if self._version_anim is not None:
            try:
                self._version_anim.stop()
            except RuntimeError:
                pass
            self._version_anim = None
        self.status_label.setText(text)
        if not animate:
            self._version_effect.setOpacity(1.0)
            return
        self._version_effect.setOpacity(0.0)
        animation = QPropertyAnimation(self._version_effect, b'opacity', self)
        animation.setDuration(150)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._version_anim = animation
        animation.start()

    def _set_sidebar_expanded(self, expanded: bool, animate: bool=True):
        expanded = bool(expanded)
        if self._sidebar_anim is not None:
            try:
                self._sidebar_anim.stop()
            except RuntimeError:
                pass
            self._sidebar_anim = None
        self._sidebar_expanded = expanded
        try:
            self.left_panel.set_expanded(expanded)
        except AttributeError:
            pass
        target_width = (
            self._sidebar_expanded_width
            if expanded else self._sidebar_collapsed_width
        )
        self._animate_sidebar_spacer(
            target_width, animate and self.isVisible(),
            190 if expanded else 160,
        )
        self._animate_media_buttons(
            target_width,
            190 if expanded else 160,
            animate and self.isVisible(),
        )
        self.nav_list.animate_expanded(
            expanded, 190 if expanded else 160,
            animate and self.isVisible(),
        )
        self._set_version_label(
            expanded, animate and self.isVisible()
        )
        self.account_card.set_compact(
            not expanded, self._sidebar_expanded_width - 20,
            animate and self.isVisible(),
        )
        if expanded:
            self.title_bar.set_title_visible(
                False, animate=bool(animate and self.isVisible())
            )
            self._set_sidebar_brand_visible(
                bool(getattr(self, '_title_available', True)),
                animate=bool(animate and self.isVisible()),
            )
        else:
            self._set_sidebar_brand_visible(
                False, animate=bool(animate and self.isVisible())
            )
        if not animate or not self.isVisible():
            self.left_panel.setGeometry(
                0, 0, target_width, self.central.height()
            )
            self.left_panel.raise_()
            self._position_media_buttons(target_width)
            if not expanded:
                self.title_bar.set_title_visible(
                    getattr(self, '_title_available', True)
                )
            return
        animation = QPropertyAnimation(self.left_panel, b'geometry', self)
        animation.setDuration(190 if expanded else 160)
        animation.setStartValue(self.left_panel.geometry())
        animation.setEndValue(
            QRect(0, 0, target_width, self.central.height())
        )
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(
            lambda value=expanded: self._on_sidebar_anim_finished(value)
        )
        self._sidebar_anim = animation
        animation.start()

    def _on_sidebar_anim_finished(self, expanded: bool):
        self._sidebar_anim = None
        target_width = (
            self._sidebar_expanded_width
            if expanded else self._sidebar_collapsed_width
        )
        self.left_panel.setGeometry(
            0, 0, target_width, self.central.height()
        )
        self.left_panel.raise_()
        self._position_media_buttons(target_width)
        if not expanded:
            self.title_bar.set_title_visible(
                getattr(self, '_title_available', True), animate=True
            )

    def _media_button_targets(self, width):
        total = max(0, int(width))
        buttons = [
            button for button in (self.video_toggle_btn, self.music_toggle_btn)
            if button.isVisible()
        ]
        if not buttons:
            return []
        button_w = 30
        if len(buttons) == 1:
            return [(buttons[0], max(0, (total - button_w) // 2))]
        collapsed = self._sidebar_collapsed_width
        expanded = self._sidebar_expanded_width
        progress = 0.0
        if expanded > collapsed:
            progress = max(0.0, min(1.0, (total - collapsed) / (expanded - collapsed)))
        pair_width = button_w * 2 + 6
        start = (collapsed - pair_width) // 2
        x1_collapsed = start
        x2_collapsed = start + button_w + 6
        x1_expanded = expanded // 4 - button_w // 2
        x2_expanded = expanded * 3 // 4 - button_w // 2
        return [
            (buttons[0], int(x1_collapsed + (x1_expanded - x1_collapsed) * progress)),
            (buttons[1], int(x2_collapsed + (x2_expanded - x2_collapsed) * progress)),
        ]

    def _position_media_buttons(self, width=None):
        if not hasattr(self, 'media_row'):
            return
        total = width if width is not None else self.left_panel.width()
        for button, x in self._media_button_targets(total):
            button.move(x, 0)

    def _animate_media_buttons(self, target_width, duration=190, animate=True):
        if not hasattr(self, 'media_row'):
            return
        for animation in self._media_button_anims:
            try:
                animation.stop()
            except RuntimeError:
                pass
        self._media_button_anims = []
        targets = self._media_button_targets(target_width)
        if not animate or not self.isVisible():
            for button, x in targets:
                button.move(x, 0)
            return
        for button, x in targets:
            animation = QPropertyAnimation(button, b'pos', self)
            animation.setDuration(int(duration))
            animation.setStartValue(button.pos())
            animation.setEndValue(QPoint(x, 0))
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.start()
            self._media_button_anims.append(animation)

    def _update_sidebar_geometry(self):
        try:
            if self._sidebar_anim is not None:
                self._sidebar_anim.stop()
                self._sidebar_anim = None
            width = (
                self._sidebar_expanded_width
                if self._sidebar_expanded
                else self._sidebar_collapsed_width
            )
            self.left_panel.setGeometry(0, 0, width, self.central.height())
            self.left_panel.raise_()
            self._position_media_buttons(width)
            if self._sidebar_spacer_anim is None:
                self.sidebar_spacer.setFixedWidth(width)
        except RuntimeError:
            pass

    def _select_nav(self, page_index, section=None):
        if page_index == 2:
            self.nav_list.blockSignals(True)
            self.nav_list.setCurrentRow(-1)
            self.nav_list.clearSelection()
            self.nav_list.blockSignals(False)
            return
        for i in range(self.nav_list.count()):
            item = self.nav_list.item(i)
            data = item.data(Qt.UserRole)
            if data == (page_index, section):
                self.nav_list.setCurrentItem(item)
                break
        else:
            for i in range(self.nav_list.count()):
                item = self.nav_list.item(i)
                data = item.data(Qt.UserRole)
                if data and data[0] == page_index:
                    self.nav_list.setCurrentItem(item)
                    break

    def show_download_section(self, section):
        self._select_nav(1, section)
        self.page_stack.setCurrentIndex(1)
        self.version_page.show_section(section)

    def _apply_title(self, title=None):
        if title is None:
            try:
                title = self.settings_page.config.get('global_window_title', 'Spark Craft Launcher')
            except Exception:
                title = 'Spark Craft Launcher'
        title = str(title or 'Spark Craft Launcher').strip() or 'Spark Craft Launcher'
        try:
            color = self.settings_page.config.get('global_title_color', '#88DDDD') or '#88DDDD'
        except Exception:
            color = '#88DDDD'
        self.setWindowTitle('Spark Craft Launcher')
        try:
            mode = self.settings_page.config.get('title_bar_mode', 'text') or 'text'
            image = self.settings_page.config.get('global_title_image', '') or ''
        except Exception:
            mode = 'text'
            image = ''
        try:
            self._title_available = mode != 'none'
            if mode == 'none':
                self.brand_label.clear()
                self.brand_label.hide()
                self.title_bar.set_title_visible(False)
                return
            if mode == 'image' and image:
                pixmap = QPixmap(image)
                if not pixmap.isNull():
                    self.brand_label.setText('')
                    self.brand_label.setPixmap(
                        pixmap.scaledToHeight(20, Qt.SmoothTransformation)
                    )
                    self.brand_label.setStyleSheet('background:transparent;')
                    self.title_bar.set_title_image(image)
                    self.title_bar.set_title_visible(
                        not self._sidebar_expanded
                    )
                    if self._sidebar_expanded:
                        self._set_sidebar_brand_visible(True, animate=False)
                    return
                brand_text = 'Spark Craft Launcher'
            else:
                brand_text = 'Spark Craft Launcher' if mode == 'default' else title
            self.brand_label.setPixmap(QPixmap())
            self.brand_label.setText(brand_text)
            self.brand_label.setStyleSheet(
                f'color:{color}; background:transparent;'
            )
            self.title_bar.set_title(brand_text, color)
            self.title_bar.set_title_visible(not self._sidebar_expanded)
            if self._sidebar_expanded:
                self._set_sidebar_brand_visible(True, animate=False)
        except Exception:
            pass

    def init_all_pages(self):
        self.home_page = HomePage(self)
        self.version_page = VersionPage(self)
        self.account_page = AccountPage(self)
        self.settings_page = SettingsPage(self)
        self.version_page.version_installed.connect(self.home_page.refresh_versions)
        self.version_page.version_selected.connect(self._on_version_selected)
        self.version_page.game_dir_changed.connect(self._on_game_dir_changed)
        self.home_page.request_switch_page.connect(self._switch_page)
        self.home_page.request_offline_login.connect((lambda: self._switch_page(2)))
        self.home_page.open_game_settings.connect(self.open_game_settings)
        self.home_page.game_dir_changed.connect(self._on_home_game_dir_changed)
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.version_page)
        self.page_stack.addWidget(self.account_page)
        self.page_stack.addWidget(self.settings_page)
        self.about_page = AboutPage()
        self.about_page.check_update.connect((lambda: self._check_updates(manual=True)))
        self.page_stack.addWidget(self.about_page)
        self.download_manager_page = DownloadManagerPage(self)
        self.download_manager_page.back_requested.connect(self.close_download_manager_page)
        self.page_stack.addWidget(self.download_manager_page)
        self._page_animation = None
        self._page_fade_overlay = None
        self.page_stack.currentChanged.connect(self._animate_page_change)
        self.page_stack.currentChanged.connect(self._style_current_page)
        try:
            self.settings_page.background_changed.connect(self._refresh_background)
            self.settings_page.background_refresh.connect((lambda: self._refresh_background(force=True)))
            self.settings_page.title_changed.connect(self._apply_title)
            self.settings_page.settings_changed.connect(self._on_settings_changed)
            self.settings_page.video_music_changed.connect(self._on_video_music_changed)
            self.settings_page.video_volume_changed.connect(self.central.set_video_volume)
            try:
                self.central.set_video_volume(
                    int(self.settings_page.config.get('background_video_volume', 70) or 70)
                )
            except Exception:
                pass
        except Exception:
            pass
        try:
            self.account_page.account_changed.connect(self.refresh_account_card)
            self.account_page.account_changed.connect(
                (lambda: self.home_page.refresh_versions())
            )
        except Exception:
            pass
        self.refresh_account_card()
        self.central.reload()
        self._apply_ui_depth()
        self._apply_title()
        self._select_nav(0)
        self.theme_manager = ThemeManager(self)
        try:
            self.settings_page.theme_changed.connect(self._on_theme_changed)
        except Exception:
            pass
        try:
            self.theme_manager.apply(
                self.settings_page.config.get('theme', 'system'),
                animate=False,
            )
        except Exception:
            pass
        try:
            video_music = bool(
                self.settings_page.config.get('background_video_audio', False)
            )
            self.central.set_video_audio_enabled(video_music)
            if video_music:
                self.music_player.pause(remember=False)
        except Exception:
            pass
        QTimer.singleShot(0, self._update_media_buttons)

    def _on_theme_changed(self, theme):
        try:
            self.theme_manager.apply(theme, QCursor.pos(), animate=True)
        except Exception:
            pass
        try:
            self.border_overlay.update()
        except Exception:
            pass

    def _refresh_background(self, force: bool=False):
        self.central.reload(force)
        self._apply_ui_depth()
        QTimer.singleShot(0, self._update_media_buttons)

    def _update_media_buttons(self):
        if not hasattr(self, 'video_toggle_btn'):
            return
        try:
            video_available = self.central.video_available()
            video_playing = self.central.video_playing()
        except Exception:
            video_available, video_playing = False, False
        self.video_toggle_btn.setVisible(video_available)
        self.video_toggle_btn.setEnabled(video_available)
        set_button_icon(self.video_toggle_btn, 'pause' if video_playing else 'play', 16, '#e6edf3')
        self.video_toggle_btn.setToolTip('暂停背景视频' if video_playing else '播放背景视频')
        try:
            music_playing = self.music_player.is_playing()
            music_muted = self.music_player.is_muted()
            try:
                has_music = bool(self.music_player.has_tracks())
            except AttributeError:
                has_music = bool(self.music_player.current_name())
        except Exception:
            music_playing, music_muted, has_music = False, False, False
        audible = music_playing and not music_muted
        self.music_toggle_btn.setVisible(has_music)
        self.music_toggle_btn.setEnabled(has_music)
        set_button_icon(
            self.music_toggle_btn,
            'music' if audible else 'music_off',
            16,
            '#e6edf3',
        )
        self.music_toggle_btn.setToolTip('暂停背景音乐' if audible else '播放背景音乐')
        if hasattr(self, 'media_row'):
            self.media_row.setVisible(video_available or has_music)
            self._position_media_buttons()

    def _toggle_background_video(self):
        self.central.toggle_video()
        self._update_media_buttons()

    def _toggle_background_music(self):
        if self.music_player.is_playing() and not self.music_player.is_muted():
            self.music_player.pause()
            self._update_media_buttons()
            return
        try:
            if (
                self.central.video_audio_enabled()
                or self.settings_page.bg_video_music_check.isChecked()
            ):
                self.settings_page.set_video_music_checked(False)
                self.central.set_video_audio_enabled(False)
        except Exception:
            pass
        self.music_player.set_muted(False)
        self.music_player.play()
        self._update_media_buttons()

    def _on_video_music_changed(self, enabled: bool):
        try:
            self.central.set_video_audio_enabled(bool(enabled))
            self.music_player.set_video_music_enabled(bool(enabled))
            if enabled:
                self.central.play_video()
        except Exception:
            pass
        self._update_media_buttons()

    def _pause_background_media(self):
        '''启动器进入后台时按设置暂停视频/音乐，记录原本正在播放的状态。'''

        if self._media_backgrounded:
            return
        self._media_backgrounded = True
        try:
            from core.config import ConfigManager
            config = ConfigManager()
            pause_music = bool(config.get('background_music_pause_on_background', True))
        except Exception:
            pause_music = True
        pause_video = bool(get_settings().get('pause_video_on_background', True))
        self._video_resume_pending = self.central.video_playing() if pause_video else False
        if self._video_resume_pending:
            self.central.pause_video()
        try:
            self._music_resume_pending = self.music_player.is_playing() if pause_music else False
            if self._music_resume_pending:
                self.music_player.pause(remember=False)
        except Exception:
            self._music_resume_pending = False
        self._update_media_buttons()

    def _resume_background_media(self):
        '''切回前台时恢复之前正在播放的视频和音乐。'''

        if not self._media_backgrounded or self.isMinimized():
            return
        self._media_backgrounded = False
        if self._video_resume_pending and not self._music_resume_pending:
            self.central.play_video()
        if self._music_resume_pending and not self.central.video_audio_enabled():
            try:
                self.music_player.play(remember=False)
            except Exception:
                pass
        self._video_resume_pending = False
        self._music_resume_pending = False
        self._update_media_buttons()

    @staticmethod
    def _modpack_paths_from_mime(mime):
        if mime is None or not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in ('.mrpack', '.zip'):
                paths.append(str(path))
        return paths

    def _import_dropped_modpack(self, path):
        try:
            self.show_download_section('modpack')
            self.version_page._start_modpack_import(path, '')
        except Exception as exc:
            QMessageBox.warning(self, '导入整合包失败', str(exc))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Enter:
            if self._is_sidebar_widget(obj):
                self._expand_sidebar()
        elif event.type() == QEvent.Leave:
            if self._is_sidebar_widget(obj):
                self._sidebar_collapse_timer.start()
        if event.type() in (QEvent.DragEnter, QEvent.DragMove):
            if self._modpack_paths_from_mime(event.mimeData()):
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Drop:
            paths = self._modpack_paths_from_mime(event.mimeData())
            if paths:
                event.acceptProposedAction()
                self._import_dropped_modpack(paths[0])
                return True
        if event.type() == QEvent.ApplicationDeactivate:
            self._pause_background_media()
        elif event.type() == QEvent.ApplicationActivate:
            self._resume_background_media()
        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                self._pause_background_media()
            elif QApplication.applicationState() == Qt.ApplicationActive:
                self._resume_background_media()
        super().changeEvent(event)

    def _apply_ui_depth(self):
        '''GUI 深度只压暗界面控件，不改变最底层背景图片。'''

        try:
            depth = max(0, min(100, int(get_settings().get('ui_depth', 0))))
        except Exception:
            depth = 0
        if depth <= 0:
            self.body.setGraphicsEffect(None)
            return
        effect = QGraphicsColorizeEffect(self.body)
        effect.setColor(QColor('#000000'))
        effect.setStrength(min(0.6, depth / 100.0 * 0.6))
        self.body.setGraphicsEffect(effect)

    def _on_avatar_failed(self):
        '''头像加载失败：启动后延迟几秒再提示，避免打断进入流程。'''

        if self._avatar_fail_toast_shown:
            return
        self._avatar_fail_toast_shown = True

        def notify():
            try:
                from ui.widgets.toast import show_toast
                show_toast('头像加载失败，请检查网络连接', 'error', self)
            except Exception:
                pass

        QTimer.singleShot(3000, notify)

    def refresh_account_card(self):
        if not hasattr(self, 'account_page'):
            return
        config = self.account_page.config
        login_type = config.get('login_type', 'offline')
        name = ''
        mode = ''
        mode_kind = 'offline'
        urls = []
        skin_path = None
        head_path = None
        if login_type == 'microsoft':
            account = config.get('microsoft_account') or {}
            name = account.get('name') or ''
            mode = '正版登录'
            mode_kind = 'microsoft'
            if name:
                try:
                    from core.skin import microsoft_skin_cache_path
                    cached = microsoft_skin_cache_path(account.get('uuid', ''), name)
                    if cached.is_file():
                        skin_path = str(cached)
                except Exception:
                    skin_path = None
                if not skin_path:
                    try:
                        from core.skin import avatar_urls_for_account
                        urls = avatar_urls_for_account(account)
                    except Exception:
                        urls = [account.get('skin_url') or '', account.get('avatar_url') or '']
                        urls = [url for url in urls if url]
        elif login_type == 'thirdparty':
            account = config.get('thirdparty_account') or {}
            name = account.get('name') or ''
            server_name = str(account.get('server_name') or '')
            account_api = str(account.get('api') or '')
            if 'littleskin.cn' in account_api.lower() or 'littleskin' in server_name.lower():
                mode = 'LittleSkin 登录'
                mode_kind = 'littleskin'
            else:
                mode = server_name or '第三方登录'
                mode_kind = 'thirdparty'
            try:
                from core.skin import thirdparty_skin_cache_path
                cached = thirdparty_skin_cache_path(account.get('uuid', ''), name)
                if cached.is_file():
                    skin_path = str(cached)
            except Exception:
                skin_path = None
            if not skin_path:
                try:
                    from core.skin import avatar_urls_for_account
                    urls = avatar_urls_for_account(account)
                except Exception:
                    avatar_url = account.get('avatar_url') or ''
                    if avatar_url:
                        urls.append(avatar_url)
        else:
            name = config.get('player_name', '') or ''
            mode = '离线登录' if name else ''
            mode_kind = 'offline'
            head_path = None
            if name:
                try:
                    from core.skin import SkinManager
                    preview = SkinManager().preview_path()
                    if preview and preview.is_file():
                        skin_path = str(preview)
                except Exception:
                    skin_path = None
                if not skin_path:
                    urls = [f'https://mc-heads.net/avatar/{name}/96', f'https://minotar.net/helm/{name}/96.png']
            else:
                steve_head = Path(__file__).resolve().parent / 'imgs' / 'steve_head.png'
                if steve_head.is_file():
                    head_path = str(steve_head)
                else:
                    urls = ['https://mc-heads.net/avatar/MHF_Steve/96', 'https://minotar.net/helm/MHF_Steve/96.png']
        avatar_cache = None
        try:
            if login_type in ('microsoft', 'thirdparty'):
                from core.paths import scl_dir
                key = str(account.get('uuid') or name or 'unknown')
                avatar_cache = scl_dir() / 'icon_cache' / f'{login_type}_avatar_{key}.png'
        except Exception:
            avatar_cache = None
        self.account_card.set_account(
            name or '未登录', mode, urls, skin_path, head_path, avatar_cache,
            mode_kind=mode_kind,
        )

    def _on_version_selected(self, version_id):
        self.home_page.refresh_versions()
        self._select_nav(0)
        self.page_stack.setCurrentWidget(self.home_page)

    def _on_home_game_dir_changed(self, path):
        try:
            self.version_page.sync_game_dir()
        except Exception:
            pass

    def _on_settings_changed(self):
        '''全局设置里切换了游戏目录等：同步首页与版本页的版本列表。'''

        try:
            mc_dir = self.settings_page.config.get_mc_dir()
        except Exception:
            return

        def sync():
            try:
                self.home_page.reload_game_dir(mc_dir)
            except Exception:
                pass
            try:
                self.version_page.sync_game_dir()
            except Exception:
                pass

        # 延后执行，避免在设置页下拉框事件处理中重建列表
        QTimer.singleShot(0, sync)

    def _on_game_dir_changed(self, path):
        try:
            self.home_page.reload_game_dir(path)
        except Exception:
            pass
        try:
            if hasattr(self.settings_page, '_refresh_dir_list'):
                self.settings_page._refresh_dir_list()
            return
        except Exception:
            pass

    def open_game_settings(self, version_id):
        if self.game_settings_page:
            self.page_stack.removeWidget(self.game_settings_page)
            self.game_settings_page.deleteLater()
            self.game_settings_page = None
        from ui.pages.game_settings_page import GameSettingsPage
        self.game_settings_page = GameSettingsPage(version_id, self)
        self.game_settings_page.back_requested.connect(self.close_game_settings)
        self.game_settings_page.version_meta_changed.connect(
            (lambda _vid: self.home_page.refresh_versions())
        )
        self.game_settings_page.global_settings_requested.connect(
            self._open_global_settings_from_version
        )
        self.page_stack.addWidget(self.game_settings_page)
        self.page_stack.setCurrentWidget(self.game_settings_page)

    def _open_global_settings_from_version(self):
        self.close_game_settings()
        self._switch_page(3)

    def close_game_settings(self):
        if self.game_settings_page:
            try:
                self.game_settings_page.shutdown()
            except Exception:
                pass
            self.page_stack.removeWidget(self.game_settings_page)
            self.game_settings_page.deleteLater()
            self.game_settings_page = None
            try:
                self.version_page._load_installed_versions()
            except Exception:
                pass
            try:
                self.home_page.refresh_versions()
            except Exception:
                pass
            self._select_nav(0)
            self.page_stack.setCurrentWidget(self.home_page)

    def _refresh_download_bar(self):
        self.download_bar.set_snapshot(self.download_manager.snapshot())

    def show_download_manager_page(self):
        if not hasattr(self, 'download_manager_page'):
            return
        current = self.page_stack.currentWidget()
        if current is self.download_manager_page:
            return
        self._download_return_widget = current
        self.page_stack.setCurrentWidget(self.download_manager_page)

    def close_download_manager_page(self):
        target = self._download_return_widget
        self._download_return_widget = None
        if target is not None and self.page_stack.indexOf(target) >= 0:
            self.page_stack.setCurrentWidget(target)
            return
        self._select_nav(0)
        self.page_stack.setCurrentWidget(self.home_page)

    def _on_page_changed(self, current, previous=None):
        if current is None:
            return
        data = current.data(Qt.UserRole)
        if not data:
            return
        page_index, section = data
        self.page_stack.setCurrentIndex(page_index)
        if page_index == 0:
            self.home_page.refresh_versions()
            return
        if page_index == 1 and section:
            self.version_page.show_section(section)

    def _style_current_page(self, _index=None):
        manager = getattr(self, 'theme_manager', None)
        if manager is None:
            return
        widget = self.page_stack.currentWidget()
        if widget is None:
            return
        QTimer.singleShot(0, lambda w=widget: manager.style_widget_tree(w))

    def _animate_page_change(self, index: int):
        '''页面切换时用暗色遮罩淡出，避免页面半透明时透出背景视频。'''

        if not self.isVisible():
            return
        if self.page_stack.widget(index) is None:
            return
        if self._page_animation is not None:
            try:
                self._page_animation.stop()
            except RuntimeError:
                pass
        if self._page_fade_overlay is not None:
            try:
                self._page_fade_overlay.deleteLater()
            except RuntimeError:
                pass
            self._page_fade_overlay = None
        overlay = QWidget(self.page_stack)
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        overlay.setAttribute(Qt.WA_StyledBackground, True)
        overlay.setStyleSheet('background: rgba(15,17,23,225);')
        overlay.setGeometry(self.page_stack.rect())
        overlay.show()
        overlay.raise_()
        effect = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', overlay)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        def finish():
            if self._page_fade_overlay is overlay:
                self._page_fade_overlay = None
            try:
                overlay.deleteLater()
            except RuntimeError:
                pass

        anim.finished.connect(finish)
        self._page_animation = anim
        self._page_fade_overlay = overlay
        anim.start()

    def _switch_page(self, index):
        if index == 1:
            self.show_download_section('available')
            return
        self._select_nav(index)
        self.page_stack.setCurrentIndex(index)
        if index == 2:
            try:
                login_type = self.account_page.config.get('login_type', 'offline')
                if login_type == 'offline':
                    self.account_page.focus_offline_name()
                return
            except Exception:
                pass

    def start_fade_anim(self):
        self.setWindowOpacity(0)
        self._fade_anim = QPropertyAnimation(self, b'windowOpacity')
        self._fade_anim.setDuration(600)
        self._fade_anim.setStartValue(0)
        self._fade_anim.setEndValue(1)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        def anim_finish():
            self.raise_()
            self.activateWindow()
            if self._fade_anim:
                self._fade_anim.deleteLater()
                self._fade_anim = None

        self._fade_anim.finished.connect(anim_finish)
        self._fade_anim.start()
        QTimer.singleShot(2000, self.force_restore)
        QTimer.singleShot(1500, self._on_startup_update_notice)

    def force_restore(self):
        if self.windowState() & Qt.WindowMinimized:
            self.setWindowState(Qt.WindowNoState)
        self.raise_()
        self.activateWindow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self.size_grip.move(max(0, self.central.width() - 18), max(0, self.central.height() - 18))
            self.size_grip.raise_()
        except Exception:
            pass
        try:
            self.title_bar.setFixedWidth(self.central.width())
        except Exception:
            pass
        try:
            self.border_overlay.setGeometry(self.central.rect())
            self.border_overlay.raise_()
        except Exception:
            pass
        self._update_sidebar_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, '_fade_in_done', False):
            return
        self._fade_in_done = True
        try:
            self.border_overlay.setGeometry(self.central.rect())
            self.border_overlay.raise_()
        except Exception:
            pass
        self._update_sidebar_geometry()
        if hasattr(self, 'theme_manager'):
            QTimer.singleShot(0, self.theme_manager.style_visible_widgets)
        if not self._music_auto_started:
            self._music_auto_started = True
            QTimer.singleShot(800, self._auto_start_music)
        try:
            self.setWindowOpacity(0.0)
            animation = QPropertyAnimation(self, b'windowOpacity', self)
            animation.setDuration(520)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.finished.connect(lambda: self.setWindowOpacity(1.0))
            self._fade_in_animation = animation
            animation.start()
        except Exception:
            self.setWindowOpacity(1.0)

    def _auto_start_music(self):
        '''进入启动器后自动播放背景音乐；视频音乐开启时让位给视频。'''

        try:
            if not self.music_player.autoplay_enabled():
                return
            if not self.music_player.has_tracks():
                return
            if self.central.video_audio_enabled():
                return
            if self.music_player.is_playing() or self.music_player.user_paused():
                return
            self.music_player.set_muted(False)
            self.music_player.play()
            self._update_media_buttons()
            QTimer.singleShot(350, self._update_media_buttons)
        except Exception:
            pass

    def _on_startup_update_notice(self):
        try:
            from core.update import consume_updated_marker
            version = consume_updated_marker()
        except Exception:
            version = ''
        if version:
            from ui.widgets.toast import show_toast
            show_toast(f'已成功更新到: {version}', 'success', self, 5000)
        QTimer.singleShot(2500, (lambda: self._check_updates(manual=False)))

    def _check_updates(self, manual=False):
        from core.update import CheckUpdateWorker
        if self._update_checker is not None and self._update_checker.isRunning():
            return
        if self._update_dialog is not None and self._update_dialog.isVisible():
            return
        if manual and hasattr(self.about_page, 'check_update_btn'):
            self.about_page.check_update_btn.setEnabled(False)
            self.about_page.check_update_btn.setText('检查中...')
        worker = CheckUpdateWorker(manual=manual, parent=self)
        self._update_checker = worker
        worker.done.connect(self._on_update_checked)
        worker.finished.connect((lambda w=worker: self._on_update_checker_finished(w)))
        worker.start()

    def _on_update_checker_finished(self, worker):
        if self._update_checker is worker:
            self._update_checker = None
        worker.deleteLater()

    def _on_update_checked(self, info, error, manual):
        if hasattr(self.about_page, 'check_update_btn'):
            self.about_page.check_update_btn.setEnabled(True)
            self.about_page.check_update_btn.setText('检查更新')
        if error:
            if manual:
                QMessageBox.warning(self, '检查更新失败', '无法获取最新版本信息：' + chr(10) + str(error))
            return
        from core.update import CURRENT_VERSION, update_kind
        if not info:
            if manual:
                QMessageBox.information(self, '检查更新', '暂无可用 Release。')
            return
        kind = update_kind(info)
        if kind == 'none':
            if manual:
                QMessageBox.information(self, '检查更新', f'当前已经是最新版本：V{CURRENT_VERSION}（正式版）')
            return
        if manual:
            self._show_update_dialog(info, kind)
            return
        self._pending_update_info = dict(info)
        self._pending_update_kind = kind
        QTimer.singleShot(1200, (lambda i=dict(info), k=kind: self._show_update_toast(i, k)))

    def _show_update_toast(self, info, kind='version'):
        '''自动检查到新版本时先显示可点击通知，不直接弹出下载窗口。'''

        if not info or not info.get('asset_url'):
            return
        if self._update_dialog is not None and self._update_dialog.isVisible():
            return
        tag = str(info.get('tag') or info.get('version') or '')
        if tag and tag == self._update_notified_tag:
            return
        self._update_notified_tag = tag
        self._pending_update_info = dict(info)
        self._pending_update_kind = kind
        version = str(info.get('version') or info.get('tag') or '').strip()
        if version[:1].lower() == 'v':
            version = version[1:]
        from ui.widgets.toast import show_toast
        if kind == 'dll':
            text = '检测到修复了一些bug，点击下载更新！'
        else:
            text = f'当前检测到最新版本V{version}，点击下载更新！'
        show_toast(
            text,
            'update',
            self,
            15000,
            self._open_pending_update,
        )

    def _open_pending_update(self):
        info = self._pending_update_info
        if not info:
            return
        self._show_update_dialog(dict(info), self._pending_update_kind)

    def _show_update_dialog(self, info, kind='version'):
        from ui.widgets.update_dialog import UpdateDialog
        if self._update_dialog is not None and self._update_dialog.isVisible():
            self._update_dialog.raise_()
            self._update_dialog.activateWindow()
            return
        tag = str(info.get('tag') or info.get('version') or '')
        if tag:
            self._update_notified_tag = tag
        self._pending_update_info = None
        dialog = UpdateDialog(self, info, kind)
        dialog.download_completed.connect(self._on_update_downloaded)
        self._update_dialog = dialog
        try:
            accepted = dialog.exec() == dialog.Accepted
        finally:
            self._update_dialog = None
        if not dialog.asset_path:
            if accepted:
                from ui.widgets.toast import show_toast
                show_toast('更新安装包不可用，请重新下载更新。', 'error', self, 6000)
            return
        if not accepted:
            # 关闭弹窗只代表稍后更新；保留已下载的安装包，不启动替换。
            self._update_prepared = False
            self._update_prepared_tag = ''
            return
        version = info.get('version') or info.get('tag') or ''
        try:
            from core.update import write_update_log
            write_update_log(
                f'ui: restart requested asset={dialog.asset_path} version={version}'
            )
        except Exception:
            pass
        self._quit_for_update(dialog.asset_path, version)

    def _on_update_downloaded(self, asset_path, version):
        '''下载完成即自动安装，不再依赖用户点击重启按钮或弹窗保持打开。'''

        asset_path = str(asset_path or '')
        if not asset_path:
            return
        try:
            from core.update import write_update_log
            write_update_log(
                f'ui: download complete, auto install asset={asset_path} version={version}'
            )
        except Exception:
            pass
        try:
            from ui.widgets.toast import show_toast
            show_toast('更新已下载，正在自动重启安装...', 'update', self, 4000)
        except Exception:
            pass
        QTimer.singleShot(
            800,
            lambda path=asset_path, ver=str(version or ''): self._auto_install_update(path, ver),
        )

    def _auto_install_update(self, asset_path, version):
        if getattr(self, '_closing', False):
            return
        try:
            from core.update import write_update_log
            write_update_log(
                f'ui: auto install starting asset={asset_path} version={version}'
            )
        except Exception:
            pass
        self._quit_for_update(asset_path, version)

    def _quit_for_update(self, asset_path='', version=''):
        '''优先在当前进程内替换 exe 并启动新版本，失败时回退到外部更新脚本。'''

        self._closing = True
        try:
            from core.single_instance import release_single_instance
            release_single_instance()
        except Exception:
            pass
        if asset_path:
            try:
                from core.update import apply_update_inplace
                if apply_update_inplace(asset_path, version):
                    # 新进程已经创建；立即退出，避免旧进程继续占用 exe / 单实例锁。
                    os._exit(0)
            except Exception as exc:
                self._update_prepare_error = str(exc)
                self._update_prepared = False
                try:
                    from core.update import write_update_log
                    write_update_log(f'ui: inplace failed, trying external updater: {exc}')
                except Exception:
                    pass
            try:
                from core.update import prepare_update
                prepare_update(asset_path, version)
                self._update_prepared = True
                self._update_prepared_tag = str(version)
                self._update_prepare_error = ''
            except Exception as exc:
                self._update_prepare_error = str(exc)
                self._closing = False
                try:
                    from core.single_instance import acquire_single_instance
                    acquire_single_instance()
                except Exception:
                    pass
                QMessageBox.critical(self, '更新失败', f'无法启动更新程序：{exc}')
                return False
        # 外部更新脚本正在等待本进程退出；直接结束进程，
        # 避免 Qt 事件循环退出后仍有线程让窗口残留、看似“没有重启”。
        os._exit(0)

    def closeEvent(self, event):
        if getattr(self, '_closing', False):
            super().closeEvent(event)
            return
        try:
            active = []
            if self.version_page and self.version_page.has_active_tasks():
                active.append('资源/版本下载或整合包导入')
            repair_active = False
            if self.game_settings_page:
                repair_active = self.game_settings_page.has_active_repair()
            if not repair_active:
                from ui.pages.game_settings_page import has_active_repairs
                repair_active = has_active_repairs()
            if repair_active:
                active.append('游戏文件修复')
            if active:
                reply = QMessageBox.question(self, '确定退出？', '以下任务尚未完成：\n- ' + '\n- '.join(active) + '\n\n确定要退出启动器吗？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply != QMessageBox.Yes:
                    event.ignore()
                    return
            self._closing = True
            from core.single_instance import release_single_instance
            release_single_instance()
            import threading
            timer = threading.Timer(1.0, lambda: os._exit(0))
            timer.daemon = True
            timer.start()
            if self.version_page:
                self.version_page.shutdown()
            if self.home_page:
                self.home_page.shutdown()
            if self.account_page:
                self.account_page.shutdown()
            if self.settings_page:
                self.settings_page.shutdown()
            if self.game_settings_page:
                self.game_settings_page.close()
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass
        super().closeEvent(event)


