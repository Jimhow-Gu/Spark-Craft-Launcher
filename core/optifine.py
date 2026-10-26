'''
OptiFine 版本查询、下载与安装
- 默认方式 A：运行 OptiFine 官方安装器生成独立版本（不依赖 Forge）
- 备用方式 B：安装 Forge 后将 OptiFine 作为模组放入 mods
'''

from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import (Callable, Dict, List, Optional)
from urllib.parse import quote
from core.logging_setup import LOG_DIR
from core.paths import safe_rmtree
from core.net import (DownloadCancelled, DownloadError, HttpClient)
BMCLAPI = 'https://bmclapi2.bangbang93.com'
OPENBMCLAPI = 'https://openbmclapi.bangbang93.com'

@dataclass
class OptiFineVersion:
    id: 'str'
    mcversion: 'str'
    type: 'str'
    patch: 'str'
    filename: 'str'
    forge_raw: 'str' = 'Forge N/A'
    @property
    def is_preview(self):
        return self.patch.lower().startswith('pre') or 'preview' in self.filename.lower()

    @property
    def needs_forge(self):
        raw = (self.forge_raw or '').strip()
        return bool(raw) and 'n/a' not in raw.lower()

    @property
    def edition(self):
        return f'{self.type}_{self.patch}'

    def display(self):
        suffix = ' [预览]' if self.is_preview else ''
        return f'{self.mcversion} {self.type} {self.patch}{suffix}'


def _natural_key(text):
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split('(\\d+)', text)]

