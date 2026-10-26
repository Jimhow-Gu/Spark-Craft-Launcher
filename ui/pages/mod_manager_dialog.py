'''版本独立 Mod 管理页面'''

from __future__ import annotations
import json
import re
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import (Qt, QThread, Signal, QUrl, QSize)
from PySide6.QtGui import (QColor, QFont, QPainter, QPixmap, QDesktopServices)
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QMessageBox, QFrame, QWidget, QLineEdit, QFileDialog)
from core.app_font import ui_font
from core.launch import LaunchManager
from core.net import HttpClient, hash_file
from ui.widgets.back_button import BackButton
from ui.widgets.loading_overlay import LoadingOverlay

def _toml_value(text, key):
    match = re.search(f'(?m)^\\s*{re.escape(key)}\\s*=\\s*"([^"]*)"', text)
    if match:
        return match.group(1)
    return ''

def _manifest_version(zf):
    try:
        names = {name.lower(): name for name in zf.namelist()}
        manifest = names.get('meta-inf/manifest.mf')
        if not manifest:
            return ''
        text = zf.read(manifest).decode('utf-8', 'replace')
        text = text.replace('\r\n ', '').replace('\n ', '')
        match = re.search('(?im)^Implementation-Version:\\s*(.+)$', text)
        if match:
            return match.group(1).strip()
        return ''
    except Exception:
        return ''

def _version_from_filename(filename):
    stem = Path(filename).name
    if stem.lower().endswith('.disabled'):
        stem = stem[:-len('.disabled')]
    if stem.lower().endswith('.jar'):
        stem = stem[:-4]
    matches = re.findall('(?<!\\d)(\\d+(?:\\.\\d+){1,3}(?:[-+][0-9A-Za-z._-]+)?)', stem)
    if matches:
        return matches[-1]
    return ''

def _clean_mod_version(version, zf, filename):
    value = str(version or '')
    if '${file.jarVersion}' in value:
        replacement = _manifest_version(zf) or _version_from_filename(filename)
        value = value.replace('${file.jarVersion}', replacement)
    if '${' in value:
        value = _manifest_version(zf) or _version_from_filename(filename)
    if not value.strip():
        value = _manifest_version(zf) or _version_from_filename(filename)
    return value.strip()

def _extract_icon(zf, icon_name):
    name = (icon_name or '').strip().lstrip('/')
    if not name:
        return b''
    try:
        return zf.read(name)
    except Exception:
        return b''

