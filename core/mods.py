'''
模组/整合包/资源包/光影包/数据包数据源
- Modrinth（默认，无需 API Key）
- CurseForge（需要 API Key，可在全局设置中填写）
所有网络请求都在调用线程内同步执行，由 QThread Worker 调用；每个 Worker 使用独立 HttpClient。
'''

from __future__ import annotations
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import (Any, Dict, List, Optional, Tuple)
from urllib.parse import quote
from core.config import ConfigManager
from core.net import DownloadError, HttpClient
from core.paths import scl_dir
MODRINTH_API = 'https://api.modrinth.com/v2'
CURSEFORGE_API = 'https://api.curseforge.com/v1'
PROJECT_TYPES: 'Dict[str, str]' = {'mod': '模组', 'modpack': '整合包', 'datapack': '数据包', 'resourcepack': '资源包', 'shader': '光影包'}
LOADERS: 'Dict[str, str]' = {'fabric': 'Fabric', 'forge': 'Forge', 'neoforge': 'NeoForge', 'quilt': 'Quilt'}
LOADER_RELEVANT_TYPES = {'mod', 'modpack'}
SORTS: 'Dict[str, str]' = {'relevance': '相关度', 'downloads': '下载量', 'updated': '最近更新', 'newest': '最新发布'}
CF_CLASS_IDS: 'Dict[str, int]' = {'mod': 6, 'modpack': 4471, 'resourcepack': 12, 'shader': 6552, 'datapack': 6945}
CF_LOADER_IDS: 'Dict[str, int]' = {'forge': 1, 'fabric': 4, 'quilt': 5, 'neoforge': 6}
CF_SECTION_URL: 'Dict[str, str]' = {'mod': 'mc-mods', 'modpack': 'modpacks', 'resourcepack': 'texture-packs', 'shader': 'shaders', 'datapack': 'data-packs'}
CATEGORY_ZH: 'Dict[str, str]' = {'adventure': '冒险', 'cursed': '整活', 'decoration': '装饰', 'economy': '经济', 'equipment': '装备', 'food': '食物', 'gameplay': '玩法', 'library': '支持库', 'magic': '魔法', 'optimization': '性能优化', 'storage': '存储', 'technology': '科技', 'transportation': '交通', 'utility': '实用', 'worldgen': '世界生成', 'cosmetic': '装饰', 'mobs': '生物', 'information': '信息显示', 'management': '管理', 'minigame': '小游戏', 'social': '社交', 'challenging': '硬核', 'combat': '战斗', 'building': '建筑', 'kitchen-sink': '综合', 'lightweight': '轻量', 'multiplayer': '多人', 'quests': '任务', 'science': '科学', 'skyblock': '空岛', 'fabric': 'Fabric', 'forge': 'Forge', 'neoforge': 'NeoForge', 'quilt': 'Quilt', 'audio': '音效', 'blocks': '方块', 'environment': '环境', 'fonts': '字体', 'gui': '界面', 'items': '物品', 'locale': '语言', 'models': '模型', 'realistic': '写实', 'simplistic': '简约', 'themed': '主题', 'tweaks': '微调', 'vanilla-like': '原版风', 'cartoon': '卡通', 'fantasy': '奇幻', 'semi-realistic': '半写实', 'colored-lighting': '彩色光照', 'foliage': '植被', 'path-tracing': '路径追踪', 'pbr': 'PBR', 'reflections': '反射', 'shadows': '阴影', 'atmosphere': '氛围', 'bloom': '泛光', 'high': '高配', 'medium': '中配', 'low': '低配', 'potato': '极低配', 'screenshot': '截图', 'water': '水体', 'clouds': '云', 'lights': '光照', 'game-mechanics': '游戏机制'}

def category_label(name):
    return CATEGORY_ZH.get(name, name)

@dataclass
class ModProject:
    source: 'str'
    project_id: 'str'
    slug: 'str'
    title: 'str'
    description: 'str'
    translated_title: 'str' = ''
    icon_url: 'str' = ''
    author: 'str' = ''
    downloads: 'int' = 0
    updated: 'str' = ''
    categories: 'List[str]' = field(default_factory=list)
    versions: 'List[str]' = field(default_factory=list)
    website_url: 'str' = ''
    project_type: 'str' = 'mod'
    def __post_init__(self):
        if not self.translated_title:
            try:
                from core.mod_translations import translate_project
                self.translated_title = translate_project(self) or ''
            except Exception:
                self.translated_title = ''
    @property
    def english_title(self):
        try:
            from core.mod_translations import english_only
            fallback = self.slug.replace('-', ' ').strip() if self.slug else str(self.project_id or '')
            return english_only(self.title, fallback)
        except Exception:
            return self.title
    @property
    def display_title(self):
        return self.translated_title or self.title
    def search_text(self):
        return f'{self.title} {self.translated_title} {self.slug} {self.author}'.lower()