class OptiFineManager:
    def __init__(self):
        self.http = HttpClient(timeout=20)
        # 列表类请求用短超时，避免慢镜像拖慢加载页面
        self.http_fast = HttpClient(timeout=8, retries=1)
        self._cache = {}
        self._forge_cache = {}

    def _base_mc_version(self, mc_version):
        match = re.search('(?<!\\d)(\\d+\\.\\d+(?:\\.\\d+)?)', str(mc_version))
        if match:
            return match.group(1)
        return str(mc_version)

    def _official_versions(self, mc_version):
        '''官方下载页兜底解析（BMCLAPI 暂时缺少版本时使用）。'''

        try:
            resp = self.http_fast.get('https://optifine.net/downloads', headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://optifine.net/'}, timeout=10)
            text = resp.text
        except Exception:
            return []
        rows = []
        seen = set()
        pattern = re.compile('(?:preview_)?OptiFine_[A-Za-z0-9._+\\-]+\\.jar')
        for filename in pattern.findall(text):
            clean = filename[:-4]
            preview = clean.startswith('preview_')
            if preview:
                clean = clean[len('preview_'):]
            match = re.match('OptiFine_(.+?)_([A-Za-z0-9_]+?)(?:_pre(\\d+))?$', clean)
            if not match:
                continue
            mc, rest, pre_num = match.groups()
            if mc != mc_version:
                continue
            if pre_num:
                typ = rest
                patch = f'pre{pre_num}'
            else:
                parts = rest.rsplit('_', 1)
                if len(parts) != 2:
                    continue
                typ, patch = parts
            key = mc, typ, patch
            if key in seen:
                continue
            seen.add(key)
            rows.append({'mcversion': mc, 'type': typ, 'patch': patch, 'filename': filename, 'forge': 'Forge N/A'})
        return rows

    def _bmclapi_optifine_rows(self, mc_version):
        '''从 BMCLAPI 获取 OptiFine 版本列表（只保留匹配该 MC 版本的行）。'''

        for url in (
            f'{BMCLAPI}/optifine/{mc_version}',
            f'{OPENBMCLAPI}/optifine/{mc_version}',
            f'{BMCLAPI}/optifine/versionList',
            f'{OPENBMCLAPI}/optifine/versionList',
        ):
            try:
                data = self.http_fast.get_json(url, timeout=8)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                mc = str(item.get('mcversion') or '')
                if mc and mc == str(mc_version):
                    rows.append(item)
            if rows:
                return rows
        return []

    def get_versions(self, mc_version):
        if mc_version in self._cache:
            return self._cache[mc_version]
        rows = []
        candidates = [str(mc_version)]
        base = self._base_mc_version(mc_version)
        if base and base not in candidates:
            candidates.append(base)
        for candidate in candidates:
            rows = self._bmclapi_optifine_rows(candidate)
            if not rows:
                rows = self._official_versions(candidate)
            if rows:
                break
        versions = []
        seen = set()
        for row in rows:
            mc = str(row.get('mcversion') or mc_version)
            typ = str(row.get('type') or 'HD_U')
            patch = str(row.get('patch') or '')
            filename = str(row.get('filename') or f'OptiFine_{mc}_{typ}_{patch}.jar')
            if not patch:
                continue
            vid = f'{mc}_{typ}_{patch}'
            if vid in seen:
                continue
            seen.add(vid)
            versions.append(OptiFineVersion(id=vid, mcversion=mc, type=typ, patch=patch, filename=filename, forge_raw=str(row.get('forge') or 'Forge N/A')))
        stable = [v for v in versions if not v.is_preview]
        preview = [v for v in versions if v.is_preview]
        stable.sort(key=(lambda v: _natural_key(f'{v.type}_{v.patch}')), reverse=True)
        preview.sort(key=(lambda v: _natural_key(f'{v.type}_{v.patch}')), reverse=True)
        result = stable + preview
        if result:
            self._cache[mc_version] = result
        return result

    def find(self, mc_version, version_id):
        for ver in self.get_versions(mc_version):
            if ver.id == version_id:
                return ver
        for ver in self.get_versions(mc_version):
            if version_id in (ver.patch, f'{ver.type}_{ver.patch}'):
                return ver

    def get_all_versions(self):
        data = None
        for url in f'{BMCLAPI}/optifine/versionList', f'{OPENBMCLAPI}/optifine/versionList':
            try:
                data = self.http_fast.get_json(url, timeout=8)
            except Exception:
                continue
            if isinstance(data, list):
                break
        if not isinstance(data, list):
            return []
        result = []
        seen = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            mc = str(row.get('mcversion') or '')
            typ = str(row.get('type') or 'HD_U')
            patch = str(row.get('patch') or '')
            if not mc or not patch:
                continue
            vid = f'{mc}_{typ}_{patch}'
            if vid in seen:
                continue
            seen.add(vid)
            result.append(OptiFineVersion(id=vid, mcversion=mc, type=typ, patch=patch, filename=str(row.get('filename') or f'OptiFine_{mc}_{typ}_{patch}.jar'), forge_raw=str(row.get('forge') or 'Forge N/A')))
        result.sort(key=(lambda v: (_natural_key(v.mcversion), v.is_preview, _natural_key(v.patch))), reverse=True)
        return result

    def _forge_list(self, mc_version):
        if mc_version in self._forge_cache:
            return self._forge_cache[mc_version]
        rows = []
        for url in f'{BMCLAPI}/forge/minecraft/{mc_version}', f'{OPENBMCLAPI}/forge/minecraft/{mc_version}':
            try:
                data = self.http_fast.get_json(url, timeout=8)
            except Exception:
                continue
            if isinstance(data, list) and data:
                rows = data
                break
        self._forge_cache[mc_version] = rows
        return rows

    def resolve_forge_version(self, optifine):
        '''返回与 OptiFine 推荐的 Forge 版本（不带 MC 前缀）。'''

        if not optifine.needs_forge:
            return
        raw = (optifine.forge_raw or '').strip()
        rows = self._forge_list(optifine.mcversion)
        build_match = re.search('#\\s*(\\d+)', raw)
        if build_match:
            build = int(build_match.group(1))
            for row in rows:
                try:
                    if int(row.get('build') or -1) == build:
                        return str(row.get('version') or '')
                except (TypeError, ValueError):
                    continue
            candidates = []
            for row in rows:
                try:
                    b = int(row.get('build') or -1)
                    if b >= build:
                        candidates.append((b, str(row.get('version') or '')))
                except (TypeError, ValueError):
                    continue
            if candidates:
                candidates.sort()
                return candidates[0][1]
        ver_match = re.search('Forge\\s+([\\d][\\w.\\-]*)', raw)
        if ver_match:
            return ver_match.group(1)
        if rows:
            def key(row):
                v = str(row.get('version') or '')
                return _natural_key(v)

            rows = sorted(rows, key=key)
            return str(rows[-1].get('version') or '') or None

    def latest_forge_version(self, mc_version):
        '''返回该游戏版本可用的最新 Forge 版本，用于 OptiFine 备用安装。'''

        rows = self._forge_list(mc_version)
        if not rows:
            return
        def key(row):
            return _natural_key(str(row.get('version') or ''))

        rows = sorted(rows, key=key)
        return str(rows[-1].get('version') or '') or None

    @staticmethod
    def _is_jar(path):
        try:
            if not Path(path).is_file() or Path(path).stat().st_size <= 1024:
                return False
            with zipfile.ZipFile(path) as zf:
                if zf.testzip() is not None:
                    return False
                names = zf.namelist()
                return (
                    'META-INF/MANIFEST.MF' in names
                    or any(name.endswith('.class') for name in names)
                    or any(name.startswith('patch/') for name in names)
                )
        except (OSError, zipfile.BadZipFile):
            return False

    def download_jar(self, optifine, dest, progress=None, cancel=None):
        filename = quote(optifine.filename)
        urls = [(f'{BMCLAPI}/optifine/{optifine.mcversion}/{optifine.type}/{optifine.patch}', {}), (f'{BMCLAPI}/maven/com/optifine/{optifine.mcversion}/{quote(optifine.filename)}', {}), (f'{OPENBMCLAPI}/optifine/{optifine.mcversion}/{optifine.type}/{optifine.patch}', {}), (f'{OPENBMCLAPI}/maven/com/optifine/{optifine.mcversion}/{quote(optifine.filename)}', {}), (f'https://optifine.net/downloadx?f={filename}', {'Referer': 'https://optifine.net/downloads', 'User-Agent': 'Mozilla/5.0'})]
        last_error = None
        for url, headers in urls:
            try:
                if dest.exists() and not self._is_jar(dest):
                    dest.unlink(missing_ok=True)
                self.http.download(url, dest, progress=progress, cancel=cancel, headers=headers)
                if dest.exists() and dest.stat().st_size > 1024 and self._is_jar(dest):
                    return dest
            except DownloadCancelled:
                raise
            except Exception as exc:
                last_error = exc
                continue
        raise DownloadError(f'OptiFine 下载失败: {last_error}')

    def install_standalone(self, mc_dir, mc_version, jar_path, java_path, *, status=None, cancel=None):
        '''运行 OptiFine 安装器，返回生成的版本目录名。'''

        mc_dir = Path(mc_dir)
        vanilla_json = mc_dir / 'versions' / mc_version / f'{mc_version}.json'
        vanilla_jar = mc_dir / 'versions' / mc_version / f'{mc_version}.jar'
        if not (vanilla_json.exists() and vanilla_jar.exists()):
            raise DownloadError(f'安装 OptiFine 前需要先安装原版 {mc_version}')
        profiles = mc_dir / 'launcher_profiles.json'
        if not profiles.exists():
            with open(profiles, 'w', encoding='utf-8') as fh:
                json.dump({'profiles': {}}, fh, ensure_ascii=False, indent=2)
        if status:
            status('正在运行 OptiFine 安装器...')
        before = {p.name for p in (mc_dir / 'versions').iterdir() if p.is_dir()}
        temp_root = Path(os.environ.get('TEMP', '.')) / 'pmgl_optifine_home'
        safe_rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)
        link = temp_root / '.minecraft'
        created_link = False
        if sys.platform == 'win32':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(mc_dir)], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0:
                safe_rmtree(temp_root)
                raise DownloadError(f'无法创建安装器所需的临时目录联接: {result.stderr or result.stdout}')
            created_link = True
        else:
            link = temp_root
            (link / '.minecraft').symlink_to(mc_dir, target_is_directory=True)
            created_link = True
        env = os.environ.copy()
        if sys.platform == 'win32':
            env['APPDATA'] = str(temp_root)
        else:
            env['HOME'] = str(temp_root)
        log_file = LOG_DIR / 'optifine_install.log'
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            java_exe = str(java_path or 'java')
            if java_exe.lower().endswith('javaw.exe'):
                console_java = java_exe[:-len('javaw.exe')] + 'java.exe'
                if Path(console_java).is_file():
                    java_exe = console_java
            if cancel and cancel():
                raise DownloadCancelled()
            with open(log_file, 'w', encoding='utf-8', errors='replace') as log:
                try:
                    result = subprocess.run(
                        [java_exe, '-Djava.awt.headless=true', '-cp', str(jar_path), 'optifine.Installer'],
                        cwd=str(link.parent), env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=240,
                        creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0),
                    )
                except subprocess.TimeoutExpired as exc:
                    raise DownloadError('OptiFine 安装器运行超时') from exc
                except FileNotFoundError as exc:
                    raise DownloadError(f'找不到用于运行 OptiFine 安装器的 Java：{java_exe}') from exc
            if cancel and cancel():
                raise DownloadCancelled()
            if result.returncode != 0:
                tail = ''
                try:
                    tail = log_file.read_text(encoding='utf-8', errors='replace')[-1500:]
                except OSError:
                    pass
                raise DownloadError(
                    f'OptiFine 安装器运行失败（退出码 {result.returncode}）'
                    + (f'：{tail}' if tail else '')
                )
            after = {p.name for p in (mc_dir / 'versions').iterdir() if p.is_dir()}
            candidates = []
            for name in after - before:
                version_json = mc_dir / 'versions' / name / f'{name}.json'
                if version_json.is_file():
                    candidates.append(name)
            if not candidates:
                stem = Path(jar_path).stem
                if stem.startswith('OptiFine_'):
                    parts = stem[len('OptiFine_'):].split('_', 1)
                    if len(parts) == 2:
                        expected = f'{parts[0]}-OptiFine_{parts[1]}'
                        version_json = mc_dir / 'versions' / expected / f'{expected}.json'
                        if version_json.is_file():
                            candidates.append(expected)
            if not candidates:
                raise DownloadError('OptiFine 安装器没有生成版本文件')
            candidates.sort(
                key=(lambda name: (mc_dir / 'versions' / name / f'{name}.json').stat().st_mtime),
                reverse=True,
            )
            if status:
                status(f'OptiFine 独立版本已生成：{candidates[0]}')
            return candidates[0]
        finally:
            if created_link:
                if sys.platform == 'win32':
                    subprocess.run(['cmd', '/c', 'rmdir', str(link)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    try:
                        link.unlink()
                    except Exception:
                        pass
            safe_rmtree(temp_root)

    @staticmethod
    def install_as_mod(jar_path, mods_dir):
        mods_dir = Path(mods_dir)
        mods_dir.mkdir(parents=True, exist_ok=True)
        dest = mods_dir / Path(jar_path).name
        shutil.copy2(jar_path, dest)
        return dest


# WARNING: Decompyle incomplete
