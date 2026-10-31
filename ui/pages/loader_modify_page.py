'''版本设置 - 修改版本名称和加载器。'''

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QThread, QTimer, Signal, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.app_font import ui_font
from core.paths import safe_rmtree
from core.config import ConfigManager
from core.download_manager import DownloadManager
from core.installers import create_alias, install_fabric, install_forge, install_neoforge, pick_java
from core.launch import LaunchManager
from core.net import network_available
from core.optifabric import (
    LEGACY_FABRIC_LOADER,
    file_version_tuple as _optifabric_version_tuple,
    resolve as _resolve_optifabric,
)
from core.version import VersionManager, detect_version_loader
from ui.pages.version_detail_page import LoadVersionsWorker
from ui.version_meta import (
    display_name,
    loader_supported,
    version_icon,
    version_tuple,
)
from ui.widgets.dark_dialog import DarkQuestionDialog
from ui.widgets.loading_overlay import LoadingOverlay
from ui.widgets.painted_icons import painted_icon, set_button_icon
from ui.widgets.toast import show_toast

LOADER_LABELS = {
    'forge': 'Forge',
    'neoforge': 'NeoForge',
    'fabric': 'Fabric',
    'fabric_api': 'Fabric API',
    'optifine': 'OptiFine',
}
LOADER_ICON_FILES = {
    'forge': 'forge.png',
    'neoforge': 'neo_forge.png',
    'fabric': 'fabric.png',
    'fabric_api': 'fabric.png',
    'optifine': 'optifine.png',
}
LOADER_PATTERNS = {
    'fabric': r'fabric-loader:([^:@]+)',
    'forge': r'minecraftforge:forge:([^:@]+)',
    'neoforge': r'neoforged:neoforge:([^:@]+)',
    'quilt': r'quilt-loader:([^:@]+)',
    'optifine': r'optifine:OptiFine:([^:@]+)',
}
MAIN_LOADERS = ('forge', 'neoforge', 'fabric')
LOADER_ORDER = ('forge', 'neoforge', 'fabric', 'fabric_api', 'optifine')
API_JAR_RE = re.compile(r'fabric[-_]api', re.I)
VANILLA_BACKUP_NAME = '.pmgl_vanilla_backup.json'
IMAGES_DIR = Path(__file__).resolve().parent.parent / 'imgs'
LAUNCHWRAPPER_MAIN = 'net.minecraft.launchwrapper.Launch'


def _merge_optifine_payload(merged, generated, keep_main=False):
    '''把 OptiFine 独立安装生成的版本信息并入现有版本 JSON：保留原库与参数，追加 OptiFine 的部分。'''

    current_main = str(merged.get('mainClass') or '')
    generated_main = str(generated.get('mainClass') or '')
    if generated_main:
        if not current_main:
            merged['mainClass'] = generated_main
        elif not keep_main or current_main == generated_main:
            merged['mainClass'] = generated_main
        # keep_main 且 mainClass 不同（如 Forge 的 modlauncher）时保留原 mainClass
    libraries = list(merged.get('libraries') or [])
    seen = {str((lib or {}).get('name') or '') for lib in libraries}
    for lib in generated.get('libraries') or []:
        name = str((lib or {}).get('name') or '')
        if name and name not in seen:
            libraries.append(lib)
            seen.add(name)
    merged['libraries'] = libraries
    if generated.get('minecraftArguments'):
        tokens = list(str(merged.get('minecraftArguments') or '').split())
        for token in str(generated['minecraftArguments']).split():
            if token not in tokens:
                tokens.append(token)
        merged['minecraftArguments'] = ' '.join(tokens)
    generated_arguments = (
        generated.get('arguments')
        if isinstance(generated.get('arguments'), dict) else {}
    )
    if generated_arguments:
        base_arguments = (
            merged.get('arguments')
            if isinstance(merged.get('arguments'), dict) else {}
        )
        game_args = list(base_arguments.get('game') or [])
        for arg in generated_arguments.get('game') or []:
            if arg not in game_args:
                game_args.append(arg)
        jvm_args = list(base_arguments.get('jvm') or [])
        for arg in generated_arguments.get('jvm') or []:
            if arg not in jvm_args:
                jvm_args.append(arg)
        merged['arguments'] = {'game': game_args, 'jvm': jvm_args}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}


def _mods_dirs(mc_dir, version_id):
    dirs = []
    try:
        dirs.append(Path(LaunchManager().get_mods_dir(version_id)))
    except Exception:
        pass
    dirs.append(Path(mc_dir) / 'versions' / version_id / 'mods')
    seen = set()
    result = []
    for path in dirs:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _remove_api_jars(mc_dir, version_id):
    for mods_dir in _mods_dirs(mc_dir, version_id):
        try:
            for jar in mods_dir.glob('*.jar'):
                if API_JAR_RE.search(jar.name):
                    jar.unlink(missing_ok=True)
        except OSError:
            pass


def _install_mods_dir(mc_dir, version_id):
    try:
        return Path(LaunchManager().get_mods_dir(version_id))
    except Exception:
        return Path(mc_dir) / 'versions' / version_id / 'mods'


def _remove_optifine_jars(mc_dir, version_id):
    for mods_dir in _mods_dirs(mc_dir, version_id):
        try:
            for jar in mods_dir.glob('*.jar'):
                lower = jar.name.lower()
                if lower.startswith('optifine_') or lower == 'optifine.jar':
                    jar.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_optifabric_jars(mc_dir, version_id):
    for mods_dir in _mods_dirs(mc_dir, version_id):
        try:
            for jar in mods_dir.glob('*.jar'):
                if 'optifabric' in jar.name.lower():
                    jar.unlink(missing_ok=True)
        except OSError:
            pass