@dataclass
class ModFile:
    source: 'str'
    project_id: 'str'
    file_id: 'str'
    filename: 'str'
    display_name: 'str'
    download_url: 'str'
    size: 'int' = 0
    date: 'str' = ''
    release_type: 'str' = 'release'
    game_versions: 'List[str]' = field(default_factory=list)
    loaders: 'List[str]' = field(default_factory=list)
    sha1: 'str' = ''
    sha512: 'str' = ''
    dependencies: 'List[dict]' = field(default_factory=list)

def download_candidates(url):
    '''社区资源下载候选源；优先使用可直连的 Modrinth CDN 节点。'''

    url = str(url or '').strip()
    if not url:
        return []
    candidates = []
    if 'cdn.modrinth.com/' in url:
        path = url.split('cdn.modrinth.com/', 1)[1]
        candidates.extend([f'https://cdn.modrinth.com/{path}', f'https://cdn-raw.modrinth.com/{path}', f'https://mod.mcimirror.top/{path}'])
    elif 'cdn-raw.modrinth.com/' in url:
        path = url.split('cdn-raw.modrinth.com/', 1)[1]
        candidates.extend([f'https://cdn-raw.modrinth.com/{path}', f'https://cdn.modrinth.com/{path}', f'https://mod.mcimirror.top/{path}'])
    if 'edge.forgecdn.net' in url:
        candidates.extend([url.replace('edge.forgecdn.net', 'mediafilez.forgecdn.net'), url])
    elif 'mediafilez.forgecdn.net' in url:
        candidates.append(url)
    if url not in candidates:
        candidates.append(url)
    return list(dict.fromkeys(candidates))

@dataclass
class SearchResult:
    projects: 'List[ModProject]'
    total: 'int'
    offset: 'int'
    limit: 'int'

class SourceUnavailable(Exception):
    pass

def _fmt_date(value):
    if not value:
        return '未知'
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return value.split('T')[0]

def _natural_key(text):
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split('(\\d+)', text)]

def _release_rank(kind):
    return {'release': 0, 'beta': 1, 'alpha': 2}.get((kind or '').lower(), 3)

class BaseSource:
    name = ''
    def search(self, query, project_type, mc_version='', loader='', category='', sort='relevance', offset=0, limit=20):
        raise NotImplementedError

    def get_files(self, project, mc_version='', loader=''):
        raise NotImplementedError

    def get_categories(self, project_type):
        return []

    def get_project(self, project_id):
        pass

    def resolve_dependency(self, dependency):
        pid = dependency.get('project_id') or dependency.get('modId') or dependency.get('projectId')
        if pid:
            return self.get_project(str(pid))

    def get_project_url(self, project):
        return project.website_url


