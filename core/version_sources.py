"""版本列表源合并与去重（对齐 PCL2 / HMCL）。

PCL2 与 HMCL 都使用 Minecraft 的 v1 version_manifest：

- PCL2：launchermeta.mojang.com + bmclapi2.bangbang93.com
- HMCL：piston-meta.mojang.com + DownloadProvider 镜像候选

本模块在两边基础上取并集：官方（Piston/launchermeta）+ BMCLAPI 并发拉取，
按版本 id 去重；同 id 官方优先，BMCLAPI 补齐缺失字段。
versions 按 releaseTime 倒序，latest 用合并结果重新计算。
v2 仅作为主源全部失败后的兜底，不参与日常首选。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

OFFICIAL = "https://launchermeta.mojang.com"
PISTON = "https://piston-meta.mojang.com"
BMCLAPI = "https://bmclapi2.bangbang93.com"
OPENBMCLAPI = "https://openbmclapi.bangbang93.com"

V1_PATH = "/mc/game/version_manifest.json"
V2_PATH = "/mc/game/version_manifest_v2.json"

MIN_VERSION_COUNT = 200


def _base(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _natural_key(value: Any) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", str(value or ""))
    )


def _version_key(item: dict) -> tuple:
    return (
        str(item.get("releaseTime") or item.get("time") or ""),
        _natural_key(item.get("id")),
    )


def _source_rank(url: str) -> int:
    """同一版本 id 的来源优先级：官方 0，BMCL 1，其他镜像 2。"""
    normalized = _base(url).lower()
    if normalized.startswith(PISTON) or normalized.startswith(OFFICIAL):
        return 0
    if normalized.startswith(BMCLAPI):
        return 1
    if normalized.startswith(OPENBMCLAPI):
        return 2
    return 3


def manifest_candidates(
    extra: Iterable[str] = (),
    include_v2: bool = False,
) -> list[str]:
    """返回去重后的 manifest URL 列表。

    默认只返回 PCL/HMCL 使用的 v1：Piston、launchermeta、BMCLAPI。
    include_v2=True 时追加 v2 兜底源；extra 可以是完整 .json URL 或镜像根地址。
    """
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        normalized = _base(url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        urls.append(normalized)

    for base in (PISTON, OFFICIAL, BMCLAPI):
        add(base + V1_PATH)

    for raw in tuple(extra or ()):
        base = _base(raw)
        if not base:
            continue
        if base.lower().endswith(".json"):
            add(base)
        else:
            add(base + V1_PATH)

    if include_v2:
        for base in (PISTON, OFFICIAL, BMCLAPI, OPENBMCLAPI):
            add(base + V2_PATH)

    return urls


def merge_manifests(
    manifests: Iterable[dict],
    sources: Sequence[str] | None = None,
    min_versions: int = 0,
) -> dict:
    """合并多个 version_manifest，按版本 id 去重。

    - sources 与 manifests 一一对应时，用 URL 判断来源优先级；
    - 同 id：官方优先；同来源取 releaseTime 更新的一份；低优先级来源补齐缺失字段；
    - min_versions > 0 时，版本数不足的 manifest 直接丢弃（PCL 用 200）。
    """
    by_id: dict[str, dict] = {}
    by_rank: dict[str, int] = {}
    latest: dict[str, str] = {}

    manifest_list = list(manifests or ())
    source_list = list(sources or ())
    for index, manifest in enumerate(manifest_list):
        if not isinstance(manifest, dict):
            continue

        versions = manifest.get("versions")
        if not isinstance(versions, list):
            continue
        if min_versions and len(versions) < min_versions:
            continue

        source = source_list[index] if index < len(source_list) else ""
        rank = _source_rank(source)

        for version in versions:
            if not isinstance(version, dict):
                continue
            version_id = str(version.get("id") or "").strip()
            if not version_id:
                continue

            old = by_id.get(version_id)
            if old is None:
                by_id[version_id] = dict(version)
                by_rank[version_id] = rank
                continue

            old_rank = by_rank.get(version_id, rank)
            if rank < old_rank:
                by_id[version_id] = dict(version)
                by_rank[version_id] = rank
            elif rank == old_rank:
                if _version_key(version) >= _version_key(old):
                    by_id[version_id] = dict(version)
                else:
                    for key, value in version.items():
                        old.setdefault(key, value)
            else:
                for key, value in version.items():
                    old.setdefault(key, value)

        latest_info = manifest.get("latest")
        if isinstance(latest_info, dict):
            for key in ("release", "snapshot"):
                value = str(latest_info.get(key) or "").strip()
                if value and key not in latest:
                    latest[key] = value

    versions = sorted(by_id.values(), key=_version_key, reverse=True)

    releases = [v for v in versions if str(v.get("type") or "") == "release"]
    snapshots = [
        v
        for v in versions
        if str(v.get("type") or "") in {"snapshot", "old_alpha", "old_beta"}
    ]
    if releases:
        latest["release"] = str(releases[0].get("id") or "")
    if snapshots:
        latest["snapshot"] = str(snapshots[0].get("id") or "")

    return {"latest": latest, "versions": versions}


__all__ = [
    "BMCLAPI", "MIN_VERSION_COUNT", "OFFICIAL", "OPENBMCLAPI", "PISTON",
    "V1_PATH", "V2_PATH", "manifest_candidates", "merge_manifests",
]