def scan_mod_file(path):
    disabled = path.name.lower().endswith('.disabled')
    base_name = path.name
    if disabled:
        base_name = base_name[:-len('.disabled')]
    fallback = base_name[:-4] if base_name.lower().endswith('.jar') else base_name
    info = {'path': path, 'name': fallback, 'version': '', 'modid': '', 'description': '', 'loader': '', 'homepage': '', 'icon': b'', 'enabled': not disabled, 'filename': path.name}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if 'fabric.mod.json' in names or 'quilt.mod.json' in names:
                meta_name = 'fabric.mod.json' if 'fabric.mod.json' in names else 'quilt.mod.json'
                meta = json.loads(zf.read(meta_name).decode('utf-8', 'replace'))
                info['loader'] = 'Fabric' if meta_name == 'fabric.mod.json' else 'Quilt'
                info['name'] = meta.get('name') or meta.get('id') or fallback
                info['version'] = str(meta.get('version') or '')
                info['modid'] = str(meta.get('id') or '')
                desc = meta.get('description') or ''
                info['description'] = str(desc) if not isinstance(desc, dict) else str(desc.get('en', ''))
                contact = meta.get('contact') or {}
                if isinstance(contact, dict):
                    info['homepage'] = contact.get('homepage') or contact.get('sources') or ''
                icon = meta.get('icon')
                if isinstance(icon, dict):
                    icon = icon.get('128') or icon.get('64') or next(iter(icon.values()), '')
                info['icon'] = _extract_icon(zf, icon)
            elif 'META-INF/neoforge.mods.toml' in names or 'META-INF/mods.toml' in names:
                meta_name = 'META-INF/neoforge.mods.toml' if 'META-INF/neoforge.mods.toml' in names else 'META-INF/mods.toml'
                text = zf.read(meta_name).decode('utf-8', 'replace')
                info['loader'] = 'NeoForge' if 'neoforge' in meta_name.lower() else 'Forge'
                info['modid'] = _toml_value(text, 'modId')
                info['name'] = _toml_value(text, 'displayName') or info['modid'] or fallback
                info['version'] = _toml_value(text, 'version')
                info['description'] = _toml_value(text, 'description')
                logo = _toml_value(text, 'logoFile')
                if not logo:
                    logo = _toml_value(text, 'logoBlur')
                info['icon'] = _extract_icon(zf, logo)
            elif 'mcmod.info' in names:
                data = json.loads(zf.read('mcmod.info').decode('utf-8', 'replace'))
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    info['loader'] = 'Forge'
                    info['name'] = data.get('name') or fallback
                    info['version'] = str(data.get('version') or '')
                    info['modid'] = str(data.get('modid') or '')
                    info['description'] = str(data.get('description') or '')
                    info['homepage'] = str(data.get('url') or '')
                    info['icon'] = _extract_icon(zf, data.get('logoFile') or '')
            if not info['loader']:
                if any((n.lower().startswith('fabric.mod.json') for n in names)):
                    info['loader'] = 'Fabric'
                elif any(('neoforge.mods.toml' in n.lower() for n in names)):
                    info['loader'] = 'NeoForge'
                elif any(('mods.toml' in n.lower() for n in names)):
                    info['loader'] = 'Forge'
            info['version'] = _clean_mod_version(info.get('version', ''), zf, base_name)
            return info
        return info
    except Exception:
        return info

class ModScanWorker(QThread):
    done = Signal(list)
    progress = Signal(int)
    error = Signal(str)
    def __init__(self, mods_dir):
        super().__init__()
        self.mods_dir = Path(mods_dir)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        result = []
        try:
            files = []
            if self.mods_dir.exists():
                for path in sorted(self.mods_dir.iterdir(), key=(lambda p: p.name.lower())):
                    if self._cancelled:
                        return
                    if not path.is_file():
                        continue
                    lower = path.name.lower()
                    if not (lower.endswith('.jar') or lower.endswith('.jar.disabled')):
                        continue
                    files.append(path)
            total = max(1, len(files))
            self.progress.emit(0)
            for index, path in enumerate(files):
                if self._cancelled:
                    return
                result.append(scan_mod_file(path))
                self.progress.emit(int((index + 1) * 100 / total))
        except Exception as exc:
            if not self._cancelled:
                self.error.emit(str(exc))
            return
        if not self._cancelled:
            self.done.emit(result)


class ModUpdateWorker(QThread):
    '''通过 Modrinth 文件哈希检测适配当前版本的新 Mod。'''

    done = Signal(dict)
    status = Signal(str)
    def __init__(self, mods, mc_version, loader):
        super().__init__()
        self.mods = list(mods)
        self.mc_version = str(mc_version or '')
        self.loader = str(loader or '')
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _check_one(self, info):
        if self._cancelled:
            return
        path = Path(info.get('path', ''))
        if not path.is_file():
            return
        try:
            digest = hash_file(path, 'sha1')
        except Exception:
            return
        if not digest:
            return
        from core.mods import ModrinthSource
        source = ModrinthSource()
        try:
            try:
                update = source.find_update(digest, self.mc_version, self.loader)
            except Exception:
                update = None
        finally:
            try:
                source.http.close()
            except Exception:
                pass
        if update:
            return (str(path), update)
        return None

    def run(self):
        results = {}
        total = len(self.mods)
        completed = 0
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(self._check_one, info) for info in self.mods]
                for future in as_completed(futures):
                    if self._cancelled:
                        break
                    completed += 1
                    self.status.emit(f'正在检查 Mod 更新 {completed}/{total}')
                    try:
                        result = future.result()
                    except Exception:
                        result = None
                    if result:
                        results[result[0]] = result[1]
        finally:
            if self._cancelled:
                self.status.emit('已取消检查 Mod 更新')
            self.done.emit(results)