class ModrinthSource(BaseSource):
    name = 'Modrinth'
    def __init__(self):
        self.http = HttpClient(timeout=15)

    def _get_json(self, url, params=None):
        last = None
        targets = [url]
        if url.startswith(MODRINTH_API):
            targets.append(url.replace(MODRINTH_API, 'https://mod.mcimirror.top/modrinth/v2', 1))
        for attempt in range(3):
            for target in targets:
                try:
                    return self.http.get_json(target, params=params)
                except Exception as exc:
                    last = exc
                    status_code = getattr(exc, 'status_code', None)
                    response = getattr(exc, 'response', None)
                    if response is not None:
                        status_code = response.status_code
                    if status_code in (401, 403, 404):
                        raise
                    continue
            if attempt < 2:
                time.sleep(0.8)
                try:
                    self.http.close()
                except Exception:
                    pass
                self.http = HttpClient(timeout=15)
        raise last

    def search(self, query, project_type, mc_version='', loader='', category='', sort='relevance', offset=0, limit=20):
        facets = [[f'project_type:{project_type}']]
        if mc_version:
            facets.append([f'versions:{mc_version}'])
        if project_type in LOADER_RELEVANT_TYPES and loader:
            facets.append([f'categories:{loader}'])
        if category:
            facets.append([f'categories:{category}'])
        index = {'relevance': 'relevance', 'downloads': 'downloads', 'updated': 'updated', 'newest': 'newest'}.get(sort, 'relevance')
        params = {'query': query or '', 'limit': max(1, min(limit, 50)), 'offset': max(0, offset), 'index': index, 'facets': json.dumps(facets, separators=(',', ':'))}
        data = self._get_json(f'{MODRINTH_API}/search', params=params)
        hits = data.get('hits') or []
        projects = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            pid = hit.get('project_id') or hit.get('slug') or ''
            slug = hit.get('slug') or pid
            versions = hit.get('versions') or []
            categories = hit.get('display_categories') or hit.get('categories') or []
            projects.append(ModProject(source=self.name, project_id=pid, slug=slug, title=hit.get('title') or slug or '未命名项目', description=hit.get('description') or '', icon_url=hit.get('icon_url') or '', author=hit.get('author') or '', downloads=int(hit.get('downloads') or 0), updated=_fmt_date(hit.get('date_modified') or hit.get('date_created') or ''), categories=[category_label(c) for c in categories], versions=versions[:8], website_url=f'https://modrinth.com/{project_type}/{slug}', project_type=project_type))
        total = int(data.get('total_hits') or 0)
        return SearchResult(projects, total, offset, limit)

    def get_files(self, project, mc_version='', loader=''):
        ident = project.project_id or project.slug
        params = {'limit': 100}
        if mc_version:
            params['game_versions'] = json.dumps([mc_version])
        if loader:
            params['loaders'] = json.dumps([loader])
        try:
            data = self._get_json(f'{MODRINTH_API}/project/{quote(ident)}/version', params=params)
        except DownloadError:
            raise
        if not isinstance(data, list):
            return []
        if not data:
            if loader:
                params.pop('loaders', None)
                data = self._get_json(f'{MODRINTH_API}/project/{quote(ident)}/version', params=params)
        if not data:
            if mc_version:
                params.pop('game_versions', None)
                params.pop('loaders', None)
                data = self._get_json(f'{MODRINTH_API}/project/{quote(ident)}/version', params=params)
        if not isinstance(data, list):
            return []
        files = []
        for version in data:
            if not isinstance(version, dict):
                continue
            vfiles = version.get('files') or []
            if not vfiles:
                continue
            primary = next((f for f in vfiles if f.get('primary')), vfiles[0])
            if not isinstance(primary, dict):
                continue
            hashes = primary.get('hashes') or {}
            files.append(ModFile(source=self.name, project_id=project.project_id, file_id=str(version.get('id') or ''), filename=primary.get('filename') or f'{project.slug}.jar', display_name=version.get('version_number') or primary.get('filename') or '', download_url=primary.get('url') or '', size=int(primary.get('size') or 0), date=_fmt_date(version.get('date_published') or ''), release_type=version.get('version_type') or 'release', game_versions=version.get('game_versions') or [], loaders=version.get('loaders') or [], sha1=hashes.get('sha1') or '', sha512=hashes.get('sha512') or '', dependencies=version.get('dependencies') or []))
        files.sort(key=(lambda f: (f.date or '', _release_rank(f.release_type))), reverse=True)
        return files

    def get_project(self, project_id):
        try:
            data = self._get_json(f'{MODRINTH_API}/project/{quote(project_id)}')
        except Exception:
            return
        if not isinstance(data, dict):
            return
        slug = data.get('slug') or project_id
        ptype = data.get('project_type') or 'mod'
        return ModProject(source=self.name, project_id=data.get('id') or project_id, slug=slug, title=data.get('title') or slug, description=data.get('description') or '', icon_url=data.get('icon_url') or '', downloads=int(data.get('downloads') or 0), updated=_fmt_date(data.get('updated') or data.get('published') or ''), categories=[category_label(c) for c in data.get('display_categories') or []], website_url=f'https://modrinth.com/{ptype}/{slug}', project_type=ptype)

    def get_version_by_hash(self, sha1):
        '''通过本地 jar 的 SHA1 精确查询 Modrinth 上的版本。'''

        if not sha1:
            return
        try:
            data = self._get_json(f'{MODRINTH_API}/version_file/{quote(str(sha1))}', params={'algorithm': 'sha1'})
        except Exception:
            return
        if isinstance(data, dict):
            return data

    @staticmethod
    def _parse_date(value):
        text = str(value or '').strip()
        if not text:
            return
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            pass

    def find_update(self, sha1, mc_version, loader):
        '''检测是否有适配当前游戏版本/加载器的更新版本。'''

        version = self.get_version_by_hash(sha1)
        if not version:
            return
        project_id = str(version.get('project_id') or '')
        if not project_id:
            return
        params = {'limit': 100}
        if mc_version:
            params['game_versions'] = json.dumps([mc_version])
        if loader:
            params['loaders'] = json.dumps([loader])
        try:
            rows = self._get_json(f'{MODRINTH_API}/project/{quote(project_id)}/version', params=params)
        except Exception:
            return
        if not isinstance(rows, list):
            return
        installed_id = str(version.get('id') or '')
        installed_date = self._parse_date(version.get('date_published'))
        latest = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if installed_id:
                if str(row.get('id') or '') == installed_id:
                    continue
            if mc_version:
                if mc_version not in (row.get('game_versions') or []):
                    continue
            if loader:
                if loader not in (row.get('loaders') or []):
                    continue
            row_date = self._parse_date(row.get('date_published'))
            if installed_date and row_date and row_date <= installed_date:
                continue
            files = row.get('files') or []
            primary = next((f for f in files if isinstance(f, dict) if f.get('primary')), files[0] if files and isinstance(files[0], dict) else None)
            if not primary or not primary.get('url'):
                continue
            hashes = primary.get('hashes') or {}
            candidate = {'version_id': str(row.get('id') or ''), 'version': str(row.get('version_number') or primary.get('filename') or ''), 'filename': str(primary.get('filename') or ''), 'url': str(primary.get('url') or ''), 'size': int(primary.get('size') or 0), 'sha1': str(hashes.get('sha1') or ''), 'date': str(row.get('date_published') or ''), 'release_type': str(row.get('version_type') or 'release')}
            if latest is None:
                latest = candidate
            elif candidate['release_type'] == 'release' and latest['release_type'] != 'release':
                latest = candidate
                break
        if latest is None:
            return
        return {'source': self.name, 'project_id': project_id, 'installed_version': str(version.get('version_number') or ''), 'latest': latest}

    def resolve_dependency(self, dependency):
        pid = dependency.get('project_id')
        if pid:
            return self.get_project(str(pid))
        version_id = dependency.get('version_id')
        if version_id:
            try:
                data = self._get_json(f'{MODRINTH_API}/version/{quote(str(version_id))}')
                pid = data.get('project_id') if isinstance(data, dict) else None
                if pid:
                    return self.get_project(str(pid))
            except Exception:
                pass
        name = dependency.get('file_name') or '未命名前置'
        return ModProject(source=self.name, project_id=str(version_id or name), slug='', title=name, description='未获取到前置模组详情，可尝试手动搜索', project_type='mod')

    def get_categories(self, project_type):
        try:
            data = self._get_json(f'{MODRINTH_API}/tag/category')
        except DownloadError:
            return []
        result = []
        for item in data or []:
            if not isinstance(item, dict):
                continue
            if item.get('project_type') != project_type:
                continue
            name = item.get('name') or ''
            if name:
                if all((name != existing[0] for existing in result)):
                    result.append((name, category_label(name)))
        priority = ['optimization', 'utility', 'library', 'adventure', 'technology', 'magic', 'worldgen', 'decoration', 'food', 'equipment', 'storage']
        def key(pair):
            idx = priority.index(pair[0]) if pair[0] in priority else 999
            return idx, pair[1].lower()

        result.sort(key=key)
        return result


