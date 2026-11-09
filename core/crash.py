'''Minecraft 启动失败原因分析与日志导出'''

from __future__ import annotations
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import (Iterable, List, Optional)
logger = logging.getLogger('Crash')

@dataclass
class CrashReport:
    version_name: 'str'
    exit_code: 'int'
    reasons: 'List[str]' = field(default_factory=list)
    detail: 'str' = ''
    window_seen: 'bool' = False
    @property
    def title(self):
        return '游戏崩溃' if self.window_seen else '游戏启动失败'

    @property
    def generic_message(self):
        if self.window_seen:
            return f'{self.version_name} 运行中崩溃了，已经输出日志，如果你要寻求帮助请提供对应日志而不是截图！'
        return f'{self.version_name}启动时崩溃了，已经输出日志，如果你要寻求帮助请提供对应日志而不是截图！'

    def message(self):
        if self.reasons:
            prefix = (
                f'{self.version_name} 运行中崩溃（退出码 {self.exit_code}）。'
                if self.window_seen else
                f'{self.version_name} 启动失败（退出码 {self.exit_code}）。'
            )
            return prefix + '\n\n可能的原因：\n- ' + '\n- '.join(self.reasons) + '\n\n如果你要寻求帮助，请导出日志而不是只提供截图。'
        return self.generic_message


def _add(reasons, text):
    text = (text or '').strip()
    if text and text not in reasons:
        reasons.append(text)

def analyze_crash(log_lines, version_name, exit_code, window_seen=False):
    lines = [str(line) for line in log_lines or []]
    text = '\n'.join(lines)
    lower = text.lower()
    report = CrashReport(
        version_name=version_name,
        exit_code=exit_code,
        window_seen=bool(window_seen),
    )
    reasons = report.reasons
    if any((key in lower for key in ('incompatible mod set', 'modresolutionexception', 'missing or unsupported mandatory dependencies', 'duplicate mods', 'mod loading error', 'modloadingexception'))):
        _add(reasons, '检测到 Mod 不兼容或缺少前置依赖（Mod 冲突）')
    mod_conflicts = re.findall("Mod ID:\\s*'?([A-Za-z0-9_.\\-]+)'?.*?requires\\s+([^\\n]+)", text, re.IGNORECASE)
    for mod_id, requirement in mod_conflicts[:6]:
        _add(reasons, f'Mod「{mod_id}」缺少依赖或不兼容：{requirement.strip()[:120]}')
    for match in re.finditer("Mod\\s+'([^']+)'.*?requires\\s+(?:version\\s+[^\\s]+\\s+of\\s+)?mod\\s+'([^']+)'", text, re.IGNORECASE):
        _add(reasons, f'Mod「{match.group(1)}」需要 Mod「{match.group(2)}」')
    # 只有日志明确写出“需要/缺少 Fabric API”时才提示。旧逻辑只要日志里
    # 同时出现 fabric-api 和任意一个 missing/requires 就认定缺失，已安装
    # Fabric API 时也会误报。
    fabric_api_missing = re.search(
        r'(?i)(?:'
        r"(?:install|add)\s+['\\\"]?fabric[-_ ]?api"
        r"|fabric[-_ ]?api['\\\"]?\s+(?:is\s+)?(?:missing|not installed)"
        r'|(?:missing|not installed)[^\n]{0,60}fabric[-_ ]?api'
        r'|requires[^\n]{0,90}fabric[-_ ]?api[^\n]{0,50}(?:missing|not installed|but)'
        r')',
        text,
    )
    if fabric_api_missing:
        _add(reasons, '缺少 Fabric API，或已安装的 Fabric API 版本不匹配')
    optifine_conflict = re.search(
        r'(?i)(?:optifine[^\n]{0,140}(?:incompatible|conflict|failed|crash|exception)'
        r'|(?:incompatible|conflict|failed|crash|exception)[^\n]{0,140}optifine)',
        text,
    )
    for key, reason in (('mixin apply failed', 'Mixin 注入失败，通常是 Mod 与游戏/其他 Mod 版本不兼容'), ('invalidmixinexception', 'Mixin 配置无效，通常是 Mod 与游戏版本不兼容'), ('mixintransformererror', 'Mixin 转换失败，通常是 Mod 不兼容')):
        if key in lower:
            _add(reasons, reason)
    if optifine_conflict:
        _add(reasons, 'OptiFine 与当前 Mod/光影存在兼容问题')
    if 'unsupportedclassversionerror' in lower or 'class file version' in lower:
        _add(reasons, 'Java 版本过低或过高，请切换与 Minecraft 版本匹配的 Java')
    java_match = re.search('requires (?:java|jdk)[^\\d]*(\\d+)', text, re.IGNORECASE)
    if java_match:
        _add(reasons, f'当前 Java 版本不满足要求，游戏需要 Java {java_match.group(1)}+')
    if 'outofmemoryerror' in lower or 'java heap space' in lower or 'gc overhead' in lower:
        _add(reasons, '游戏内存不足，请在游戏设置中调大最大内存或减少 Mod 数量')
    if any((key in lower for key in ('pixel format not accelerated', 'glfw error 65542', 'failed to create window', 'could not initialize glfw', 'opengl error', 'unsupported opengl'))):
        _add(reasons, '显卡驱动或 OpenGL 初始化失败，请更新显卡驱动并确认使用高性能显卡')
    if any((key in lower for key in ('authenticationexception', 'invalid credentials', 'failed to login', 'invalid session', 'unauthorized'))):
        _add(reasons, '账号登录验证失败，请重新登录或检查正版账号状态')
    missing = re.findall('(?:java\\.lang\\.)?(?:NoClassDefFoundError|ClassNotFoundException):\\s*([^\\s\\n]+)', text)
    if missing:
        names = ', '.join(dict.fromkeys(missing[:4]))
        _add(reasons, f'运行库或 Mod 依赖类缺失/版本不匹配：{names}')
    if any((key in lower for key in ('nosuchmethoderror', 'nosuchfielderror', 'abstractmethoderror'))):
        _add(reasons, 'Mod 与当前游戏或加载器版本不匹配（NoSuchMethod/NoSuchField）')
    desc = re.search('Description:\\s*([^\\n]+)', text)
    exc = re.search('Exception:\\s*([^\\n]+)', text)
    if desc:
        _add(reasons, f'游戏崩溃描述：{desc.group(1).strip()}')
    if exc:
        _add(reasons, f'异常信息：{exc.group(1).strip()}')
    if not reasons:
        for line in reversed(lines):
            if re.search('(Exception|Error|Caused by|崩溃|failed to)', line, re.IGNORECASE):
                _add(reasons, line.strip()[:220])
                break
    report.detail = '\n'.join(lines[-120:])
    return report

def export_log_zip(log_path, version_name, save_path, extra_files=None):
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    files = []
    if log_path and Path(log_path).is_file():
        files.append(Path(log_path))
    if extra_files:
        files.extend((p for p in extra_files if p if Path(p).is_file()))
    with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            try:
                zf.write(path, arcname=path.name)
            except OSError:
                continue
        if not files:
            zf.writestr('README.txt', f'{version_name} 启动日志未生成\n')
        return save_path
    return save_path

