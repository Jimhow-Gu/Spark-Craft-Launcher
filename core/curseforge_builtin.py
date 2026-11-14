'''兼容旧版本的 CurseForge Key 接口。

共享 Key 会随客户端被提取，不能作为安全凭据；现在只读取用户配置或
环境变量（见 CurseForgeSource），这里不再返回内置 Key。
'''

from __future__ import annotations


def builtin_curseforge_key():
    return ''


__all__ = ['builtin_curseforge_key']