class ModUpdateDownloadWorker(QThread):
    '''下载并替换检测到的新版本 Mod。'''

    status = Signal(str)
    done = Signal(int, list)
    def __init__(self, items, mods_dir):
        super().__init__()
        self.items = list(items)
        self.mods_dir = Path(mods_dir)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def _download_urls(url):
        urls = []
        if 'cdn.modrinth.com/' in url:
            path = url.split('cdn.modrinth.com/', 1)[1]
            urls.append(f'https://mod.mcimirror.top/{path}')
            urls.append(f'https://cdn-raw.modrinth.com/{path}')
        if url and url not in urls:
            urls.append(url)
        return urls

    def run(self):
        http = HttpClient(timeout=120, retries=3)
        updated = 0
        errors = []
        try:
            for info in self.items:
                if self._cancelled:
                    break
                latest = info.get('latest_file') or {}
                url = str(latest.get('url') or '')
                if not url:
                    continue
                old_path = Path(info.get('path', ''))
                filename = Path(str(latest.get('filename') or old_path.name)).name
                disabled = old_path.name.lower().endswith('.disabled')
                if disabled and filename.lower().endswith('.jar'):
                    filename += '.disabled'
                dest = self.mods_dir / filename
                self.status.emit(f'正在更新 {info.get("name") or old_path.name} ...')
                ok = False
                last_error = ''
                for candidate in self._download_urls(url):
                    if self._cancelled:
                        break
                    try:
                        http.download(candidate, dest, sha1=latest.get('sha1') or None, size=latest.get('size') or None, cancel=(lambda: self._cancelled), allow_slow_fallback=False)
                        ok = True
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        continue
                if not ok:
                    errors.append(f'{info.get("name") or old_path.name}: {last_error}')
                    continue
                if old_path != dest and old_path.exists():
                    try:
                        old_path.unlink()
                    except OSError:
                        pass
                updated += 1
        finally:
            try:
                http.close()
            except Exception:
                pass
            self.done.emit(updated, errors)