class _LoaderHeader(QFrame):
    '''加载器行的可点击标题栏。'''

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('LoaderHeader')
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _VersionRowWidget(QWidget):
    '''版本列表中的一行：图标 + 版本号 + 稳定版/测试版。'''

    def __init__(self, icon_path, version, tag, selected, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(10)

        icon_label = QLabel()
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        pixmap = QPixmap()
        if icon_path is not None and Path(icon_path).is_file():
            pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            icon_label.setPixmap(painted_icon('package', 22, '#aeb8c8').pixmap(22, 22))
        else:
            from ui.widgets.settings_widgets import hidpi_pixmap
            icon_label.setPixmap(hidpi_pixmap(pixmap, 26, 26))
        layout.addWidget(icon_label)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        name_label = QLabel(str(version))
        name_label.setFont(ui_font(13))
        name_label.setStyleSheet('color:rgba(255,255,255,0.92); background:transparent;')
        name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        tag_label = QLabel(str(tag))
        tag_label.setStyleSheet('color:rgba(255,255,255,0.46); font-size:11px; background:transparent;')
        tag_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_box.addWidget(name_label)
        text_box.addWidget(tag_label)
        layout.addLayout(text_box, 1)

        if selected:
            check = QLabel('✓')
            check.setStyleSheet('color:#67b7ff; font-size:14px; font-weight:900; background:transparent;')
            check.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(check, 0, Qt.AlignVCenter)


class VersionModifyWorker(QThread):
    status = Signal(str)
    progress = Signal(int)
    done = Signal()
    error = Signal(str)

    def __init__(self, plan):
        super().__init__()
        self.plan = dict(plan or {})
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _cancel_requested(self):
        return self._cancelled

    def _progress_mapped(self, start, span):
        return lambda value: self.progress.emit(
            int(start + max(0, min(100, value)) * span / 100)
        )

    def _backup_vanilla_json(self):
        plan = self.plan
        version_id = plan['version_id']
        if version_id != plan['base_mc']:
            return
        current_loader = plan.get('current_loader') or ''
        current_optifine = plan.get('current_optifine') or ''
        if current_loader or current_optifine:
            return
        version_dir = Path(plan['mc_dir']) / 'versions' / version_id
        version_json = version_dir / f'{version_id}.json'
        backup = version_dir / VANILLA_BACKUP_NAME
        if backup.exists() or not version_json.is_file():
            return
        try:
            shutil.copy2(version_json, backup)
        except OSError:
            pass

    def _restore_vanilla(self):
        plan = self.plan
        mc_dir = Path(plan['mc_dir'])
        version_id = plan['version_id']
        base_mc = plan['base_mc']
        version_dir = mc_dir / 'versions' / version_id
        if version_id != base_mc:
            vanilla_json = mc_dir / 'versions' / base_mc / f'{base_mc}.json'
            if not vanilla_json.is_file():
                raise RuntimeError(
                    f'原版 {base_mc} 未安装，无法恢复原版；请先下载原版 {base_mc}'
                )
            create_alias(mc_dir, version_id, base_mc, jar_id=base_mc)
        else:
            backup = version_dir / VANILLA_BACKUP_NAME
            data = {}
            if backup.is_file():
                data = _read_json(backup)
                if not data:
                    raise RuntimeError('原版备份已损坏，无法恢复原版')
            else:
                data = self._fetch_vanilla_json(base_mc) or {}
                if not data:
                    raise RuntimeError(
                        '缺少原版备份，且无法在线获取原版信息，无法恢复原版；'
                        '请联网后重试'
                    )
            data['id'] = version_id
            data.pop('inheritsFrom', None)
            (version_dir / f'{version_id}.json').write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
        pack_path = version_dir / 'pmgl_pack.json'
        pack = _read_json(pack_path)
        if pack:
            changed = False
            for key in ('loader', 'loader_version'):
                if pack.pop(key, None) is not None:
                    changed = True
            if changed:
                try:
                    pack_path.write_text(
                        json.dumps(pack, ensure_ascii=False, indent=2),
                        encoding='utf-8',
                    )
                except OSError:
                    pass

    @staticmethod
    def _fetch_vanilla_json(mc_version):
        '''在线获取原版版本 JSON（BMCLAPI 优先，Mojang 官方兜底）。'''

        try:
            from core.downloading import fetch_version_json
            data = fetch_version_json(str(mc_version))
        except Exception:
            return None
        if isinstance(data, dict) and data.get('libraries') is not None:
            return data
        return None

    def _strip_optifine_json(self):
        '''把通过“独立安装合并”写入的 OptiFine 信息从版本 JSON 中剥离。'''

        version_id = self.plan['version_id']
        base_mc = self.plan['base_mc']
        version_dir = Path(self.plan['mc_dir']) / 'versions' / version_id
        json_path = version_dir / f'{version_id}.json'
        data = _read_json(json_path)
        if not data:
            return
        own_libs = [
            str((lib or {}).get('name') or '')
            for lib in data.get('libraries') or []
        ]
        if not any('optifine' in name.lower() for name in own_libs):
            return
        parent = str(data.get('inheritsFrom') or '').strip()
        if parent:
            clean = {'id': version_id, 'inheritsFrom': parent, 'type': 'release'}
            jar = str(data.get('jar') or '').strip()
            if jar:
                clean['jar'] = jar
            json_path.write_text(
                json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8'
            )
            return
        backup = version_dir / VANILLA_BACKUP_NAME
        restored = _read_json(backup) if backup.is_file() else {}
        if not restored:
            restored = self._fetch_vanilla_json(base_mc) or {}
        if restored:
            restored['id'] = version_id
            restored.pop('inheritsFrom', None)
            json_path.write_text(
                json.dumps(restored, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

    def _merge_standalone_optifine(self, generated_id):
        plan = self.plan
        mc_dir = Path(plan['mc_dir'])
        version_id = plan['version_id']
        base_mc = plan['base_mc']
        versions_dir = mc_dir / 'versions'
        generated_dir = versions_dir / generated_id
        generated_json = generated_dir / f'{generated_id}.json'
        target_dir = versions_dir / version_id
        target_dir.mkdir(parents=True, exist_ok=True)
        if generated_id == version_id:
            return
        generated = _read_json(generated_json)
        if not generated:
            raise RuntimeError('OptiFine 安装器生成的版本信息无法读取')
        target_json = target_dir / f'{version_id}.json'
        current = _read_json(target_json)
        if version_id == base_mc:
            # 原版本体：并入原版 JSON，保留原版库，追加 OptiFine 库与参数
            merged = dict(current or {})
            merged['id'] = version_id
            merged.pop('inheritsFrom', None)
            merged['jar'] = base_mc
            _merge_optifine_payload(merged, generated)
            target_json.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        elif str((current or {}).get('inheritsFrom') or '').strip():
            # 继承式版本：父版本按“目标加载器”决定——
            # 保留 Forge 时沿用当前指向（装完 Forge 后已是新目录），
            # 目标是纯 OptiFine（已卸载加载器）时必须指回原版
            target_loader = str(plan.get('target_loader') or '')
            if target_loader == 'forge':
                parent_id = str(current['inheritsFrom']).strip()
            else:
                parent_id = base_mc
            data = dict(generated)
            data['id'] = version_id
            data['inheritsFrom'] = parent_id
            # jar 必须指向 classpath 上真实存在的客户端 jar：
            # 优先保留安装器写好的指向；老版 Forge 目录可能没有 jar，此时回退原版
            if target_loader == 'forge':
                existing_jar = str((current or {}).get('jar') or '').strip()
                if existing_jar and (versions_dir / existing_jar / f'{existing_jar}.jar').is_file():
                    data['jar'] = existing_jar
                elif (versions_dir / parent_id / f'{parent_id}.jar').is_file():
                    data['jar'] = parent_id
                else:
                    data['jar'] = base_mc
            else:
                data['jar'] = base_mc
            parent_data = _read_json(
                versions_dir / parent_id / f'{parent_id}.json'
            )
            parent_main = str((parent_data or {}).get('mainClass') or '')
            if parent_main and parent_main != LAUNCHWRAPPER_MAIN:
                # 父版本有自己的主类（如新版 Forge），不要用 OptiFine 的覆盖
                data.pop('mainClass', None)
            target_json.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
        else:
            # 完整版本 JSON（如旧版 Forge 安装器产物）：并入，保留加载器主类
            merged = dict(current or {})
            merged['id'] = version_id
            _merge_optifine_payload(merged, generated, keep_main=True)
            target_json.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        safe_rmtree(generated_dir, ignore_errors=True)

    def _json_has_standalone_optifine(self):
        '''版本 JSON 自身的库列表里是否已含 OptiFine（独立安装合并后的形态）。'''

        version_id = self.plan['version_id']
        data = _read_json(
            Path(self.plan['mc_dir']) / 'versions' / version_id / f'{version_id}.json'
        )
        for lib in data.get('libraries') or []:
            name = str((lib or {}).get('name') or '').lower()
            if name.startswith('optifine:optifine'):
                return True
        return False

    def _repoint_standalone_optifine(self):
        '''OptiFine 不变时仅调整继承关系（如从 Forge 切回纯 OptiFine）。'''

        version_id = self.plan['version_id']
        base_mc = self.plan['base_mc']
        versions_dir = Path(self.plan['mc_dir']) / 'versions'
        json_path = versions_dir / version_id / f'{version_id}.json'
        data = _read_json(json_path)
        if not data:
            return
        if str(data.get('inheritsFrom') or '').strip():
            data['inheritsFrom'] = base_mc
            data['jar'] = base_mc if (versions_dir / base_mc / f'{base_mc}.jar').is_file() else version_id
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
            )
            self.status.emit('OptiFine 独立安装已就绪')
            return
        # 完整版本 JSON（老版 Forge 与 OptiFine 合并过的形态）：以原版为底重建
        vanilla = self._fetch_vanilla_json(base_mc)
        if not vanilla:
            self.status.emit('无法在线获取原版信息，保留当前安装')
            return
        of_libs = [
            lib for lib in data.get('libraries') or []
            if 'optifine' in str((lib or {}).get('name') or '').lower()
        ]
        rebuilt = dict(vanilla)
        rebuilt['id'] = version_id
        rebuilt.pop('inheritsFrom', None)
        _merge_optifine_payload(rebuilt, {
            'mainClass': str(data.get('mainClass') or ''),
            'libraries': of_libs,
            'minecraftArguments': str(data.get('minecraftArguments') or ''),
            'arguments': data.get('arguments') or {},
        })
        # 剔除 Forge 的 tweakClass（保留 OptiFine 的）
        raw_tokens = str(rebuilt.get('minecraftArguments') or '').split()
        cleaned = []
        index = 0
        while index < len(raw_tokens):
            token = raw_tokens[index]
            if token == '--tweakClass' and index + 1 < len(raw_tokens):
                value = raw_tokens[index + 1]
                if 'minecraftforge' in value.lower():
                    index += 2
                    continue
                cleaned.append(token)
                cleaned.append(value)
                index += 2
                continue
            cleaned.append(token)
            index += 1
        rebuilt['minecraftArguments'] = ' '.join(cleaned)
        json_path.write_text(
            json.dumps(rebuilt, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        self.status.emit('OptiFine 独立安装已就绪')

    def _fallback_optifine_forge(self, target_optifine, standalone_error=''):
        plan = self.plan
        mc_dir = plan['mc_dir']
        base_mc = plan['base_mc']
        version_id = plan['version_id']
        forge_version = str(plan.get('fallback_forge_version') or '').strip()
        if not forge_version:
            try:
                helper = LoadVersionsWorker(base_mc, load_optional=False)
                versions = helper._get_forge() or []
                forge_version = str(versions[0]) if versions else ''
            except Exception:
                forge_version = ''
        if not forge_version:
            raise RuntimeError(
                f'OptiFine 独立安装失败，且无法获取 Forge 备用版本：{standalone_error}'
            )
        self.status.emit(
            f'OptiFine 独立安装失败，正在使用 Forge {forge_version} 备用方案...'
        )
        try:
            install_forge(
                base_mc, forge_version, mc_dir, version_id,
                status_cb=self.status.emit,
                progress_cb=self._progress_mapped(86, 8),
                cancel=self._cancel_requested,
            )
            if self._cancelled:
                return
            _remove_api_jars(mc_dir, version_id)
            _remove_optifabric_jars(mc_dir, version_id)
            self._install_optifine(target_optifine, 'forge', 94, 6)
        except Exception as exc:
            raise RuntimeError(
                f'OptiFine 独立安装失败（{standalone_error}），'
                f'Forge 备用方案也失败：{exc}'
            ) from exc

    def _install_optifine_standalone(self, target_optifine, progress_base, progress_span):
        from core.optifine import OptiFineManager
        manager = OptiFineManager()
        plan = self.plan
        info = manager.find(plan['base_mc'], target_optifine)
        if info is None:
            raise RuntimeError(f'未找到 OptiFine 版本: {target_optifine}')
        version_dir = Path(plan['mc_dir']) / 'versions' / plan['version_id']
        version_dir.mkdir(parents=True, exist_ok=True)
        temp_jar = version_dir / '.pmgl_optifine_installer.jar'
        self.status.emit(f'正在下载 OptiFine {info.display()}...')

        def download_progress(done, total):
            ratio = done / total if total else 0
            self.progress.emit(progress_base + int(progress_span * 0.35 * ratio))

        generated_id = None
        try:
            manager.download_jar(
                info, temp_jar, progress=download_progress,
                cancel=self._cancel_requested,
            )
            if self._cancelled:
                return
            self.progress.emit(progress_base + int(progress_span * 0.4))
            self.status.emit('正在运行 OptiFine 独立安装器...')
            generated_id = manager.install_standalone(
                plan['mc_dir'], plan['base_mc'], temp_jar, pick_java(plan['base_mc']),
                status=self.status.emit, cancel=self._cancel_requested,
            )
            if self._cancelled:
                return
            if not generated_id:
                raise RuntimeError('OptiFine 安装器没有生成独立版本')
            self.status.emit('正在合并 OptiFine 版本信息...')
            self._merge_standalone_optifine(generated_id)
            self.progress.emit(progress_base + progress_span)
            self.status.emit('OptiFine 安装完成')
        except Exception:
            if generated_id and generated_id != plan['version_id']:
                safe_rmtree(
                    Path(plan['mc_dir']) / 'versions' / generated_id,
                    ignore_errors=True,
                )
            raise
        finally:
            try:
                temp_jar.unlink(missing_ok=True)
            except OSError:
                pass

    def _install_optifine(self, target_optifine, target_loader, progress_base, progress_span):
        from core.optifine import OptiFineManager
        manager = OptiFineManager()
        info = manager.find(self.plan['base_mc'], target_optifine)
        if info is None:
            raise RuntimeError(f'未找到 OptiFine 版本: {target_optifine}')
        mods_dir = _install_mods_dir(self.plan['mc_dir'], self.plan['version_id'])
        mods_dir.mkdir(parents=True, exist_ok=True)
        version_dir = Path(self.plan['mc_dir']) / 'versions' / self.plan['version_id']
        version_dir.mkdir(parents=True, exist_ok=True)
        temp_jar = version_dir / '.pmgl_optifine_download.jar'
        optifabric_file = None
        optifabric_tmp = None
        self.status.emit(f'正在下载 OptiFine {info.display()}...')

        def optifine_progress(done, total):
            ratio = done / total if total else 0
            self.progress.emit(progress_base + int(progress_span * 0.65 * ratio))

        try:
            manager.download_jar(
                info, temp_jar, progress=optifine_progress,
                cancel=self._cancel_requested,
            )
            if self._cancelled:
                return
            if target_loader == 'fabric':
                from core.optifabric import download as download_optifabric, find_for
                self.status.emit('正在准备 OptiFabric...')
                optifabric_file = (
                    getattr(self, '_chosen_optifabric', None)
                    or find_for(self.plan['base_mc'])
                )
                if optifabric_file is None:
                    raise RuntimeError('未找到适配的 OptiFabric 版本')
                _remove_optifabric_jars(self.plan['mc_dir'], self.plan['version_id'])
                optifabric_tmp = download_optifabric(
                    optifabric_file, version_dir, cancel=self._cancel_requested
                )
            if self._cancelled:
                return
            _remove_optifine_jars(self.plan['mc_dir'], self.plan['version_id'])
            target_jar = mods_dir / info.filename
            if target_jar.exists():
                target_jar.unlink()
            shutil.move(str(temp_jar), str(target_jar))
            if target_loader == 'fabric' and optifabric_tmp is not None:
                source = Path(optifabric_tmp)
                target = mods_dir / source.name
                if source.resolve() != target.resolve():
                    if target.exists():
                        target.unlink()
                    shutil.move(str(source), str(target))
                optifabric_tmp = None
            else:
                _remove_optifabric_jars(self.plan['mc_dir'], self.plan['version_id'])
            self.progress.emit(progress_base + progress_span)
            self.status.emit('OptiFine 安装完成')
        finally:
            try:
                temp_jar.unlink(missing_ok=True)
            except OSError:
                pass
            if optifabric_tmp is not None:
                try:
                    Path(optifabric_tmp).unlink(missing_ok=True)
                except OSError:
                    pass

    def run(self):
        plan = self.plan
        mc_dir = plan['mc_dir']
        version_id = plan['version_id']
        base_mc = plan['base_mc']
        current_loader = plan.get('current_loader') or ''
        current_version = plan.get('current_version') or ''
        current_api = plan.get('current_api') or ''
        current_optifine = plan.get('current_optifine') or ''
        target_loader = plan.get('target_loader') or ''
        target_version = plan.get('target_version') or ''
        target_api = plan.get('target_api') or ''
        target_optifine = plan.get('target_optifine') or ''
        try:
            self._backup_vanilla_json()
            current_main = current_loader if current_loader in MAIN_LOADERS else ''
            optifine_changed = target_optifine != current_optifine
            restore_optifine_only = (
                current_loader == 'optifine'
                and not target_loader
                and not target_optifine
            )
            loader_changed = (
                target_loader != current_main
                or (target_loader and target_version != current_version)
                or restore_optifine_only
            )
            # Fabric + OptiFine 的 Loader 兼容处理（无论加载器是否变更都要检查）
            legacy_fabric_loader = ''
            if target_loader == 'fabric' and target_optifine:
                chosen_optifabric, legacy_needed = _resolve_optifabric(base_mc)
                self._chosen_optifabric = chosen_optifabric
                if legacy_needed:
                    active_loader = target_version if loader_changed else current_version
                    loader_vt = version_tuple(active_loader)
                    if loader_vt and loader_vt >= (0, 15):
                        if version_tuple(base_mc) >= (1, 20, 5):
                            raise RuntimeError(
                                '该 MC 版本需要 Fabric Loader 0.15+，但没有与其'
                                '兼容的 OptiFabric，无法同时安装 OptiFine'
                            )
                        legacy_fabric_loader = LEGACY_FABRIC_LOADER
                        self.status.emit(
                            f'OptiFabric 需要旧版 Fabric Loader，'
                            f'已改用 {LEGACY_FABRIC_LOADER}...'
                        )
            target_standalone_optifine = bool(target_optifine and not target_loader)

            if target_standalone_optifine:
                self.progress.emit(3)
                if not optifine_changed and self._json_has_standalone_optifine():
                    # OptiFine 版本没变：只需把继承关系指向目标（如卸载 Forge 保留 OF），
                    # 无需重新下载安装
                    self.status.emit('正在调整 OptiFine 独立安装...')
                    self._repoint_standalone_optifine()
                    self.progress.emit(100)
                    self.done.emit()
                    return
                self.status.emit('正在准备独立安装 OptiFine...')
                _remove_api_jars(mc_dir, version_id)
                _remove_optifine_jars(mc_dir, version_id)
                _remove_optifabric_jars(mc_dir, version_id)
                base = 5 if loader_changed else 0
                span = 80 if loader_changed else 90
                try:
                    self._install_optifine_standalone(target_optifine, base, span)
                except Exception as exc:
                    if self._cancelled:
                        return
                    self._fallback_optifine_forge(target_optifine, str(exc))
                if self._cancelled:
                    return
                self.progress.emit(100)
                self.done.emit()
                return

            if loader_changed:
                self.status.emit('正在卸载现有版本加载器...')
                self.progress.emit(3)
                if not target_loader:
                    self._restore_vanilla()
                    _remove_api_jars(mc_dir, version_id)
                    _remove_optifine_jars(mc_dir, version_id)
                    _remove_optifabric_jars(mc_dir, version_id)
                    self.progress.emit(100)
                    self.done.emit()
                    return
                self.status.emit(
                    f'正在安装 {LOADER_LABELS.get(target_loader, target_loader)} {target_version}...'
                )
                progress_cb = self._progress_mapped(5, 60)
                fabric_version = LEGACY_FABRIC_LOADER if legacy_fabric_loader else target_version
                if target_loader == 'fabric':
                    install_fabric(
                        base_mc, fabric_version, mc_dir, version_id,
                        status_cb=self.status.emit,
                        progress_cb=progress_cb,
                        cancel=self._cancel_requested,
                    )
                elif target_loader == 'forge':
                    install_forge(
                        base_mc, target_version, mc_dir, version_id,
                        status_cb=self.status.emit,
                        progress_cb=progress_cb,
                        cancel=self._cancel_requested,
                    )
                elif target_loader == 'neoforge':
                    install_neoforge(
                        base_mc, target_version, mc_dir, version_id,
                        status_cb=self.status.emit,
                        progress_cb=progress_cb,
                        cancel=self._cancel_requested,
                    )
                else:
                    raise RuntimeError('暂不支持修改该加载器')
            if self._cancelled:
                return
            if legacy_fabric_loader and not loader_changed:
                # 加载器本身没变，但 OptiFabric 需要旧版 Loader：重装 Fabric
                self.status.emit(
                    f'正在改用 Fabric Loader {LEGACY_FABRIC_LOADER} 以兼容 OptiFabric...'
                )
                install_fabric(
                    base_mc, LEGACY_FABRIC_LOADER, mc_dir, version_id,
                    status_cb=self.status.emit,
                    progress_cb=self._progress_mapped(5, 60),
                    cancel=self._cancel_requested,
                )
                if self._cancelled:
                    return

            if target_optifine:
                if target_loader not in ('forge', 'fabric'):
                    raise RuntimeError('OptiFine 需要配合 Forge 或 Fabric 使用')
                if optifine_changed or loader_changed:
                    opti_base = 65 if loader_changed else 0
                    opti_span = 20 if loader_changed else 80
                    if target_loader == 'forge':
                        # Forge 场景默认独立安装（合并进版本 JSON），
                        # 失败时退回 mods 目录备用方案
                        try:
                            self.status.emit('正在准备独立安装 OptiFine...')
                            _remove_optifine_jars(mc_dir, version_id)
                            _remove_optifabric_jars(mc_dir, version_id)
                            self._install_optifine_standalone(
                                target_optifine, opti_base, opti_span
                            )
                        except Exception as exc:
                            if self._cancelled:
                                return
                            self.status.emit(
                                'OptiFine 独立安装失败，正在使用备用方案安装...'
                            )
                            try:
                                self._install_optifine(
                                    target_optifine, target_loader,
                                    opti_base, opti_span,
                                )
                            except Exception as fallback_exc:
                                raise RuntimeError(
                                    f'OptiFine 独立安装失败（{exc}），'
                                    f'备用方案也失败：{fallback_exc}'
                                ) from fallback_exc
                    else:
                        self._install_optifine(
                            target_optifine, target_loader, opti_base, opti_span
                        )
            elif current_optifine:
                self.status.emit('正在移除 OptiFine...')
                _remove_optifine_jars(mc_dir, version_id)
                self._strip_optifine_json()
            if not (target_loader == 'fabric' and target_optifine):
                _remove_optifabric_jars(mc_dir, version_id)
            if self._cancelled:
                return

            if target_loader == 'fabric' and target_api:
                if target_api != current_api or loader_changed:
                    self.status.emit(f'正在更新 Fabric API {target_api}...')
                    api_base = 85 if loader_changed or target_optifine else 0
                    api_span = 15 if loader_changed or target_optifine else 100

                    def api_progress(done, total):
                        ratio = int(done * 100 / total) if total else 0
                        self.progress.emit(api_base + int(ratio * api_span / 100))

                    ok = VersionManager(mc_dir).download_fabric_api(
                        version_id, mc_dir, base_mc, target_api,
                        progress_cb=api_progress,
                        cancel=self._cancel_requested,
                    )
                    if not ok and not self._cancelled:
                        raise RuntimeError('Fabric API 下载失败')
            else:
                if current_api or (target_loader and target_loader != 'fabric'):
                    self.status.emit('正在移除 Fabric API...')
                    _remove_api_jars(mc_dir, version_id)
            if self._cancelled:
                return
            self.progress.emit(100)
            self.done.emit()
        except Exception as exc:
            if not self._cancelled:
                self.error.emit(str(exc))


class LoaderModifyPage(QWidget):
    changed = Signal(str)

    def __init__(self, version_id, parent=None):
        super().__init__(parent)
        self.version_id = version_id
        self.config = ConfigManager()
        self.launch_mgr = LaunchManager()
        self.mc_dir = self.config.get_mc_dir()
        self.version_dir = Path(self.mc_dir) / 'versions' / version_id
        self._versions_worker = None
        self._modify_worker = None
        self._versions = {key: [] for key in LOADER_ORDER}
        self._stability = {key: {} for key in LOADER_ORDER}
        self._loaded_keys = set()
        self._versions_finished = False
        self._rows = {}
        self._row_animations = {}
        self._current_loader = ''
        self._current_version = ''
        self._current_api = ''
        self._current_optifine = ''
        self._base_mc = version_id
        self._selected_loader = ''
        self._selected_version = ''
        self._selected_api = ''
        self._selected_optifine = ''
        self._build_ui()
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.attach_to(self)
        self.loading_overlay.hidden.connect(self._on_loading_hidden)
        self._loading_watchdog = QTimer(self)
        self._loading_watchdog.setSingleShot(True)
        self._loading_watchdog.setInterval(12000)
        self._loading_watchdog.timeout.connect(self._on_loading_watchdog)
        self._load_meta()
        self._refresh_rows()
        self._start_versions_worker()

    # ---------- 读取版本信息 ----------
    def _read_chain(self):
        chain = []
        current = self.version_id
        seen = set()
        while current and current not in seen:
            seen.add(current)
            version_dir = Path(self.mc_dir) / 'versions' / current
            data = _read_json(version_dir / f'{current}.json')
            chain.append((current, data, version_dir))
            parent = str(data.get('inheritsFrom') or '').strip()
            if not parent:
                break
            current = parent
        return chain

    _LOADER_BLOB_MARKERS = (
        ('neoforge', ('net.neoforged', 'neoforged')),
        ('forge', ('net.minecraftforge', 'minecraftforge')),
        ('fabric', ('fabric-loader', 'net.fabricmc')),
        ('quilt', ('quilt-loader', 'org.quiltmc')),
        ('optifine', ('optifine:', 'optifine.OptiFine', 'optifine_optifine')),
    )

    @classmethod
    def _blob_loader(cls, data):
        try:
            blob = json.dumps(data, ensure_ascii=False).lower()
        except (TypeError, ValueError):
            return ''
        for loader, markers in cls._LOADER_BLOB_MARKERS:
            if any(marker in blob for marker in markers):
                return loader
        return ''

    @staticmethod
    def _is_pure_alias(data):
        return bool(
            isinstance(data, dict)
            and str(data.get('inheritsFrom') or '').strip()
            and not (data.get('libraries') or data.get('mainClass')
                     or data.get('minecraftArguments') or data.get('arguments')
                     or data.get('downloads'))
        )

    def _detect_loader(self, chain):
        # 先按 JSON 内容识别（别名指向原版时不应因目录名误判）
        for _vid, data, _dir in chain:
            loader = self._blob_loader(data)
            if loader in ('fabric', 'forge', 'neoforge', 'optifine', 'quilt'):
                return loader
        # 再走常规检测；纯别名（仅 inheritsFrom，无库/主类）跳过名字启发式
        for vid, data, version_dir in chain:
            if self._is_pure_alias(data):
                continue
            try:
                loader = detect_version_loader(vid, data, version_dir)
            except Exception:
                loader = ''
            if loader in ('fabric', 'forge', 'neoforge', 'optifine', 'quilt'):
                return loader
        return ''

    def _detect_loader_version(self, chain, loader):
        pattern = LOADER_PATTERNS.get(loader)
        if not pattern:
            return ''
        for _vid, data, _version_dir in chain:
            for lib in data.get('libraries') or []:
                name = str((lib or {}).get('name') or '')
                match = re.search(pattern, name, re.I)
                if match:
                    value = match.group(1)
                    if loader == 'forge' and self._base_mc and value.startswith(self._base_mc + '-'):
                        value = value[len(self._base_mc) + 1:]
                    return value
        return ''

    def _detect_api(self):
        for mods_dir in _mods_dirs(self.mc_dir, self.version_id):
            try:
                for jar in mods_dir.glob('*.jar'):
                    match = re.search(r'fabric[-_]api[-_]([0-9][0-9A-Za-z.+-]*)', jar.name, re.I)
                    if match:
                        return match.group(1).split('+', 1)[0].removesuffix('.jar')
            except OSError:
                pass
        return ''

    def _detect_optifine(self, chain):
        for mods_dir in _mods_dirs(self.mc_dir, self.version_id):
            try:
                for jar in mods_dir.glob('*.jar'):
                    match = re.match(r'OptiFine_(.+?)(?:_preview)?\.jar$', jar.name, re.I)
                    if match and re.search(r'\d', match.group(1)):
                        return match.group(1).removesuffix('.jar')
            except OSError:
                pass
        # 独立安装合并进版本 JSON 时，OptiFine 以库形式存在
        for _vid, data, _dir in chain:
            for lib in (data or {}).get('libraries') or []:
                match = re.search(
                    r'optifine:OptiFine:([^:@]+)',
                    str((lib or {}).get('name') or ''), re.I,
                )
                if match and re.search(r'\d', match.group(1)):
                    return match.group(1)
        if self._current_loader == 'optifine':
            return self._detect_loader_version(chain, 'optifine')
        return ''

    def _load_meta(self):
        chain = self._read_chain()
        pack = _read_json(self.version_dir / 'pmgl_pack.json')
        base = ''
        if pack.get('mc_version'):
            base = str(pack['mc_version'])
        if not base:
            for _vid, data, _dir in chain:
                marker = str((data or {}).get('pmgl_mc') or '').strip()
                if marker:
                    base = marker
                    break
        if not base:
            base = self._infer_vanilla_base(chain)
        self._base_mc = base or (chain[-1][0] if chain else self.version_id)
        self._current_top_id = chain[-1][0] if chain else self.version_id
        self._current_loader = self._detect_loader(chain)
        self._current_version = self._detect_loader_version(chain, self._current_loader)
        if not self._current_version:
            pack_loader = str(pack.get('loader') or '').lower()
            if pack_loader == self._current_loader:
                self._current_version = str(pack.get('loader_version') or '')
        self._current_api = self._detect_api()
        self._current_optifine = self._detect_optifine(chain)
        self._selected_loader = (
            self._current_loader if self._current_loader in MAIN_LOADERS else ''
        )
        self._selected_version = (
            self._current_version if self._selected_loader else ''
        )
        self._selected_api = (
            self._current_api if self._selected_loader == 'fabric' else ''
        )
        self._selected_optifine = self._current_optifine
        self._set_name_text(display_name(self.config, self.version_id))

    @staticmethod
    def _infer_vanilla_base(chain):
        '''从加载器版本信息里推断真实原版 MC 版本（pmgl_mc 标记缺失时的兜底）。'''

        if len(chain) < 2:
            return ''
        top_id = chain[-1][0]
        for _vid, data, _dir in reversed(chain):
            for lib in (data or {}).get('libraries') or []:
                name = str((lib or {}).get('name') or '')
                match = re.search(r'net\.minecraftforge:forge:([^:@]+)', name)
                if not match:
                    match = re.search(r'optifine:OptiFine:([^:@]+)', name)
                if match:
                    head = re.split(r'[-_]', match.group(1), 1)[0]
                    if re.fullmatch(r'\d+(?:\.\d+)*', head):
                        return head
                    continue
                match = re.search(r'net\.neoforged:neoform:(\d+\.\d+(?:\.\d+)?)-', name)
                if match:
                    return match.group(1)
        for pattern in (
            r'(\d+(?:\.\d+)+)-forge-',
            r'(\d+(?:\.\d+)+)-neoforge-',
            r'fabric-loader-[\w.]+-(\d[\w.]*)$',
            r'quilt-loader-[\w.]+-(\d[\w.]*)$',
        ):
            match = re.match(pattern, top_id, re.I)
            if match:
                return match.group(1)
        return ''

    # ---------- UI ----------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet('QScrollArea { background:transparent; border:none; }')
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 16, 0, 30)
        layout.setSpacing(12)

        name_block = QWidget()
        name_layout = QHBoxLayout(name_block)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(12)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(56, 56)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet('background:transparent;')
        name_layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        name_text_box = QWidget()
        name_text_box.setMaximumHeight(56)
        name_text_layout = QVBoxLayout(name_text_box)
        name_text_layout.setContentsMargins(0, 0, 0, 0)
        name_text_layout.setSpacing(2)
        self.name_edit = QLineEdit()
        self.name_edit.setFixedHeight(34)
        self.name_edit.setStyleSheet(
            'QLineEdit { background:rgba(255,255,255,0.05); color:#f0f0f0;'
            ' border:1px solid rgba(255,255,255,0.16); border-radius:7px;'
            ' padding:4px 10px; font-size:13px; }'
            'QLineEdit:focus { border-color:rgba(0,120,212,0.75); }'
        )
        self.name_edit.textChanged.connect(self._on_name_changed)
        name_text_layout.addWidget(self.name_edit)
        self.original_label = QLabel('')
        self.original_label.setFixedHeight(20)
        self.original_label.setStyleSheet(
            'color:rgba(255,255,255,0.42); font-size:12px; background:transparent;'
        )
        self.original_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.original_label.hide()
        name_text_layout.addWidget(self.original_label)
        name_layout.addWidget(name_text_box, 1, Qt.AlignVCenter)

        self.undo_btn = QToolButton()
        self.undo_btn.setText('↶')
        self.undo_btn.setToolTip('撤销，恢复原名')
        self.undo_btn.setFixedSize(32, 32)
        self.undo_btn.setStyleSheet(
            'QToolButton { background:transparent; color:rgba(255,255,255,0.6);'
            ' border:none; border-radius:6px; font-size:18px; }'
            'QToolButton:hover { background:rgba(255,255,255,0.10); color:white; }'
            'QToolButton:disabled { color:rgba(255,255,255,0.24); }'
        )
        self.undo_btn.clicked.connect(self._undo_name)
        name_layout.addWidget(self.undo_btn, 0, Qt.AlignVCenter)
        layout.addWidget(name_block)

        for key in LOADER_ORDER:
            self._rows[key] = self._create_loader_card(key)
            layout.addWidget(self._rows[key]['frame'])

        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px; background:transparent;')
        layout.addWidget(self.status_label)

        start_row = QHBoxLayout()
        start_row.addStretch(1)
        self.start_btn = QPushButton('开始修改')
        set_button_icon(self.start_btn, 'pencil', 16, '#e6edf3')
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setMinimumWidth(148)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet(
            'QPushButton { background:#0078d4; color:white; border:none;'
            ' border-radius:8px; padding:8px 30px; font-size:13px; }'
            'QPushButton:hover { background:#1084e0; }'
            'QPushButton:pressed { background:#006cbd; }'
            'QPushButton:disabled { background:rgba(128,128,128,0.25); color:#8a8f98; }'
        )
        self.start_btn.clicked.connect(self._on_start_modify)
        start_row.addWidget(self.start_btn)
        start_row.addStretch(1)
        layout.addSpacing(2)
        layout.addLayout(start_row)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.content_scroll = scroll

    def _on_loading_hidden(self):
        try:
            self.content_scroll.show()
        except RuntimeError:
            pass

    def _create_loader_card(self, key):
        frame = QFrame()
        frame.setObjectName('ModifyLoaderCard')
        frame.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = _LoaderHeader()
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 9, 12, 9)
        row.setSpacing(10)
        name = QLabel(LOADER_LABELS.get(key, key))
        name.setFont(ui_font(13, weight=600))
        name.setFixedWidth(108)
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(name)
        state = QLabel('正在获取...')
        state.setStyleSheet('color:rgba(255,255,255,0.55); font-size:12px; background:transparent;')
        state.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(state, 1)

        remove = QToolButton()
        remove.setText('×')
        remove.setToolTip('取消选择')
        remove.setFixedSize(26, 26)
        remove.setStyleSheet(
            'QToolButton { background:transparent; color:rgba(255,255,255,0.65);'
            ' border:none; border-radius:5px; font-size:17px; }'
            'QToolButton:hover { background:rgba(255,90,90,0.18); color:#ff8a8a; }'
            'QToolButton:disabled { color:rgba(255,255,255,0.22); }'
        )
        remove.clicked.connect((lambda _=False, k=key: self._clear_selection(k)))
        row.addWidget(remove)

        arrow = QToolButton()
        arrow.setToolTip('展开版本列表')
        arrow.setFixedSize(26, 26)
        arrow.setIconSize(QSize(16, 16))
        set_button_icon(arrow, 'chevron_right', 16, '#aab4c0')
        arrow.setStyleSheet(
            'QToolButton { background:transparent; border:none; border-radius:5px; }'
            'QToolButton:hover { background:rgba(255,255,255,0.10); }'
            'QToolButton:disabled { background:transparent; }'
        )
        arrow.clicked.connect((lambda _=False, k=key: self._toggle_row(k)))
        row.addWidget(arrow)
        outer.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 0, 14, 12)
        body_layout.setSpacing(0)
        version_list = QListWidget()
        version_list.setFocusPolicy(Qt.NoFocus)
        version_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        version_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        version_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        version_list.setSpacing(1)
        version_list.setStyleSheet(
            'QListWidget { background:rgba(8,10,16,0.45);'
            ' border:1px solid rgba(255,255,255,0.10); border-radius:7px;'
            ' outline:none; padding:3px; }'
            'QListWidget::item { background:transparent; border:none;'
            ' border-radius:6px; margin:1px 2px; }'
            'QListWidget::item:hover { background:rgba(255,255,255,0.07); }'
            'QListWidget::item:selected { background:rgba(0,120,212,0.40); }'
            'QScrollBar:vertical { background:transparent; width:8px; margin:2px; }'
            'QScrollBar::handle:vertical { background:rgba(255,255,255,0.22);'
            ' border-radius:4px; min-height:22px; }'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }'
        )
        version_list.itemClicked.connect(
            (lambda item, k=key: self._select_version(k, str(item.data(Qt.UserRole) or '')))
        )
        body_layout.addWidget(version_list)
        body.hide()
        outer.addWidget(body)

        header.clicked.connect((lambda k=key: self._toggle_row(k)))
        return {
            'frame': frame,
            'header': header,
            'name': name,
            'state': state,
            'remove': remove,
            'arrow': arrow,
            'body': body,
            'list': version_list,
            'expanded': False,
            'signature': None,
            'disabled': False,
        }

    def _set_name_text(self, text):
        self.name_edit.blockSignals(True)
        self.name_edit.setText(str(text or ''))
        self.name_edit.blockSignals(False)
        self._update_original_label(str(text or ''))

    def _update_original_label(self, text):
        value = str(text or '').strip()
        show = bool(value) and value != self.version_id
        self.original_label.setVisible(show)
        if show:
            self.original_label.setText(f'原名: {self.version_id}')
            self.original_label.setToolTip(f'原版本名称：{self.version_id}')
        self.undo_btn.setEnabled(show)

    def _refresh_icon(self):
        icon_path = version_icon(self.mc_dir, self.version_id, self.config)
        if icon_path is not None:
            pix = QPixmap(str(icon_path))
            if not pix.isNull():
                from ui.widgets.settings_widgets import hidpi_pixmap
                self.icon_label.setPixmap(hidpi_pixmap(pix, 50, 50))
                return
        self.icon_label.clear()

    # ---------- 加载器行状态 ----------
    def _incompatible_reason(self, key):
        selected = self._selected_loader
        if key == 'optifine':
            if self._selected_optifine:
                return ''
            if selected in ('forge', 'fabric'):
                return ''
            if selected == 'neoforge':
                return '与 NeoForge 不兼容'
            if not selected:
                return ''
            return f'与 {LOADER_LABELS.get(selected, selected)} 不兼容'
        if key == 'fabric_api':
            if selected == 'fabric':
                return ''
            if selected:
                return f'与 {LOADER_LABELS.get(selected, selected)} 不兼容'
            return '请先选择 Fabric'
        if selected and selected != key:
            return f'与 {LOADER_LABELS.get(selected, selected)} 不兼容'
        return ''

    def _refresh_rows(self):
        for key, parts in self._rows.items():
            supported = loader_supported(self._base_mc, key)
            if key == 'fabric_api' and self._selected_loader != 'fabric':
                # 未选择 Fabric 时隐藏 Fabric API 行
                supported = False
            parts['frame'].setVisible(supported)
            if not supported:
                if parts['expanded']:
                    self._set_row_expanded(key, False, animate=False)
                else:
                    parts['body'].hide()
                continue
            versions = self._versions_for(key)
            reason = self._incompatible_reason(key)
            loaded = key in self._loaded_keys or self._versions_finished
            selected = self._selected_value(key)
            disabled = bool(reason)
            can_expand = False
            if loaded and not versions and not selected and not reason and network_available():
                # 没有匹配当前游戏版本的加载器：直接隐藏空行
                parts['frame'].setVisible(False)
                continue
            if reason:
                state_text = reason
                state_style = 'color:rgba(255,255,255,0.36); font-size:12px; background:transparent;'
            elif selected:
                state_text = selected
                state_style = 'color:#67b7ff; font-size:12px; background:transparent;'
                can_expand = bool(versions)
            elif not loaded and not versions:
                state_text = '' if self.loading_overlay.is_loading() else '正在获取...'
                state_style = 'color:rgba(255,255,255,0.48); font-size:12px; background:transparent;'
            elif not versions:
                if not network_available():
                    state_text = '网络获取失败，请检查网络'
                    state_style = 'color:#ff8a8a; font-size:12px; background:transparent;'
                else:
                    state_text = '暂无可用版本'
                    state_style = 'color:rgba(255,255,255,0.36); font-size:12px; background:transparent;'
                disabled = True
            else:
                can_expand = True
                if key == 'fabric_api' and self._selected_loader == 'fabric':
                    state_text = '未安装'
                else:
                    state_text = '未选择'
                state_style = 'color:rgba(255,255,255,0.58); font-size:12px; background:transparent;'
            if key == 'optifine' and selected and self._selected_loader not in ('forge', 'fabric'):
                can_expand = False
            if (
                key == 'optifine'
                and can_expand
                and not selected
                and not self._selected_loader
                and self._current_loader != 'optifine'
            ):
                state_text = '可独立安装'
            parts['disabled'] = disabled
            parts['can_expand'] = can_expand
            parts['state'].setText(state_text)
            parts['state'].setStyleSheet(state_style)
            parts['name'].setStyleSheet(
                'color:rgba(255,255,255,0.90); font-weight:900; background:transparent;'
                if not disabled else
                'color:rgba(255,255,255,0.36); font-weight:900; background:transparent;'
            )
            parts['remove'].setVisible(bool(selected) and not disabled)
            parts['arrow'].setVisible(can_expand)
            parts['remove'].setEnabled(not self._busy())
            parts['arrow'].setEnabled(not self._busy())
            parts['header'].setEnabled(not disabled and not self._busy())
            parts['header'].setCursor(
                Qt.PointingHandCursor if can_expand and not self._busy() else Qt.ArrowCursor
            )
            if not can_expand:
                if parts['expanded']:
                    self._set_row_expanded(key, False, animate=False)
                else:
                    parts['body'].hide()
            elif parts['expanded']:
                parts['body'].show()
            self._set_arrow(key, parts['expanded'] and can_expand)
            if can_expand and parts['expanded']:
                self._populate_versions(key)
            self._apply_card_style(key, disabled, bool(selected))
        self._refresh_icon()
        self.start_btn.setEnabled(self._has_changes() and not self._busy())

    def _apply_card_style(self, key, disabled, selected):
        parts = self._rows[key]
        if disabled:
            style = (
                'QFrame#ModifyLoaderCard { background:rgba(255,255,255,0.025);'
                ' border:1px solid rgba(255,255,255,0.08); border-radius:9px; }'
                'QFrame#ModifyLoaderCard QLabel { background:transparent; border:none; }'
            )
        else:
            border = 'rgba(0,120,212,0.55)' if selected else 'rgba(255,255,255,0.18)'
            background = 'rgba(0,120,212,0.10)' if selected else 'rgba(18,20,26,0.58)'
            style = (
                f'QFrame#ModifyLoaderCard {{ background:{background};'
                f' border:1px solid {border}; border-radius:9px; }}'
                'QFrame#ModifyLoaderCard QLabel { background:transparent; border:none; }'
                'QFrame#LoaderHeader { background:transparent; border:none; border-radius:9px; }'
                'QFrame#LoaderHeader:hover { background:rgba(255,255,255,0.045); }'
            )
        parts['frame'].setStyleSheet(style)

    def _busy(self):
        return self._modify_worker is not None

    # ---------- 版本列表 ----------
    def _selected_value(self, key):
        if key in MAIN_LOADERS:
            return self._selected_version if self._selected_loader == key else ''
        if key == 'fabric_api':
            return self._selected_api if self._selected_loader == 'fabric' else ''
        if key == 'optifine':
            return self._selected_optifine
        return ''

    def _current_value(self, key):
        if key in MAIN_LOADERS:
            return self._current_version if self._current_loader == key else ''
        if key == 'fabric_api':
            return self._current_api if self._current_loader == 'fabric' else ''
        if key == 'optifine':
            return self._current_optifine
        return ''

    def _versions_for(self, key):
        versions = list(self._versions.get(key, []) or [])
        selected = self._selected_value(key)
        if selected and selected not in versions:
            versions.insert(0, selected)
        return versions

    def _version_tag(self, key, version):
        stable = self._stability.get(key, {}).get(version)
        if stable is True:
            return '稳定版'
        if stable is False:
            return '测试版'
        if key in ('forge', 'neoforge', 'optifine'):
            return '稳定版'
        return '可用版本'

    def _loader_icon_path(self, key):
        filename = LOADER_ICON_FILES.get(key)
        if not filename:
            return None
        path = IMAGES_DIR / filename
        return path if path.is_file() else None

    def _set_arrow(self, key, expanded):
        button = self._rows[key]['arrow']
        set_button_icon(
            button, 'chevron_up' if expanded else 'chevron_right',
            16, '#aab4c0',
        )
        button.setToolTip('收起版本列表' if expanded else '展开版本列表')

    def _populate_versions(self, key):
        parts = self._rows[key]
        versions = self._versions_for(key)
        selected = self._selected_value(key)
        signature = (tuple(versions), selected)
        if signature == parts['signature']:
            return
        parts['signature'] = signature
        listing = parts['list']
        listing.blockSignals(True)
        listing.clear()
        icon_path = self._loader_icon_path(key)
        current_item = None
        for version in versions:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, str(version))
            item.setSizeHint(QSize(10, 46))
            listing.addItem(item)
            widget = _VersionRowWidget(
                icon_path, version, self._version_tag(key, version),
                version == selected,
            )
            listing.setItemWidget(item, widget)
            if version == selected:
                current_item = item
        if current_item is not None:
            listing.setCurrentItem(current_item)
            listing.scrollToItem(current_item, QAbstractItemView.PositionAtCenter)
        listing.blockSignals(False)
        visible_rows = min(max(1, len(versions)), 5)
        listing.setFixedHeight(10 + 48 * visible_rows)
        listing.setVisible(bool(versions))

    def _set_row_expanded(self, key, expanded, animate=True):
        parts = self._rows[key]
        body = parts['body']
        previous = self._row_animations.pop(key, None)
        if previous is not None:
            try:
                previous.stop()
                previous.deleteLater()
            except RuntimeError:
                pass
        if expanded:
            if parts['expanded'] and body.isVisible():
                return
            parts['expanded'] = True
            self._populate_versions(key)
            target = max(1, parts['list'].height() + 12)
            self._set_arrow(key, True)
            if not animate or not self.isVisible():
                body.setMaximumHeight(16777215)
                body.show()
                return
            body.setMaximumHeight(0)
            body.show()
            animation = QPropertyAnimation(body, b'maximumHeight', self)
            animation.setDuration(160)
            animation.setStartValue(0)
            animation.setEndValue(target)
            animation.setEasingCurve(QEasingCurve.OutCubic)

            def finish_expand():
                if self._row_animations.get(key) is not animation:
                    return
                body.setMaximumHeight(16777215)
                self._row_animations.pop(key, None)

            animation.finished.connect(finish_expand)
            self._row_animations[key] = animation
            animation.start()
            return
        parts['expanded'] = False
        self._set_arrow(key, False)
        if not animate or not body.isVisible():
            body.hide()
            body.setMaximumHeight(16777215)
            return
        start = max(1, body.height())
        animation = QPropertyAnimation(body, b'maximumHeight', self)
        animation.setDuration(160)
        animation.setStartValue(start)
        animation.setEndValue(0)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def finish_collapse():
            if self._row_animations.get(key) is not animation:
                return
            body.hide()
            body.setMaximumHeight(16777215)
            self._row_animations.pop(key, None)

        animation.finished.connect(finish_collapse)
        self._row_animations[key] = animation
        animation.start()

    def _toggle_row(self, key):
        if self._busy():
            return
        parts = self._rows[key]
        if parts['disabled'] or not parts['header'].isEnabled():
            return
        if parts['expanded']:
            self._set_row_expanded(key, False)
            return
        if not parts.get('can_expand') or not self._versions_for(key):
            return
        self._set_row_expanded(key, True)

    def _select_version(self, key, version):
        if not version or self._busy():
            return
        version = str(version)
        if key in MAIN_LOADERS:
            preserve_api = (
                key == self._current_loader
                and key == 'fabric'
                and version == self._current_version
            )
            self._selected_loader = key
            self._selected_version = version
            if key == 'fabric':
                self._selected_api = self._current_api if preserve_api else ''
            else:
                self._selected_api = ''
            if key == 'neoforge':
                self._selected_optifine = ''
        elif key == 'fabric_api':
            self._selected_api = version
        elif key == 'optifine':
            self._selected_optifine = version
        self._set_row_expanded(key, False)
        self._refresh_rows()

    def _clear_selection(self, key):
        if self._busy():
            return
        if key in MAIN_LOADERS:
            self._selected_loader = ''
            self._selected_version = ''
            self._selected_api = ''
            self._selected_optifine = ''
        elif key == 'fabric_api':
            self._selected_api = ''
        elif key == 'optifine':
            self._selected_optifine = ''
        self._set_row_expanded(key, False)
        self._refresh_rows()

    # ---------- 名称修改 ----------
    def _name_value(self):
        value = self.name_edit.text().strip()
        return value or self.version_id

    def _name_pending(self):
        return self._name_value() != display_name(self.config, self.version_id)

    def _on_name_changed(self, text):
        self._update_original_label(text)
        self.start_btn.setEnabled(not self._busy() and self._has_changes())

    def _apply_name(self):
        value = self._name_value()
        nicknames = dict(self.config.get('version_nicknames', {}) or {})
        current = str(nicknames.get(self.version_id) or self.version_id)
        changed = value != current
        if changed:
            if value == self.version_id:
                nicknames.pop(self.version_id, None)
            else:
                nicknames[self.version_id] = value
            self.config.set('version_nicknames', nicknames)
            self.config.save()
            self.changed.emit(self.version_id)
        self._set_name_text(value)
        self._refresh_rows()
        return changed

    def _undo_name(self):
        self._set_name_text(self.version_id)
        self.start_btn.setEnabled(not self._busy() and self._has_changes())

    # ---------- 修改 ----------
    def _has_loader_changes(self):
        current_main = self._current_loader if self._current_loader in MAIN_LOADERS else ''
        target_main = self._selected_loader
        changed = False
        if target_main != current_main:
            changed = True
        elif target_main and self._selected_version != self._current_version:
            changed = True
        current_api = self._current_api if current_main == 'fabric' else ''
        target_api = self._selected_api if target_main == 'fabric' else ''
        if target_api != current_api:
            changed = True
        if self._selected_optifine != self._current_optifine:
            changed = True
        return changed

    def _has_changes(self):
        return self._has_loader_changes() or self._name_pending()

    def _on_start_modify(self):
        if self._busy():
            return
        name_changed = self._name_pending()
        loader_changed = self._has_loader_changes()
        if not name_changed and not loader_changed:
            self.status_label.setText('没有需要修改的内容')
            return
        target_name = self._name_value()
        standalone_optifine = bool(
            self._selected_optifine
            and not self._selected_loader
            and self._selected_optifine != self._current_optifine
        )
        if loader_changed:
            text = '请注意：\n• 如果该版本安装了 PCL 不兼容的加载器，或经过第三方修改，这些改动将会丢失！'
            if standalone_optifine:
                text += '\n• 优先独立安装 OptiFine，失败时自动改用 Forge 备用方案'
            elif self._selected_optifine and self._selected_loader == 'forge':
                text += '\n• 优先独立安装 OptiFine，失败时自动改用 mods 备用方案'
            if name_changed:
                text += f'\n• 版本名称将修改为：{target_name}'
            dialog = DarkQuestionDialog(
                self, '修改确认', text,
                actions=[('开始修改', 'ok', True), ('取消', 'cancel', False)],
                icon_text='!', icon_color='#d83b3b', title_color='#ff5a5a',
            )
        else:
            dialog = DarkQuestionDialog(
                self, '重命名确认',
                f'确定将版本名称修改为：\n{target_name}',
                actions=[('修改', 'ok', True), ('取消', 'cancel', False)],
                icon_text='✎', icon_color='#0078d4',
            )
        dialog.exec()
        if dialog.choice != 'ok':
            return
        if name_changed:
            self._apply_name()
            QTimer.singleShot(
                250, lambda: show_toast('重命名成功！', 'success', self)
            )
        if loader_changed:
            self._begin_modify()
        else:
            self.status_label.setText('版本名称已更新')
            self._refresh_rows()

    def _begin_modify(self):
        current_main = self._current_loader if self._current_loader in MAIN_LOADERS else ''
        current_api = self._current_api if current_main == 'fabric' else ''
        target_optifine = self._selected_optifine
        target_loader = self._selected_loader
        target_version = self._selected_version
        if target_optifine and target_loader not in ('', 'forge', 'fabric'):
            self.status_label.setText('OptiFine 不能与 NeoForge 同时使用')
            return
        target_api = self._selected_api if target_loader == 'fabric' else ''
        target_standalone = bool(target_optifine and not target_loader)
        fallback_forge_version = ''
        if target_standalone:
            forge_versions = [
                str(item) for item in self._versions.get('forge', []) if item
            ]
            fallback_forge_version = forge_versions[0] if forge_versions else ''
        restore_optifine_only = (
            self._current_loader == 'optifine'
            and not target_loader
            and not target_optifine
        )
        loader_changed = (
            target_loader != current_main
            or (target_loader and target_version != self._current_version)
            or restore_optifine_only
        )
        optifine_changed = target_optifine != self._current_optifine
        api_changed = target_api != current_api
        steps = []
        if loader_changed:
            steps.append('卸载版本加载器')
            if target_loader:
                steps.append('安装版本加载器')
            elif target_standalone:
                steps.append('安装独立 OptiFine')
            else:
                steps.append('恢复原版版本')
        elif target_standalone and optifine_changed:
            steps.append('安装独立 OptiFine')
        if target_loader:
            if optifine_changed or (loader_changed and target_optifine):
                steps.append('安装 OptiFine' if target_optifine else '移除 OptiFine')
        elif not loader_changed and optifine_changed and not target_optifine:
            steps.append('移除 OptiFine')
        if target_loader == 'fabric':
            if api_changed:
                steps.append('更新 Fabric API' if target_api else '移除 Fabric API')
        elif current_api:
            steps.append('移除 Fabric API')
        if not steps:
            return
        plan = {
            'mc_dir': self.mc_dir,
            'version_id': self.version_id,
            'base_mc': self._base_mc,
            'current_top_id': getattr(self, '_current_top_id', ''),
            'current_loader': self._current_loader,
            'current_version': self._current_version,
            'current_api': current_api,
            'current_optifine': self._current_optifine,
            'target_loader': target_loader,
            'target_version': target_version,
            'target_api': target_api,
            'target_optifine': target_optifine,
            'fallback_forge_version': fallback_forge_version,
        }
        self._set_busy(True)
        manager = DownloadManager.instance()
        task_id = f'modify-{self.version_id}-{int(time.time() * 1000)}'
        self._task_id = task_id
        manager.start_task(
            task_id, f'{display_name(self.config, self.version_id)} 修改加载器',
            steps=steps, cancel_callback=(lambda: self._modify_worker.cancel() if self._modify_worker else None),
        )
        manager.update_step(task_id, 0, state='active')
        worker = VersionModifyWorker(plan)
        self._modify_worker = worker
        worker.status.connect(
            (lambda text: (self.status_label.setText(text), manager.update_task(task_id, status=text)))
        )
        worker.progress.connect(
            (lambda value: self._update_progress(manager, task_id, value))
        )
        worker.done.connect(
            (lambda: self._on_modify_done(manager, task_id))
        )
        worker.error.connect(
            (lambda message: self._on_modify_error(manager, task_id, message))
        )
        worker.finished.connect(self._on_modify_finished)
        win = self.window()
        if hasattr(win, 'show_download_manager_page'):
            win.show_download_manager_page()
        self.status_label.setText('正在准备修改...')
        worker.start()

    def _update_progress(self, manager, task_id, value):
        task = manager.task(task_id)
        if not task:
            return
        steps = [dict(step) for step in task.get('steps', [])]
        count = max(1, len(steps))
        for index, step in enumerate(steps):
            start = index * 100 / count
            end = (index + 1) * 100 / count
            if value >= end:
                step['percent'] = 100.0
                step['state'] = 'done'
            elif value <= start:
                step['percent'] = 0.0
                step['state'] = 'pending'
            else:
                step['percent'] = round((value - start) * 100 / max(1, end - start), 1)
                step['state'] = 'active'
        manager.update_task(task_id, percent=float(value), steps=steps)

    def _on_modify_done(self, manager, task_id):
        manager.finish_task(task_id, True, '修改完成')
        self.status_label.setText('修改完成')
        self._load_meta()
        self._refresh_rows()
        self.changed.emit(self.version_id)
        show_toast('版本修改完成！', 'success', self)
        QTimer.singleShot(900, self._return_home)

    def _on_modify_error(self, manager, task_id, message):
        manager.finish_task(task_id, False, message)
        self.status_label.setText('修改失败')
        QMessageBox.warning(self, '修改失败', message)
        win = self.window()
        try:
            if hasattr(win, 'page_stack') and getattr(win, 'game_settings_page', None):
                win.page_stack.setCurrentWidget(win.game_settings_page)
        except Exception:
            pass

    def _on_modify_finished(self):
        worker = self.sender()
        if worker is self._modify_worker:
            self._modify_worker = None
        self._set_busy(False)
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self._refresh_rows()

    def _set_busy(self, busy):
        self.name_edit.setEnabled(not busy)
        self.undo_btn.setEnabled(not busy and self.original_label.isVisible())
        self.start_btn.setEnabled(not busy and self._has_changes())
        for parts in self._rows.values():
            parts['remove'].setEnabled(not busy)
            parts['arrow'].setEnabled(not busy)
            parts['header'].setEnabled(not busy and not parts['disabled'])

    def _return_home(self):
        win = self.window()
        try:
            if hasattr(win, 'close_game_settings'):
                win.close_game_settings()
            if hasattr(win, '_switch_page'):
                win._switch_page(0)
        except Exception:
            pass

    # ---------- 版本列表加载 ----------
    def _needed_version_keys(self):
        keys = ('forge', 'neoforge', 'fabric', 'fabric_api', 'optifine')
        return [key for key in keys if loader_supported(self._base_mc, key)]

    def _update_version_loading(self):
        if not self.loading_overlay.is_loading():
            return
        needed = self._needed_version_keys()
        if not needed:
            self.loading_overlay.finish()
            return
        loaded = sum(1 for key in needed if key in self._loaded_keys)
        self.loading_overlay.set_progress(int(loaded * 100 / len(needed)))
        if loaded >= len(needed):
            self._finish_version_loading()

    def _finish_version_loading(self):
        self._loading_watchdog.stop()
        if not self.loading_overlay.is_loading():
            return
        needed = self._needed_version_keys()
        if needed and all(not self._versions.get(key) for key in needed):
            reason = (
                '未连接到互联网，无法获取加载器版本'
                if not network_available()
                else '加载器版本获取失败，请稍后重试'
            )
            try:
                self.content_scroll.show()
            except RuntimeError:
                pass
            self.loading_overlay.fail(reason)
        else:
            self.loading_overlay.finish()

    def _start_versions_worker(self):
        if not self._base_mc:
            self._versions_finished = True
            self.loading_overlay.finish()
            return
        try:
            self.content_scroll.hide()
        except RuntimeError:
            pass
        self.loading_overlay.start_auto('正在加载加载器版本', 92)
        self._versions_worker = LoadVersionsWorker(
            self._base_mc, load_optional=True, load_optifabric=False
        )
        self._versions_worker.partial.connect(self._on_versions_loaded)
        self._versions_worker.finished.connect(self._on_versions_finished)
        self._versions_worker.start()
        self._loading_watchdog.start()
        self.status_label.setText('')

    def _on_versions_loaded(self, versions):
        payload = dict(versions or {})
        payload.pop('optifine_objects', None)
        for key in LOADER_ORDER:
            if key in payload:
                self._versions[key] = list(payload[key] or [])
                self._loaded_keys.add(key)
        worker = self._versions_worker
        if worker is not None:
            try:
                stability = getattr(worker, 'stability', {}) or {}
                for key in LOADER_ORDER:
                    values = stability.get(key) or {}
                    if values:
                        self._stability[key].update(values)
            except RuntimeError:
                pass
        self.status_label.setText('')
        self._refresh_rows()
        self._update_version_loading()

    def _on_versions_finished(self, versions):
        self._versions_finished = True
        self._loaded_keys.update(LOADER_ORDER)
        self._on_versions_loaded(versions)
        self.status_label.setText('')
        self._finish_version_loading()

    def _on_loading_watchdog(self):
        '''版本列表加载超过 30 秒时不再等待，直接放行页面，避免卡在加载中。'''

        if not self.loading_overlay.is_loading():
            return
        self._versions_finished = True
        self._loaded_keys.update(LOADER_ORDER)
        self._refresh_rows()
        self._finish_version_loading()

    def shutdown(self):
        try:
            self.loading_overlay.stop()
        except RuntimeError:
            pass
        try:
            self._loading_watchdog.stop()
        except RuntimeError:
            pass
        for attr in ('_versions_worker', '_modify_worker'):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    cancel = getattr(worker, 'cancel', None)
                    if callable(cancel):
                        cancel()
                    worker.wait(2500)
            except RuntimeError:
                pass
            setattr(self, attr, None)
