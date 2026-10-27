'''社区资源中文译名。

数据来源：
- HMCL 的 addon/mod.csv 与 addon/modpack.csv（译名数据来自 mcmod.cn）
- 内置少量常用资源作为首次启动/离线回退

译名只用于显示和下载文件名；没有中文译名时保持英文原名胜。
'''

from __future__ import annotations

import csv
import html
import io
import json
import re
import threading
import time
from pathlib import Path

from core.net import HttpClient
from core.paths import scl_dir

HMCL_RAW = 'https://raw.githubusercontent.com/HMCL-dev/HMCL/main/HMCL/src/main/resources/assets/addon/'
HMCL_FILES = {
    'mod': HMCL_RAW + 'mod.csv',
    'modpack': HMCL_RAW + 'modpack.csv',
}
ACCELERATORS = ('https://ghfast.top/', 'https://gh-proxy.com/')
CACHE_MAX_AGE = 7 * 24 * 3600

# 首次启动或离线时至少覆盖常用资源；联网后会被 HMCL 的完整数据覆盖。
BUILTIN = {
    'sodium': ('钠', 'Sodium'),
    'sodium-extra': ('钠：扩展', 'Sodium Extra'),
    'create': ('机械动力', 'Create'),
    'createaddition': ('机械动力：附加', 'Create Crafts & Additions'),
    'fabric-api': ('Fabric API', 'Fabric API'),
    'jei': ('JEI 物品管理器', 'Just Enough Items'),
    'rei': ('REI 物品管理器', 'Roughly Enough Items'),
    'iris': ('Iris 光影', 'Iris Shaders'),
    'optifine': ('高清修复', 'OptiFine'),
    'modmenu': ('Mod 菜单', 'Mod Menu'),
    'cloth-config': ('Cloth Config API', 'Cloth Config API'),
    'appleskin': ('苹果皮', 'AppleSkin'),
    'jade': ('Jade', 'Jade'),
    'xaeros-minimap': ('Xaero 的小地图', "Xaero's Minimap"),
    'xaeros-world-map': ('Xaero 的世界地图', "Xaero's World Map"),
    'farmers-delight': ('农夫乐事', "Farmer's Delight"),
    'twilightforest': ('暮色森林', 'The Twilight Forest'),
    'mekanism': ('通用机械', 'Mekanism'),
    'botania': ('植物魔法', 'Botania'),
    'lithium': ('锂', 'Lithium'),
    'starlight': ('星光', 'Starlight'),
    'ferritecore': ('铁氧体核心', 'FerriteCore'),
    'architectury-api': ('Architectury API', 'Architectury API'),
    'geckolib': ('GeckoLib', 'GeckoLib'),
    'curios': ('饰品栏', 'Curios API'),
    'trinkets': ('饰品', 'Trinkets'),
    'patchouli': ('帕秋莉手册', 'Patchouli'),
    'citadel': ('Citadel', 'Citadel'),
    'alexsmobs': ('Alex 的生物', "Alex's Mobs"),
    'terralith': ('Terralith', 'Terralith'),
    'yungsapi': ('YUNG 的 API', "YUNG's API"),
    'betterf3': ('更好的 F3', 'BetterF3'),
    'entityculling': ('实体渲染剔除', 'Entity Culling'),
    'indium': ('铟', 'Indium'),
    'continuity': ('连续性', 'Continuity'),
    'fabric-language-kotlin': ('Fabric Kotlin 语言', 'Fabric Language Kotlin'),
    'kotlinforforge': ('Forge Kotlin 语言', 'Kotlin for Forge'),
}


