'''
Forge 运行时组件补全。

Forge 1.17+ 的启动器只把 Forge 的版本 JSON 复制进版本目录，真正启动所需的
net.minecraftforge:forge:<version> 的 universal/client 两个 jar 位于 libraries 中，
由 Forge 安装器的处理器生成，不会出现在 JSON 的 libraries 列表里。
复制自其他启动器的版本常常缺这两个文件，本模块负责按官方 install_profile 的
处理器链补齐（逻辑参考 HMCL 的 ForgeNewInstallTask，独立实现）。
'''

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from core.downloading import DownloadClient, RemoteFile, maven_path_urls
from core.paths import safe_rmtree
from core.net import DownloadCancelled, DownloadError, hash_file

FORGE_GROUP = 'net.minecraftforge'


def _quote(value):
    value = str(value or '').strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _coord_path(coord):
    coord = str(coord or '').strip()
    if coord.startswith('[') and coord.endswith(']'):
        coord = coord[1:-1]
    name, _, ext = coord.partition('@')
    parts = name.split(':')
    if len(parts) < 3:
        return ''
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ''
    filename = f'{artifact}-{version}'
    if classifier:
        filename += f'-{classifier}'
    filename += f'.{ext or "jar"}'
    return '/'.join(group.split('.')) + f'/{artifact}/{version}/{filename}'


def _resolve_data_value(value, installer, mc_dir, temp_dir):
    value = str(value or '').strip()
    if not value:
        return ''
    if value.startswith('[') and value.endswith(']'):
        path = _coord_path(value)
        return str(mc_dir / 'libraries' / Path(*path.split('/'))) if path else ''
    if value.startswith('/') and not value.startswith('//'):
        member = value.lstrip('/')
        dest = temp_dir / Path(*member.split('/'))
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(installer) as zf:
                dest.write_bytes(zf.read(member))
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise DownloadError(f'无法从 Forge 安装器提取 {member}: {exc}') from exc
        return str(dest)
    return _quote(value)


def _expand(value, variables):
    text = str(value)
    return re.sub(r'\{([A-Za-z0-9_]+)\}', lambda match: str(variables.get(match.group(1), match.group(0))), text)


def _expand_processor_arg(value, variables, mc_dir):
    text = _expand(value, variables).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    if text.startswith('[') and text.endswith(']'):
        path = _coord_path(text)
        if path:
            return str(mc_dir / 'libraries' / Path(*path.split('/')))
    return text


def _read_main_class(jar_path):
    try:
        with zipfile.ZipFile(jar_path) as zf:
            manifest = zf.read('META-INF/MANIFEST.MF').decode('utf-8', 'replace')
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DownloadError(f'无法读取处理器 {Path(jar_path).name}: {exc}') from exc
    for line in manifest.splitlines():
        if line.lower().startswith('main-class:'):
            return line.split(':', 1)[1].strip()
    raise DownloadError(f'处理器 {Path(jar_path).name} 缺少 Main-Class')


def _arg_outputs(args):
    outputs = []
    for index, arg in enumerate(args):
        if arg in ('--output', '--slim', '--extra') and index + 1 < len(args):
            outputs.append(args[index + 1])
    return outputs


def _artifact_ok(path):
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if path.suffix.lower() in ('.jar', '.zip') and not zipfile.is_zipfile(path):
            return False
        return True
    except OSError:
        return False


def _hash_ok(path, expected):
    try:
        if not _artifact_ok(path):
            return False
        if expected and hash_file(path, 'sha1') != str(expected).lower():
            return False
        return True
    except OSError:
        return False


