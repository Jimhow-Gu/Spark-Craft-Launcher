"""Version rename plan, rollback and journal recovery tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.version_rename import (
    JOURNAL_NAME,
    RenameError,
    build_rename_plan,
    execute_rename,
    recover_pending_version_rename,
)


class FakeConfig:
    def __init__(self, path, data):
        self.config_file = Path(path)
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, auto_save=True):
        self.data[key] = value
        if auto_save:
            self.save()
        return True

    def save(self):
        self.config_file.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        return True


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def make_env(tmp):
    mc = Path(tmp) / 'mc'
    versions = mc / 'versions'
    versions.mkdir(parents=True)
    config_path = Path(tmp) / 'config.json'
    config = FakeConfig(config_path, {
        'selected_version': 'Test',
        'favorited_versions': ['Test', 'Other'],
        'version_settings': {'Test': {'isolated': True}, 'Other': {}},
        'version_nicknames': {'Test': '测试版'},
    })
    config.save()
    profile = mc / 'launcher_profiles.json'
    write_json(profile, {
        'profiles': {
            'p1': {'name': 'P1', 'lastVersionId': 'Test'},
            'p2': {'name': 'P2', 'lastVersionId': 'Other'},
        }
    })
    old = versions / 'Test'
    old.mkdir()
    write_json(old / 'Test.json', {'id': 'Test', 'jar': 'Test'})
    (old / 'Test.jar').write_bytes(b'jar')
    child1 = versions / 'Child1'
    child1.mkdir()
    write_json(child1 / 'Child1.json', {
        'id': 'Child1', 'inheritsFrom': 'Test', 'jar': 'Test',
    })
    child2 = versions / 'Child2'
    child2.mkdir()
    write_json(child2 / 'Child2.json', {
        'id': 'Child2', 'inheritsFrom': 'Test',
    })
    return mc, versions, config, profile


class RenamePlanTests(unittest.TestCase):
    def test_leaf_and_children_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, profile = make_env(tmp)
            plan = build_rename_plan(
                'Test', 'Renamed', versions, mc, config
            )
            execute_rename(plan)
            new = versions / 'Renamed'
            self.assertTrue(new.is_dir())
            self.assertFalse((versions / 'Test').exists())
            data = json.loads((new / 'Renamed.json').read_text(encoding='utf-8'))
            self.assertEqual(data['id'], 'Renamed')
            self.assertEqual(data['jar'], 'Renamed')
            self.assertTrue((new / 'Renamed.jar').is_file())
            for name in ('Child1', 'Child2'):
                child = json.loads(
                    (versions / name / f'{name}.json').read_text(encoding='utf-8')
                )
                self.assertEqual(child['inheritsFrom'], 'Renamed')
            self.assertEqual(config.data['selected_version'], 'Renamed')
            self.assertIn('Renamed', config.data['favorited_versions'])
            self.assertIn('Renamed', config.data['version_settings'])
            self.assertIn('Renamed', config.data['version_nicknames'])
            profile_data = json.loads(profile.read_text(encoding='utf-8'))
            self.assertEqual(
                profile_data['profiles']['p1']['lastVersionId'], 'Renamed'
            )
            self.assertFalse((versions / JOURNAL_NAME).exists())
            self.assertFalse(list(versions.glob('.rename-backup-*')))

    def test_corrupt_child_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, _profile = make_env(tmp)
            (versions / 'Child1' / 'Child1.json').write_text(
                '{broken', encoding='utf-8'
            )
            with self.assertRaises(RenameError):
                build_rename_plan('Test', 'Renamed', versions, mc, config)
            self.assertTrue((versions / 'Test' / 'Test.json').is_file())

    def test_existing_or_case_conflict_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, _profile = make_env(tmp)
            with self.assertRaises(RenameError):
                build_rename_plan('Test', 'Child1', versions, mc, config)
            with self.assertRaises(RenameError):
                build_rename_plan('Test', 'test', versions, mc, config)

    def test_fail_after_child_rolls_back_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, profile = make_env(tmp)
            plan = build_rename_plan('Test', 'Renamed', versions, mc, config)
            with self.assertRaises(RenameError):
                execute_rename(plan, fail_after='child_0')
            self.assertTrue((versions / 'Test' / 'Test.json').is_file())
            self.assertEqual((versions / 'Test' / 'Test.jar').read_bytes(), b'jar')
            for name in ('Child1', 'Child2'):
                child = json.loads(
                    (versions / name / f'{name}.json').read_text(encoding='utf-8')
                )
                self.assertEqual(child['inheritsFrom'], 'Test')
            child1 = json.loads(
                (versions / 'Child1' / 'Child1.json').read_text(encoding='utf-8')
            )
            self.assertEqual(child1['jar'], 'Test')
            self.assertEqual(config.data['selected_version'], 'Test')
            profile_data = json.loads(profile.read_text(encoding='utf-8'))
            self.assertEqual(
                profile_data['profiles']['p1']['lastVersionId'], 'Test'
            )
            self.assertFalse((versions / JOURNAL_NAME).exists())
            self.assertFalse(list(versions.glob('.rename-backup-*')))

    def test_fail_after_dir_moved_restores_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, profile = make_env(tmp)
            plan = build_rename_plan('Test', 'Renamed', versions, mc, config)
            with self.assertRaises(RenameError):
                execute_rename(plan, fail_after='dir_moved')
            self.assertTrue((versions / 'Test' / 'Test.json').is_file())
            self.assertFalse((versions / 'Renamed').exists())
            self.assertEqual(config.data['selected_version'], 'Test')

    def test_rollback_does_not_create_missing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, _profile = make_env(tmp)
            config.config_file.unlink()
            plan = build_rename_plan('Test', 'Renamed', versions, mc, config)
            self.assertFalse(plan.config_existed)
            with self.assertRaises(RenameError):
                execute_rename(plan, fail_after='dir_moved')
            self.assertTrue((versions / 'Test' / 'Test.json').is_file())
            self.assertFalse((versions / 'Renamed').exists())
            self.assertFalse(config.config_file.exists())

    def test_locked_child_write_rolls_back_all(self):
        import core.version_rename as module
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, profile = make_env(tmp)
            plan = build_rename_plan('Test', 'Renamed', versions, mc, config)
            real_write = module._atomic_write
            failed = {'value': False}

            def flaky(path, data):
                if path.name == 'Child2.json' and not failed['value']:
                    failed['value'] = True
                    raise PermissionError('child locked')
                return real_write(path, data)

            with patch.object(module, '_atomic_write', flaky):
                with self.assertRaises(RenameError):
                    execute_rename(plan)
            self.assertTrue((versions / 'Test' / 'Test.json').is_file())
            self.assertFalse((versions / 'Renamed').exists())
            child2 = json.loads(
                (versions / 'Child2' / 'Child2.json').read_text(encoding='utf-8')
            )
            self.assertEqual(child2['inheritsFrom'], 'Test')
            self.assertEqual(config.data['selected_version'], 'Test')

    def test_crash_recovery_from_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, _profile = make_env(tmp)
            plan = build_rename_plan('Test', 'Renamed', versions, mc, config)
            with self.assertRaises(Exception):
                execute_rename(plan, crash_after='dir_moved')
            self.assertTrue((versions / JOURNAL_NAME).is_file())
            self.assertTrue((versions / 'Renamed').is_dir())
            with patch('core.config.ConfigManager', return_value=config):
                message = recover_pending_version_rename(mc)
            self.assertEqual(message, '')
            self.assertTrue((versions / 'Test' / 'Test.json').is_file())
            self.assertFalse((versions / 'Renamed').exists())
            self.assertEqual(config.data['selected_version'], 'Test')
            self.assertFalse((versions / JOURNAL_NAME).exists())
            self.assertFalse(list(versions.glob('.rename-backup-*')))

    def test_noop_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            mc, versions, config, _profile = make_env(tmp)
            with self.assertRaises(RenameError):
                build_rename_plan('Test', 'Test', versions, mc, config)
            with self.assertRaises(RenameError):
                build_rename_plan('Test', 'bad/name', versions, mc, config)


if __name__ == '__main__':
    unittest.main()