def _norm(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


_CJK_RE = re.compile(r'[\u3400-\u9fff]')


def english_only(value, fallback=''):
    '''从可能混有中文的标题中提取纯英文名。'''

    text = str(value or '').strip()
    if not text:
        return str(fallback or '').strip()
    if not _CJK_RE.search(text):
        return text
    outside = re.sub(r'[（(][^（）()]*[）)]', '', text)
    outside_words = re.findall(r'[A-Za-z]{2,}', outside)
    parenthesized = re.findall(r'[（(]([^（）()]*[A-Za-z][^（）()]*)[）)]', text)
    if parenthesized and not outside_words:
        candidate = ' '.join(part.strip() for part in parenthesized if part.strip())
    else:
        candidate = re.sub(r'[\u3400-\u9fff]+', ' ', text)
    candidate = re.sub(r'[【】（）()\[\]｜|]+', ' ', candidate)
    candidate = re.sub(r'\s+', ' ', candidate).strip(' -_:：,，')
    if candidate and re.search(r'[A-Za-z]', candidate) and not _CJK_RE.search(candidate):
        return candidate
    return str(fallback or '').strip()


def chinese_only(value, fallback=''):
    '''从混排标题中取出中文主标题。'''

    text = str(value or '').strip()
    if not text:
        return str(fallback or '').strip()
    head = re.split(r'[（(]', text, maxsplit=1)[0].strip(' -_:：,，')
    if _CJK_RE.search(head):
        return head
    chinese = ' '.join(re.findall(r'[\u3400-\u9fff]+', text)).strip()
    return chinese or str(fallback or text).strip()


def _norm_slug(value):
    return str(value or '').strip().casefold()


def _clean_entry(zh, en):
    zh = str(zh or '').strip()
    en = str(en or '').strip()
    if not zh or not en:
        return None
    if _norm(zh) == _norm(en):
        return None
    return zh, en


def _fetch_csv(url, timeout=20):
    http = HttpClient(timeout=timeout, retries=1, verify=False)
    try:
        for base in ACCELERATORS + ('',):
            try:
                response = http.get(base + url)
                data = response.content
                if len(data) > 1000 and b',' in data:
                    return data
            except Exception:
                continue
    finally:
        http.close()
    return b''


def _parse_csv(data, kind):
    rows = []
    try:
        text = data.decode('utf-8-sig', 'replace')
        for row in csv.reader(io.StringIO(text)):
            if not row:
                continue
            first = row[0].lstrip()
            if not first or first.startswith('#'):
                continue
            if len(row) < 5:
                continue
            if kind == 'mod':
                slug_cf, slug_mr, zh, en = (row[1].strip(), row[2].strip(), row[3], row[4])
                modids = row[6] if len(row) > 6 else ''
                rows.append([slug_cf, slug_mr, zh, en, modids])
            else:
                slug_cf, slug_mr, zh, en = (row[1].strip(), row[2].strip(), row[3], row[4])
                rows.append([slug_cf, slug_mr, zh, en])
    except Exception:
        return []
    return rows


class TranslationDB:
    def __init__(self):
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._loading = False
        self._loaded = False
        self._attempted = False
        self._mod_slug = {}
        self._modpack_slug = {}
        self._modid = {}
        self._mod_name = {}
        self._modpack_name = {}
        self._zh_entries = {}
        self._apply_builtin()

    def _remember_zh(self, table, project_type, zh, en, slug):
        zh = str(zh or '').strip()
        en = str(en or '').strip()
        if not zh or not en:
            return
        key = _norm(zh)
        if not key:
            return
        current = table.get(key)
        if current is None or (not current[2] and slug):
            table[key] = (project_type, zh, en, slug or '')

    def _apply_builtin(self):
        for slug, (zh, en) in BUILTIN.items():
            entry = _clean_entry(zh, en)
            if entry:
                self._mod_slug[_norm_slug(slug)] = entry
                self._mod_name[_norm(en)] = entry
                self._modid[_norm_slug(slug)] = entry
                self._remember_zh(self._zh_entries, 'mod', zh, en, slug)

    @property
    def cache_path(self):
        return scl_dir() / 'cache' / 'community_translations.json'

    def start_background_load(self):
        with self._lock:
            if self._loaded or self._loading:
                return
            self._loading = True
        thread = threading.Thread(target=self._load, name='community-translations', daemon=True)
        thread.start()

    def ensure_loaded(self, wait=0):
        if self._event.is_set():
            return
        self.start_background_load()
        if wait:
            self._event.wait(max(0.0, float(wait)))

    def _load(self):
        cache = self.cache_path
        cached = None
        try:
            if cache.is_file():
                cached = json.loads(cache.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict):
            self._apply_rows(cached.get('mod') or [], cached.get('modpack') or [])
            self._loaded = True
            self._event.set()
        stale = False
        if isinstance(cached, dict):
            try:
                stale = time.time() - float(cached.get('fetched') or 0) > CACHE_MAX_AGE
            except (TypeError, ValueError):
                stale = True
        need_fetch = not isinstance(cached, dict) or stale or not cached.get('mod')
        if need_fetch and not self._attempted:
            self._attempted = True
            rows = {}
            for kind, url in HMCL_FILES.items():
                data = _fetch_csv(url)
                if data:
                    rows[kind] = _parse_csv(data, kind)
            if rows.get('mod') or rows.get('modpack'):
                self._apply_rows(rows.get('mod') or [], rows.get('modpack') or [])
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    temp = cache.with_suffix('.tmp')
                    temp.write_text(json.dumps({
                        'version': 1,
                        'fetched': time.time(),
                        'mod': rows.get('mod') or [],
                        'modpack': rows.get('modpack') or [],
                    }, ensure_ascii=False), encoding='utf-8')
                    temp.replace(cache)
                except OSError:
                    pass
        self._loaded = True
        self._event.set()
        with self._lock:
            self._loading = False

    def _apply_rows(self, mod_rows, modpack_rows):
        mod_slug = dict(self._mod_slug)
        modpack_slug = dict(self._modpack_slug)
        modid = dict(self._modid)
        mod_name = dict(self._mod_name)
        modpack_name = dict(self._modpack_name)
        zh_entries = dict(self._zh_entries)
        for row in mod_rows:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            slug_cf, slug_mr, zh, en, ids = (row[0], row[1], row[2], row[3], row[4])
            entry = _clean_entry(zh, en)
            if not entry:
                continue
            slug = slug_mr or slug_cf
            self._remember_zh(zh_entries, 'mod', zh, en, slug)
            for slug_value in (slug_cf, slug_mr):
                if slug_value:
                    mod_slug[_norm_slug(slug_value)] = entry
            mod_name[_norm(en)] = entry
            for token in re.split(r'[;|,]', str(ids or '')):
                token = _norm_slug(token)
                if token:
                    modid[token] = entry
        for row in modpack_rows:
            if not isinstance(row, (list, tuple)) or len(row) < 4:
                continue
            slug_cf, slug_mr, zh, en = (row[0], row[1], row[2], row[3])
            entry = _clean_entry(zh, en)
            if not entry:
                continue
            slug = slug_mr or slug_cf
            self._remember_zh(zh_entries, 'modpack', zh, en, slug)
            for slug_value in (slug_cf, slug_mr):
                if slug_value:
                    modpack_slug[_norm_slug(slug_value)] = entry
            modpack_name[_norm(en)] = entry
        with self._lock:
            self._mod_slug = mod_slug
            self._modpack_slug = modpack_slug
            self._modid = modid
            self._mod_name = mod_name
            self._modpack_name = modpack_name
            self._zh_entries = zh_entries

    def search_hints(self, query, project_type='mod', limit=8):
        '''把中文搜索词转换成对应的英文资源名。'''

        query = _norm(query)
        if not query or not re.search(r'[\u3400-\u9fff]', query):
            return []
        results = []
        for key, value in self._zh_entries.items():
            entry_type, zh, en, slug = value
            if query == key:
                score = 0
            elif key.startswith(query):
                score = 1
            elif query in key:
                score = 2
            else:
                continue
            if project_type == 'modpack' and entry_type != 'modpack':
                score += 4
            results.append((score, len(key), zh, en, slug))
        results.sort(key=lambda item: (item[0], item[1]))
        output = []
        seen = set()
        for _score, _length, zh, en, slug in results:
            key = en.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append({'zh': zh, 'en': en, 'slug': slug or ''})
            if len(output) >= max(1, int(limit)):
                break
        return output

    def translate_project(self, project):
        ptype = str(getattr(project, 'project_type', 'mod') or 'mod').lower()
        slug = _norm_slug(getattr(project, 'slug', ''))
        title = _norm(getattr(project, 'title', ''))
        project_id = _norm_slug(getattr(project, 'project_id', ''))
        if ptype == 'modpack':
            for key, table in ((slug, self._modpack_slug), (title, self._modpack_name)):
                if key and key in table:
                    return table[key][0]
        for table, key in ((self._mod_slug, slug), (self._mod_slug, project_id), (self._mod_name, title)):
            if key and key in table:
                return table[key][0]
        return ''

    def translate_mod(self, modid='', name='', filename=''):
        keys = []
        for token in re.split(r'[;|,\s]+', str(modid or '')):
            key = _norm_slug(token)
            if key:
                keys.append(key)
        if name:
            keys.append(_norm(name))
        stem = Path(str(filename or '')).name
        if stem.lower().endswith('.disabled'):
            stem = stem[:-9]
        if stem.lower().endswith('.jar'):
            stem = stem[:-4]
        if stem:
            keys.append(_norm(stem))
        for key in keys:
            entry = self._modid.get(key) or self._mod_name.get(key)
            if entry:
                return entry[0]
        return ''


translation_db = TranslationDB()


def translate_project(project):
    return translation_db.translate_project(project)


def translate_mod(modid='', name='', filename=''):
    return translation_db.translate_mod(modid, name, filename)


def bilingual_html(zh, en, primary_size=13, secondary_size=11,
                   primary_color='#f0f0f0', secondary_color='#8d939e'):
    zh = str(zh or '').strip()
    en = english_only(en, '')
    if not zh:
        zh = chinese_only(en, '')
    if not en:
        return f'<span style="font-size:{primary_size}px;color:{primary_color};">{html.escape(zh)}</span>' if zh else ''
    if not zh or _norm(zh) == _norm(en):
        return f'<span style="font-size:{primary_size}px;color:{primary_color};">{html.escape(en)}</span>'
    return (
        f'<span style="font-size:{primary_size}px;color:{primary_color};font-weight:600;">{html.escape(zh)}</span>'
        f'<span style="font-size:{secondary_size}px;color:{secondary_color};"> | {html.escape(en)}</span>'
    )


def bilingual_plain(zh, en):
    zh = str(zh or '').strip()
    en = english_only(en, '')
    if not zh:
        zh = chinese_only(en, '')
    if not en:
        return zh
    if not zh or _norm(zh) == _norm(en):
        return en
    return f'{zh} | {en}'


__all__ = [
    'TranslationDB', 'translation_db', 'translate_project', 'translate_mod',
    'bilingual_html', 'bilingual_plain', 'english_only', 'chinese_only',
]