class ModRowWidget(QWidget):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(10)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(48, 48)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet('background: rgba(255,255,255,0.05); border-radius: 7px;')
        self.icon_label.setPixmap(self._make_icon())
        layout.addWidget(self.icon_label)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(f'{info.get("name") or info.get("filename")}    {info.get("version", "")}')
        title.setStyleSheet('color:#f0f0f0; font-size:13px; font-weight:600;')
        title.setWordWrap(True)
        title.setToolTip(f'{info.get("name") or info.get("filename")}\n{info.get("filename", "")}\n{info.get("description", "")}')
        text.addWidget(title)
        tags = info.get('loader') or 'Mod'
        desc = info.get('description') or ''
        detail = f'[{tags}]  {info.get("filename", "")}'
        if desc:
            detail += f'：{desc[:160]}'
        if info.get('update_available'):
            detail += f'    |    可更新至 {info.get("latest_version", "")}'
        detail_label = QLabel(detail)
        detail_label.setStyleSheet('color:rgba(255,255,255,0.48); font-size:11px;')
        detail_label.setWordWrap(True)
        detail_label.setToolTip(detail)
        text.addWidget(detail_label)
        layout.addLayout(text, 1)
        if not info.get('enabled', True):
            self.setStyleSheet('background: rgba(255,255,255,0.02);')
            title.setStyleSheet('color:rgba(255,255,255,0.55); font-size:13px; font-weight:600;')
            return
        self.setStyleSheet('background: transparent;')

    def _make_icon(self):
        size = 48
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        raw = self.info.get('icon') or b''
        source = QPixmap()
        if raw:
            source.loadFromData(raw)
        if source.isNull():
            color = QColor.fromHsv(abs(hash(self.info.get('name', ''))) % 360, 130, 150)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(0, 0, size, size, 7, 7)
            painter.setPen(QColor('#ffffff'))
            painter.setFont(ui_font(18, QFont.Bold))
            letter = (self.info.get('name') or '?')[:1].upper()
            painter.drawText(pix.rect(), Qt.AlignCenter, letter)
        else:
            scaled = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (size - scaled.width()) // 2
            y = (size - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        if not self.info.get('enabled', True):
            cx, cy, r = size - 10, size - 10, 9
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#d32f2f'))
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            painter.setPen(QColor('#ffffff'))
            painter.setBrush(Qt.NoBrush)
            painter.setFont(ui_font(12, QFont.Bold))
            painter.drawText(cx - r, cy - r, r * 2, r * 2, Qt.AlignCenter, '−')
        painter.end()
        return pix


class ModManagerPage(QWidget):
    back_requested = Signal()
    def __init__(self, version_id, parent=None, mods_dir=None, embedded=False):
        super().__init__(parent)
        self.version_id = version_id
        self._embedded = bool(embedded)
        self.launch_mgr = LaunchManager()
        self.mods_dir = Path(mods_dir) if mods_dir else self.launch_mgr.get_mods_dir(version_id)
        self._all_mods = []
        self._filter = 'all'
        self._scan_worker = None
        settings = self.launch_mgr.get_version_settings(version_id) or {}
        disabled = settings.get('disable_mod_update')
        if disabled is None or disabled == '':
            disabled = self.launch_mgr.config.get('disable_mod_update', False)
        self._mod_update_disabled = bool(disabled)
        self._update_worker = None
        self._update_download_worker = None
        self.setMinimumSize(640, 480)
        self.setStyleSheet('background:transparent; color:#f0f0f0;')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0 if self._embedded else 18, 8 if self._embedded else 14, 0 if self._embedded else 18, 10 if self._embedded else 14)
        layout.setSpacing(8)
        if not self._embedded:
            top_bar = QHBoxLayout()
            back_btn = BackButton()
            back_btn.clicked.connect((lambda: self.back_requested.emit()))
            page_title = QLabel(f'Mod 管理 - {version_id}')
            page_title.setFont(ui_font(18, QFont.DemiBold))
            page_title.setStyleSheet('color:#f0f0f0;')
            top_bar.addWidget(back_btn)
            top_bar.addWidget(page_title)
            top_bar.addStretch(1)
            layout.addLayout(top_bar)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索 Mod 名称 / 描述 / 标签')
        self.search_input.setMinimumHeight(34)
        self.search_input.setStyleSheet('\n            QLineEdit { background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12);\n                        border-radius:7px; color:#f0f0f0; padding:5px 10px; }\n        ')
        self.search_input.textChanged.connect((lambda _t: self._populate()))
        layout.addWidget(self.search_input)
        header = QHBoxLayout()
        open_btn = QPushButton('打开文件夹')
        open_btn.setStyleSheet(self._btn_style())
        open_btn.clicked.connect(self._open_folder)
        install_btn = QPushButton('从文件安装')
        install_btn.setStyleSheet(self._btn_style())
        install_btn.clicked.connect(self._install_from_file)
        download_btn = QPushButton('下载新 Mod')
        download_btn.setStyleSheet(self._btn_style())
        download_btn.clicked.connect(self._open_mod_download)
        select_all_btn = QPushButton('全选')
        select_all_btn.setStyleSheet(self._btn_style())
        select_all_btn.clicked.connect(self.list_select_all)
        refresh_btn = QPushButton('刷新')
        refresh_btn.setStyleSheet(self._btn_style())
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(open_btn)
        header.addWidget(install_btn)
        header.addWidget(download_btn)
        header.addWidget(select_all_btn)
        header.addStretch(1)
        header.addWidget(refresh_btn)
        layout.addLayout(header)
        self.path_label = QLabel(str(self.mods_dir))
        self.path_label.setStyleSheet('color:rgba(255,255,255,0.45); font-size:11px;')
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        tab_row = QHBoxLayout()
        self.all_tab = QPushButton('全部 (0)')
        self.update_tab = QPushButton('可更新 (0)')
        self.disabled_tab = QPushButton('已禁用 (0)')
        for btn in self.all_tab, self.update_tab, self.disabled_tab:
            btn.setCheckable(True)
            btn.setStyleSheet('\n                QPushButton { background:transparent; color:rgba(255,255,255,0.6);\n                              border:none; padding:7px 15px; font-size:13px; }\n                QPushButton:checked { color:white; border-bottom:2px solid #0078d4; }\n            ')
        self.all_tab.setChecked(True)
        self.all_tab.clicked.connect((lambda: self._set_filter('all')))
        self.update_tab.clicked.connect((lambda: self._set_filter('update')))
        self.disabled_tab.clicked.connect((lambda: self._set_filter('disabled')))
        if self._mod_update_disabled:
            self.update_tab.setEnabled(False)
            self.update_tab.setToolTip('当前版本已开启「禁止更新Mod」')
        tab_row.addWidget(self.all_tab)
        tab_row.addWidget(self.update_tab)
        tab_row.addWidget(self.disabled_tab)
        tab_row.addStretch(1)
        layout.addLayout(tab_row)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.MultiSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setStyleSheet('\n            QListWidget { background: rgba(10,12,18,0.45);\n                          border:1px solid rgba(255,255,255,0.16); border-radius:8px; outline:none; }\n            QListWidget::item { border-bottom:1px solid rgba(255,255,255,0.06); }\n            QListWidget::item:selected { background: rgba(0,120,212,0.3); }\n            QListWidget::item:hover { background: rgba(255,255,255,0.06); }\n        ')
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list, 1)
        self.loading_overlay = LoadingOverlay(self.list.viewport())
        self.loading_overlay.attach_to(self.list.viewport())
        self.status_label = QLabel('正在扫描 Mod...')
        self.status_label.setStyleSheet('color:rgba(255,255,255,0.5); font-size:12px;')
        layout.addWidget(self.status_label)
        self.action_bar = QFrame()
        self.action_bar.setStyleSheet('QFrame { background:rgba(18,20,26,0.62); border:1px solid rgba(255,255,255,0.16); border-radius:8px; }')
        bar = QHBoxLayout(self.action_bar)
        bar.setContentsMargins(12, 8, 12, 8)
        self.selected_label = QLabel('已选择 0 个文件')
        self.selected_label.setStyleSheet('color:#ddd; font-size:12px;')
        bar.addWidget(self.selected_label)
        bar.addStretch(1)
        self.update_btn = QPushButton('更新')
        self.enable_btn = QPushButton('启用')
        self.disable_btn = QPushButton('禁用')
        self.delete_btn = QPushButton('删除')
        self.clear_btn = QPushButton('取消选择')
        for btn in self.update_btn, self.enable_btn, self.disable_btn, self.delete_btn, self.clear_btn:
            btn.setStyleSheet(self._btn_style())
        self.update_btn.clicked.connect(self._update_selected)
        if self._mod_update_disabled:
            self.update_btn.setEnabled(False)
            self.update_btn.setToolTip('当前版本已开启「禁止更新Mod」')
        self.enable_btn.clicked.connect((lambda: self._set_enabled(True)))
        self.disable_btn.clicked.connect((lambda: self._set_enabled(False)))
        self.delete_btn.clicked.connect(self._delete_selected)
        self.clear_btn.clicked.connect(self.list.clearSelection)
        bar.addWidget(self.update_btn)
        bar.addWidget(self.enable_btn)
        bar.addWidget(self.disable_btn)
        bar.addWidget(self.delete_btn)
        bar.addWidget(self.clear_btn)
        self.action_bar.hide()
        layout.addWidget(self.action_bar)
        self.refresh()

    def _btn_style(self):
        return 'QPushButton { background:#3a3a3a; color:white; border:none; border-radius:5px; padding:5px 12px; } QPushButton:hover { background:#4a4a4a; } QPushButton:disabled { background:#2a2a2a; color:#666; }'

    def _open_folder(self):
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.mods_dir)))

    def _install_from_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, '选择 Mod 文件', '', 'Minecraft Mod (*.jar);;所有文件 (*.*)')
        if not files:
            return
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        copied = 0
        for file_path in files:
            try:
                src = Path(file_path)
                dest = self.mods_dir / src.name
                if dest.exists():
                    errors.append(f'{src.name} 已存在')
                else:
                    shutil.copy2(src, dest)
                    copied += 1
                continue
            except OSError as exc:
                errors.append(f'{file_path}: {exc}')
                continue
        if errors:
            QMessageBox.warning(self, '部分安装失败', '\n'.join(errors[:8]))
        if copied:
            self.status_label.setText(f'已安装 {copied} 个 Mod')
        self.refresh()

    def _open_mod_download(self):
        window = self.window()
        if hasattr(window, 'show_download_section'):
            window.show_download_section('mod')
            self.back_requested.emit()
            return
        QMessageBox.information(self, '下载新 Mod', '请到下载中心 → 模组页面下载。')

    def list_select_all(self):
        self.list.selectAll()

    def refresh(self):
        self.list.clear()
        self.action_bar.hide()
        self.status_label.setText('')
        self.loading_overlay.start('正在扫描 Mod', 0)
        try:
            if self._scan_worker and self._scan_worker.isRunning():
                return
        except RuntimeError:
            self._scan_worker = None
        worker = ModScanWorker(self.mods_dir)
        worker.done.connect(self._on_scanned)
        worker.progress.connect(self.loading_overlay.set_progress)
        worker.error.connect(self._on_scan_error)
        worker.finished.connect((lambda w=worker: self._on_scan_finished(w)))
        self._scan_worker = worker
        worker.start()

    def _on_scan_error(self, message):
        if not self.loading_overlay.is_loading():
            return
        reason = str(message or '').strip() or '扫描 Mod 文件失败'
        self.loading_overlay.fail(reason)
        self.status_label.setText('')

    def _on_scan_finished(self, worker):
        if self._scan_worker is worker:
            self._scan_worker = None
        worker.deleteLater()

    def shutdown(self):
        try:
            self.loading_overlay.stop()
        except RuntimeError:
            pass
        for attr in ('_scan_worker', '_update_worker', '_update_download_worker'):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    worker.cancel()
                    worker.wait(3000)
            except RuntimeError:
                pass
            setattr(self, attr, None)

    def _on_scanned(self, mods):
        self.loading_overlay.finish()
        self._all_mods = mods
        for info in self._all_mods:
            info['update_available'] = False
            info.pop('latest_file', None)
            info.pop('latest_version', None)
        self.all_tab.setText(f'全部 ({len(mods)})')
        self.update_tab.setText('可更新 (0)')
        disabled_count = sum((1 for m in mods if not m.get('enabled', True)))
        self.disabled_tab.setText(f'已禁用 ({disabled_count})')
        self.status_label.setText(f'共 {len(mods)} 个 Mod：{self.mods_dir}')
        self._populate()
        if self._mod_update_disabled:
            self.status_label.setText('当前版本已开启「禁止更新Mod」')
            return
        self._start_update_check()

    def _version_context(self):
        '''解析当前实例对应的原版版本号与加载器。'''

        from core.version import detect_version_loader
        mc_dir = Path(self.launch_mgr.config.get_mc_dir())
        versions_dir = mc_dir / 'versions'
        version_dir = versions_dir / self.version_id
        data = {}
        try:
            data = json.loads((version_dir / f'{self.version_id}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        loader = detect_version_loader(self.version_id, data, version_dir)
        mc_version = self.version_id
        current_data = data
        seen = set()
        for _ in range(10):
            if not isinstance(current_data, dict):
                break
            parent = str(current_data.get('inheritsFrom') or '')
            if not parent or parent in seen:
                break
            seen.add(parent)
            parent_dir = versions_dir / parent
            try:
                parent_data = json.loads((parent_dir / f'{parent}.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                parent_data = {}
            if not loader:
                loader = detect_version_loader(parent, parent_data, parent_dir)
            mc_version = parent
            current_data = parent_data
        if loader == 'optifine':
            loader = ''
        lower = mc_version.lower()
        if any((key in lower for key in ('forge', 'fabric', 'neoforge', 'quilt', 'optifine'))):
            match = re.match('^(\\d+\\.\\d+(?:\\.\\d+)?)', mc_version)
            if match:
                mc_version = match.group(1)
        return mc_version, loader

    def _start_update_check(self):
        worker = self._update_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
        mc_version, loader = self._version_context()
        self.status_label.setText('正在检查 Mod 更新...')
        worker = ModUpdateWorker(self._all_mods, mc_version, loader)
        worker.status.connect(self.status_label.setText)
        worker.done.connect((lambda results, w=worker: self._on_updates_checked(results, w)))
        worker.finished.connect((lambda w=worker: self._on_update_check_finished(w)))
        self._update_worker = worker
        worker.start()

    def _on_update_check_finished(self, worker):
        if self._update_worker is worker:
            self._update_worker = None
        worker.deleteLater()

    def _on_updates_checked(self, results, worker=None):
        if worker is not None and self._update_worker is not None and worker is not self._update_worker:
            return
        for info in self._all_mods:
            update = results.get(str(info.get('path', '')))
            latest = (update or {}).get('latest') or {}
            if latest:
                info['update_available'] = True
                info['latest_file'] = latest
                info['latest_version'] = latest.get('version') or ''
            else:
                info['update_available'] = False
        update_count = sum((1 for m in self._all_mods if m.get('update_available')))
        self.update_tab.setText(f'可更新 ({update_count})')
        if update_count:
            self.status_label.setText(f'检测到 {update_count} 个 Mod 有适配当前版本的新版本')
        else:
            self.status_label.setText('未检测到适配当前版本的新版本')
        self._populate()

    def _set_filter(self, kind):
        self._filter = kind
        self.all_tab.setChecked(kind == 'all')
        self.update_tab.setChecked(kind == 'update')
        self.disabled_tab.setChecked(kind == 'disabled')
        self._populate(preserve_selection=False)

    def _populate(self, preserve_selection=True):
        selected_paths = set()
        if preserve_selection:
            selected_paths = {str(info.get('path')) for info in self._selected()}
        scroll_pos = self.list.verticalScrollBar().value()
        self.list.blockSignals(True)
        try:
            self.list.clear()
            mods = self._all_mods
            if self._filter == 'update':
                mods = [m for m in mods if m.get('update_available')]
            elif self._filter == 'disabled':
                mods = [m for m in mods if not m.get('enabled', True)]
            query = self.search_input.text().strip().lower() if hasattr(self, 'search_input') else ''
            if query:
                mods = [m for m in mods if query in str(m.get('name', '')).lower() or query in str(m.get('filename', '')).lower() if query in str(m.get('description', '')).lower() or query in str(m.get('modid', '')).lower()]
            if not mods:
                empty_text = {'update': '没有可更新的 Mod', 'disabled': '没有已禁用的 Mod'}.get(self._filter, '没有匹配的 Mod')
                item = QListWidgetItem(empty_text)
                item.setFlags(Qt.NoItemFlags)
                self.list.addItem(item)
                self.status_label.setText('未检测到可更新 Mod' if self._filter == 'update' else '没有已禁用的 Mod' if self._filter == 'disabled' else f'共 0 个 Mod：{self.mods_dir}')
            else:
                for info in mods:
                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, info)
                    item.setSizeHint(QSize(0, 66))
                    self.list.addItem(item)
                    widget = ModRowWidget(info, self.list)
                    self.list.setItemWidget(item, widget)
                    if str(info.get('path')) in selected_paths:
                        item.setSelected(True)
                self.status_label.setText(f'共 {len(mods)} 个 Mod：{self.mods_dir}')
        finally:
            self.list.blockSignals(False)
            self.list.verticalScrollBar().setValue(scroll_pos)
            self._on_selection_changed()

    def _selected(self):
        result = []
        for item in self.list.selectedItems():
            info = item.data(Qt.UserRole)
            if info:
                result.append(info)
        return result

    def _on_selection_changed(self):
        selected = self._selected()
        count = len(selected)
        self.selected_label.setText(f'已选择 {count} 个文件')
        self.action_bar.setVisible(count > 0)
        self.enable_btn.setEnabled(any((not m.get('enabled') for m in selected)))
        self.disable_btn.setEnabled(any((m.get('enabled') for m in selected)))
        self.update_btn.setEnabled(bool(selected) and not self._mod_update_disabled and any((m.get('update_available') for m in selected)))
        self.delete_btn.setEnabled(bool(selected))

    def _set_enabled(self, enabled):
        selected = self._selected()
        changed = 0
        errors = []
        for info in selected:
            path = info['path']
            if info.get('enabled') == enabled:
                continue
            try:
                if enabled:
                    name = path.name
                    if name.lower().endswith('.disabled'):
                        new_path = path.with_name(name[:-len('.disabled')])
                else:
                    new_path = path.with_name(path.name + '.disabled')
                if new_path.exists():
                    errors.append(f'{new_path.name} 已存在')
                else:
                    path.rename(new_path)
                    changed += 1
                continue
            except OSError as exc:
                errors.append(f'{path.name}: {exc}')
                continue
        if errors:
            QMessageBox.warning(self, '部分操作失败', '\n'.join(errors[:8]))
        if changed:
            self.status_label.setText(f'已{"启用" if enabled else "禁用"} {changed} 个 Mod')
        self.refresh()

    def _delete_selected(self):
        selected = self._selected()
        if not selected:
            return
        reply = QMessageBox.question(self, '删除 Mod', f'确定删除选中的 {len(selected)} 个 Mod 文件吗？此操作不可撤销。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        errors = []
        for info in selected:
            try:
                Path(info['path']).unlink()
            except OSError as exc:
                errors.append(f'{info["filename"]}: {exc}')
                continue
        if errors:
            QMessageBox.warning(self, '部分删除失败', '\n'.join(errors[:8]))
        self.refresh()

    def _update_selected(self):
        if self._mod_update_disabled:
            QMessageBox.information(self, '更新已禁用', '当前版本已开启「禁止更新Mod」。\n如需更新，请在游戏设置的「高级选项」中取消勾选。')
            return
        selected = self._selected()
        if not selected:
            return
        updatable = [info for info in selected if info.get('update_available') if info.get('latest_file')]
        if not updatable:
            QMessageBox.information(self, '暂无更新', '所选 Mod 没有检测到适配当前游戏版本的新版本。')
            return
        worker = self._update_download_worker
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, '请稍候', '正在更新 Mod，请等待完成。')
            return
        self.update_btn.setEnabled(False)
        self.status_label.setText(f'正在更新 {len(updatable)} 个 Mod...')
        worker = ModUpdateDownloadWorker(updatable, self.mods_dir)
        worker.status.connect(self.status_label.setText)
        worker.done.connect(self._on_mod_update_done)
        worker.finished.connect((lambda w=worker: self._on_mod_update_finished(w)))
        self._update_download_worker = worker
        worker.start()

    def _on_mod_update_done(self, updated, errors):
        if updated:
            self.status_label.setText(f'已更新 {updated} 个 Mod')
        if errors:
            QMessageBox.warning(self, '部分 Mod 更新失败', '\n'.join(errors[:6]))
        self.refresh()

    def _on_mod_update_finished(self, worker):
        if self._update_download_worker is worker:
            self._update_download_worker = None
        worker.deleteLater()