class CurseForgeSource(BaseSource):
    name = 'CurseForge'
    def __init__(self):
        cfg = ConfigManager()
        self.api_key = os.environ.get('CURSEFORGE_API_KEY', '').strip() or str(cfg.get('curseforge_api_key', '') or '').strip()
        base = (os.environ.get('CURSEFORGE_API_BASE', '').strip() or str(cfg.get('curseforge_api_base', '') or '').strip() or CURSEFORGE_API).rstrip('/')
        self.api_base = base
        self.mirror_base = 'https://mod.mcimirror.top/curseforge/v1'
        self.http = HttpClient(timeout=15)
        self._category_cache = {}

    def _headers(self):
        headers = {'Accept': 'application/json'}
        if self.api_key:
            headers['x-api-key'] = self.api_key
        return headers

    def _check(self):
        if self.api_base == CURSEFORGE_API and not self.api_key:
            raise SourceUnavailable('CurseForge 需要 API Key。请到 https://console.curseforge.com/ 申请后，在“全局设置 → 模组下载源”中填写。')

    def search(self, query, project_type, mc_version='', loader='', category='', sort='relevance', offset=0, limit=20):
        self._check()
        class_id = CF_CLASS_IDS.get(project_type, 6)
        sort_field = {'relevance': 2, 'downloads': 6, 'updated': 3, 'newest': 11}.get(sort, 2)
        params = {'gameId': 432, 'classId': class_id, 'index': max(0, offset), 'pageSize': max(1, min(limit, 50)), 'sortField': sort_field, 'sortOrder': 'desc'}
        if query:
            params['searchFilter'] = query
        if mc_version:
            params['gameVersion'] = mc_version
        if loader and project_type in LOADER_RELEVANT_TYPES and loader in CF_LOADER_IDS:
            params['modLoaderType'] = CF_LOADER_IDS[loader]
        if category:
            try:
                params['categoryId'] = int(category)
            except ValueError:
                pass
        data = self.http.get_json(f'{self.api_base}/mods/search', params=params, headers=self._headers())
        rows = data.get('data') or []
        pagination = data.get('pagination') or {}
        projects = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            logo = row.get('logo') or {}
            authors = row.get('authors') or []
            categories = row.get('categories') or []
            files = row.get('latestFiles') or []
            slug = row.get('slug') or str(row.get('id', ''))
            website = (row.get('links') or {}).get('websiteUrl') or ''
            if not website and slug:
                website = f'https://www.curseforge.com/minecraft/{CF_SECTION_URL.get(project_type, "mc-mods")}/{slug}'
            projects.append(ModProject(source=self.name, project_id=str(row.get('id', '')), slug=slug, title=row.get('name') or slug, description=(row.get('summary') or '').strip(), icon_url=logo.get('thumbnailUrl') or logo.get('url') or '', author=authors[0].get('name', '') if authors and isinstance(authors[0], dict) else '', downloads=int(row.get('downloadCount') or 0), updated=_fmt_date(row.get('dateModified') or ''), categories=[category_label(c.get('name', '')) for c in categories if isinstance(c, dict)], versions=[f.get('displayName', '') for f in files[:8] if isinstance(f, dict) and f.get('displayName')], website_url=website, project_type=project_type))
        total = int(pagination.get('totalCount') or len(projects))
        return SearchResult(projects, total, offset, limit)

    def get_files(self, project, mc_version='', loader=''):
        self._check()
        params = {'index': 0, 'pageSize': 50, 'sortField': 2, 'sortOrder': 'desc'}
        if mc_version:
            params['gameVersion'] = mc_version
        if loader and loader in CF_LOADER_IDS:
            params['modLoaderType'] = CF_LOADER_IDS[loader]
        try:
            data = self.http.get_json(f'{self.api_base}/mods/{project.project_id}/files', params=params, headers=self._headers())
        except DownloadError:
            raise
        rows = data.get('data') or []
        if not rows and (mc_version or loader):
            data = self.http.get_json(f'{self.api_base}/mods/{project.project_id}/files', params={'index': 0, 'pageSize': 50}, headers=self._headers())
            rows = data.get('data') or []
        files = []
        for row in rows:
            item = self._row_to_file(row, project)
            if item:
                files.append(item)
        files.sort(key=(lambda f: (f.date or '', _release_rank(f.release_type))), reverse=True)
        return files

    def get_file_by_id(self, project_id, file_id, mc_version='', loader=''):
        '''获取指定 project/file 的元数据（整合包导入用）。

        没有 CurseForge API Key 时回退到 CFWidget 公开元数据 + ForgeCDN 直链，
        保证本地整合包导入不被 Key 卡住。
        '''

        if self.api_key or self.api_base != CURSEFORGE_API:
            try:
                data = self.http.get_json(f'{self.api_base}/mods/{project_id}/files/{file_id}', headers=self._headers())
                row = data.get('data') if isinstance(data, dict) else None
                if isinstance(row, dict):
                    fake = ModProject(source=self.name, project_id=str(project_id), slug=str(project_id), title=str(project_id), description='')
                    result = self._row_to_file(row, fake)
                    if result is not None:
                        return result
            except Exception:
                pass
        try:
            data = self.http.get_json(f'{self.mirror_base}/mods/{project_id}/files/{file_id}', headers={'Accept': 'application/json'})
            row = data.get('data') if isinstance(data, dict) else None
            if isinstance(row, dict) and row.get('isAvailable', True):
                fake = ModProject(source=self.name, project_id=str(project_id), slug=str(project_id), title=str(project_id), description='')
                result = self._row_to_file(row, fake)
                if result is not None and result.download_url:
                    return result
        except Exception:
            pass
        return self._get_file_by_id_cfwidget(project_id, file_id, mc_version, loader)

    def _get_file_by_id_cfwidget(self, project_id, file_id, mc_version='', loader=''):
        try:
            wanted = int(file_id)
        except (TypeError, ValueError):
            return
        params = {}
        if mc_version:
            params['version'] = mc_version
        if loader:
            params['loader'] = loader
        try:
            data = self.http.get_json(f'https://api.cfwidget.com/{quote(str(project_id))}', params=params or None, headers={'Accept': 'application/json'})
        except Exception:
            return
        found = None
        def walk(node):
            nonlocal found
            if found is not None:
                return
            if isinstance(node, dict):
                try:
                    node_id = int(node.get('id') or -1)
                except (TypeError, ValueError):
                    node_id = -1
                if node_id == wanted and node.get('name'):
                    found = node
                    return
                for value in node.values():
                    walk(value)
                return
            if isinstance(node, (list, tuple)):
                for value in node:
                    walk(value)

        walk(data)
        if not found:
            return
        filename = str(found.get('name') or '')
        if not filename:
            return
        download_url = self._cdn_url(str(wanted), filename)
        if not download_url:
            return
        release = str(found.get('type') or 'release').lower()
        if release not in ('release', 'beta', 'alpha'):
            release = 'release'
        return ModFile(source=self.name, project_id=str(project_id), file_id=str(wanted), filename=filename, display_name=str(found.get('display') or filename), download_url=download_url, size=int(found.get('filesize') or 0), date=_fmt_date(str(found.get('uploaded_at') or '')), release_type=release, game_versions=[str(v) for v in found.get('versions') or []], loaders=[], sha1='', dependencies=[])

    def _row_to_file(self, row, project):
        if not isinstance(row, dict):
            return
        file_id = str(row.get('id') or '')
        filename = row.get('fileName') or f'{project.slug}-{file_id}.jar'
        url = row.get('downloadUrl') or self._cdn_url(file_id, filename)
        hashes = {}
        for h in row.get('hashes') or []:
            if isinstance(h, dict) and h.get('algo') == 1:
                hashes['sha1'] = h.get('value', '')
        release = {1: 'release', 2: 'beta', 3: 'alpha'}.get(int(row.get('releaseType') or 1), 'release')
        dependencies = []
        for dep in row.get('dependencies') or []:
            if not isinstance(dep, dict):
                continue
            try:
                relation = int(dep.get('relationType') or 0)
            except Exception:
                relation = 0
            if relation == 3 and dep.get('modId'):
                dependencies.append({'project_id': str(dep.get('modId')), 'dependency_type': 'required'})
        return ModFile(source=self.name, project_id=project.project_id, file_id=file_id, filename=filename, display_name=row.get('displayName') or filename, download_url=url, size=int(row.get('fileLength') or 0), date=_fmt_date(row.get('fileDate') or ''), release_type=release, game_versions=row.get('gameVersions') or [], loaders=[], sha1=hashes.get('sha1', ''), dependencies=dependencies)

    def get_project(self, project_id):
        self._check()
        try:
            data = self.http.get_json(f'{self.api_base}/mods/{project_id}', headers=self._headers())
        except Exception:
            return
        row = data.get('data') if isinstance(data, dict) else None
        if not isinstance(row, dict):
            return
        logo = row.get('logo') or {}
        authors = row.get('authors') or []
        categories = row.get('categories') or []
        files = row.get('latestFiles') or []
        slug = row.get('slug') or str(row.get('id', project_id))
        website = (row.get('links') or {}).get('websiteUrl') or ''
        if not website:
            if slug:
                website = f'https://www.curseforge.com/minecraft/mc-mods/{slug}'
        return ModProject(source=self.name, project_id=str(row.get('id', project_id)), slug=slug, title=row.get('name') or slug, description=(row.get('summary') or '').strip(), icon_url=logo.get('thumbnailUrl') or logo.get('url') or '', author=authors[0].get('name', '') if authors and isinstance(authors[0], dict) else '', downloads=int(row.get('downloadCount') or 0), updated=_fmt_date(row.get('dateModified') or ''), categories=[category_label(c.get('name', '')) for c in categories if isinstance(c, dict)], versions=[f.get('displayName', '') for f in files[:8] if isinstance(f, dict) and f.get('displayName')], website_url=website, project_type='mod')

    @staticmethod
    def _cdn_url(file_id, filename):
        try:
            fid = int(file_id)
            return f'https://mediafilez.forgecdn.net/files/{fid // 1000}/{fid % 1000}/{quote(filename)}'
        except (TypeError, ValueError):
            return ''

    def get_categories(self, project_type):
        self._check()
        if project_type in self._category_cache:
            return self._category_cache[project_type]
        try:
            data = self.http.get_json(f'{self.api_base}/categories', params={'gameId': 432, 'classId': CF_CLASS_IDS.get(project_type, 6)}, headers=self._headers())
        except DownloadError:
            return []
        result = []
        for item in data.get('data') or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get('id', ''))
            name = item.get('name') or ''
            if cid and name:
                result.append((cid, category_label(name)))
        result.sort(key=(lambda x: x[1].lower()))
        self._category_cache[project_type] = result
        return result