def _run_processor(java, classpath, main_class, args, mc_dir, log_cb, cancel):
    if cancel and cancel():
        raise DownloadCancelled()
    command = [java, '-cp', os.pathsep.join(str(item) for item in classpath), main_class] + [str(item) for item in args]
    logger = log_cb or (lambda _s: None)
    logger(f'运行 Forge 处理器: {main_class}')
    try:
        proc = subprocess.Popen(
            command, cwd=str(mc_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except OSError as exc:
        raise DownloadError(f'无法启动 Forge 处理器: {exc}') from exc
    lines = []
    assert proc.stdout is not None
    while True:
        if cancel and cancel():
            try:
                proc.kill()
            except OSError:
                pass
            raise DownloadCancelled()
        chunk = proc.stdout.readline()
        if chunk:
            text = chunk.decode('utf-8', 'replace').rstrip()
            if text:
                lines.append(text)
                if len(lines) > 40:
                    lines.pop(0)
            continue
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    if proc.returncode != 0:
        tail = '\n'.join(lines[-12:])
        raise DownloadError(f'Forge 处理器 {main_class} 失败（退出码 {proc.returncode}）\n{tail}')


def _prefetch_libraries(installer, mc_dir, status_cb, progress_cb, cancel):
    from core.installers import prefetch_installer_libraries
    status_cb('正在补全 Forge 安装器依赖...')
    prefetch_installer_libraries(
        installer, mc_dir, status_cb=status_cb,
        progress_cb=(lambda value: progress_cb(int(value)) if progress_cb else None),
        cancel=cancel,
    )


def ensure_forge_artifacts(mc_version, forge_version, mc_dir, *, minecraft_jar=None,
                           status_cb=(lambda _s: None), progress_cb=None,
                           log_cb=(lambda _s: None), cancel=(lambda: False)):
    '''确保 Forge universal/client 两个运行时 jar 存在；返回是否新生成。'''

    mc_dir = Path(mc_dir)
    full_version = str(forge_version)
    if not full_version.startswith(f'{mc_version}-'):
        full_version = f'{mc_version}-{full_version}'
    artifact_dir = mc_dir / 'libraries' / 'net' / 'minecraftforge' / 'forge' / full_version
    universal = artifact_dir / f'forge-{full_version}-universal.jar'
    client = artifact_dir / f'forge-{full_version}-client.jar'
    if _artifact_ok(universal) and _artifact_ok(client):
        return False
    if client.is_file() and not _artifact_ok(client):
        client.unlink(missing_ok=True)

    client_downloader = DownloadClient(mc_dir, fast=False)
    try:
        if not _artifact_ok(universal):
            status_cb(f'正在补全 Forge {full_version} universal...')
            path = f'net/minecraftforge/forge/{full_version}/forge-{full_version}-universal.jar'
            remote = RemoteFile(
                label=f'Forge {full_version} universal', dest=universal,
                urls=maven_path_urls(path, f'https://maven.minecraftforge.net/{path}'),
                check_zip=True,
            )
            client_downloader.download(remote, cancel=cancel)
        if _artifact_ok(client):
            return True
        from core.installers import _download_installer, _forge_installer_urls, _prefetch_mojmaps, _read_forge_profile, pick_java, PROJECT_TEMP
        installer = PROJECT_TEMP / 'forge_installer' / f'forge-installer-{full_version}-repair.jar'
        installer.parent.mkdir(parents=True, exist_ok=True)
        status_cb(f'正在获取 Forge {full_version} 安装器以生成客户端补丁...')
        from core.net import HttpClient
        _download_installer(_forge_installer_urls(mc_version, full_version[len(mc_version) + 1:]), installer, http=HttpClient(timeout=60, retries=2), status_cb=status_cb, cancel=cancel)
        progress_base = 0
        def prefetch_progress(value):
            if progress_cb:
                progress_cb(int(progress_base + value * 0.5))
        _prefetch_libraries(installer, mc_dir, status_cb, prefetch_progress, cancel)
        profile = _read_forge_profile(installer)
        if not profile or not profile.get('processors'):
            raise DownloadError('Forge 安装器缺少处理器信息，无法生成客户端补丁')
        if not minecraft_jar:
            candidate = mc_dir / 'versions' / mc_version / f'{mc_version}.jar'
            minecraft_jar = candidate if candidate.is_file() else None
        if not minecraft_jar or not Path(minecraft_jar).is_file():
            raise DownloadError('缺少原版客户端 JAR，无法生成 Forge 客户端补丁')
        try:
            _prefetch_mojmaps(profile, mc_dir, status_cb=status_cb, cancel=cancel)
        except Exception as exc:
            log_cb(f'Mojang 映射预下载失败: {exc}')
        temp_dir = Path(tempfile.mkdtemp(prefix='pmgl_forge_', dir=str(PROJECT_TEMP)))
        try:
            variables = {
                'SIDE': 'client',
                'MINECRAFT_JAR': str(minecraft_jar),
                'MINECRAFT_VERSION': str(mc_version),
                'ROOT': str(mc_dir),
                'INSTALLER': str(installer),
                'LIBRARY_DIR': str(mc_dir / 'libraries'),
            }
            for key, entry in (profile.get('data') or {}).items():
                if not isinstance(entry, dict):
                    continue
                raw = entry.get('client', entry.get('server', ''))
                try:
                    variables[key] = _resolve_data_value(raw, installer, mc_dir, temp_dir)
                except DownloadError:
                    variables[key] = ''
            java = pick_java(mc_version)
            processors = profile.get('processors') or []
            total = max(1, len(processors))
            done = 0
            for step in processors:
                if cancel():
                    raise DownloadCancelled()
                sides = step.get('sides') or []
                if sides and 'client' not in sides:
                    continue
                args = [_expand_processor_arg(arg, variables, mc_dir) for arg in step.get('args') or []]
                if any('DOWNLOAD_MOJMAPS' in str(item) for item in args):
                    done += 1
                    continue
                outputs = step.get('outputs') or {}
                verified = True
                for out_key, sha_key in outputs.items():
                    out_path = _expand(out_key, variables)
                    expected = _expand(sha_key, variables)
                    if not _hash_ok(out_path, expected):
                        verified = False
                        break
                if outputs and verified:
                    done += 1
                    if progress_cb:
                        progress_cb(50 + int(done * 50 / total))
                    continue
                jar_coord = str(step.get('jar') or '')
                jar_path = _coord_path(jar_coord)
                if not jar_path:
                    raise DownloadError(f'Forge 处理器坐标无效: {jar_coord}')
                classpath = [mc_dir / 'libraries' / Path(*jar_path.split('/'))]
                for coord in step.get('classpath') or []:
                    path = _coord_path(coord)
                    if path:
                        classpath.append(mc_dir / 'libraries' / Path(*path.split('/')))
                if not classpath[0].is_file():
                    raise DownloadError(f'缺少 Forge 处理器: {classpath[0].name}')
                main_class = _read_main_class(classpath[0])
                if not outputs:
                    targets = _arg_outputs(args)
                    if targets and all(_artifact_ok(target) for target in targets):
                        done += 1
                        continue
                status_cb(f'正在生成 Forge 客户端补丁（{done + 1}/{total}）...')
                _run_processor(java, classpath, main_class, args, mc_dir, log_cb, cancel)
                done += 1
                if progress_cb:
                    progress_cb(50 + int(done * 50 / total))
            expected = str(variables.get('PATCHED_SHA') or '').strip()
            if client.is_file() and expected and hash_file(client, 'sha1') != expected.lower():
                client.unlink(missing_ok=True)
                raise DownloadError('生成的 Forge 客户端补丁校验失败')
            if not _artifact_ok(client):
                raise DownloadError('Forge 处理器未生成有效的客户端补丁')
            return True
        finally:
            safe_rmtree(temp_dir, ignore_errors=True)
    finally:
        client_downloader.close()


__all__ = ['ensure_forge_artifacts']
