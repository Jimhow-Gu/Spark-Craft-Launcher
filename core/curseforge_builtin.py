'''读取官方构建内置的 CurseForge API Key。

开源仓库只包含本文件，不包含真实 Key；官方打包时会在本地放置
core/_curseforge_key.py（已加入 .gitignore），由 PyInstaller 一并打入。
'''

from __future__ import annotations

import base64


def builtin_curseforge_key():
    try:
        from core._curseforge_key import ENCODED_KEY
        raw = base64.b64decode(str(ENCODED_KEY or '').strip(), validate=True)
        return raw.decode('utf-8').strip()
    except Exception:
        return ''


__all__ = ['builtin_curseforge_key']