def get_source(name):
    if name == 'CurseForge':
        return CurseForgeSource()
    return ModrinthSource()

def cached_mc_versions(include_snapshots=False):
    '''读取本地缓存的 MC 版本列表；没有缓存时返回空列表。'''

    key = 'all' if include_snapshots else 'release'
    try:
        cache_file = scl_dir() / 'cache' / 'mc_versions_cache.json'
        if not cache_file.is_file():
            return []
        raw = json.loads(cache_file.read_text(encoding='utf-8'))
        value = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
    except (OSError, ValueError):
        pass
    return []


def fetch_mc_versions(include_snapshots=False, force=False):
    '''获取 MC 版本列表，优先镜像，并缓存上次成功结果。'''

    key = 'all' if include_snapshots else 'release'
    cache_file = None
    cached = {}
    try:
        cache_dir = scl_dir() / 'cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / 'mc_versions_cache.json'
        if cache_file.is_file():
            raw = json.loads(cache_file.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                cached = raw
    except (OSError, ValueError):
        cached = {}
    if not force:
        cached_versions = cached.get(key)
        if isinstance(cached_versions, list) and cached_versions:
            try:
                fresh = time.time() - cache_file.stat().st_mtime < 86400
            except OSError:
                fresh = False
            if fresh:
                return [str(v) for v in cached_versions]
    urls = ['https://openbmclapi.bangbang93.com/mc/game/version_manifest_v2.json', 'https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json', 'https://launchermeta.mojang.com/mc/game/version_manifest_v2.json']
    http = HttpClient(timeout=8, retries=1)
    for url in urls:
        try:
            data = http.get_json(url)
            versions = []
            for item in data.get('versions', []):
                if not isinstance(item, dict):
                    continue
                if not include_snapshots and item.get('type') != 'release':
                    continue
                vid = item.get('id')
                if vid:
                    versions.append(str(vid))
            if versions:
                versions.sort(key=_natural_key, reverse=True)
                if cache_file is not None:
                    try:
                        cached[key] = versions
                        tmp = cache_file.with_suffix('.tmp')
                        tmp.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
                        tmp.replace(cache_file)
                    except OSError:
                        pass
                return versions
        except Exception:
            continue
    fallback = cached.get(key)
    if isinstance(fallback, list) and fallback:
        return [str(v) for v in fallback]
    if not include_snapshots:
        return ['26.3', '26.2', '1.21.11', '1.21.10', '1.21.8', '1.21.4', '1.21.1', '1.20.6', '1.20.4', '1.20.1', '1.19.4', '1.19.2', '1.18.2', '1.17.1', '1.16.5', '1.15.2', '1.14.4', '1.12.2', '1.11.2', '1.10.2', '1.9.4', '1.8.9', '1.7.10']
    return []

# WARNING: Decompyle incomplete
