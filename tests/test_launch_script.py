"""Integer memory args and PCL-style launch script tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.launch import (
    LaunchManager,
    _serialize_batch_command,
    parse_mem_to_mb,
)


class MemoryParseTests(unittest.TestCase):
    def test_integer_mb(self):
        self.assertEqual(parse_mem_to_mb('3715M'), 3715)
        self.assertEqual(parse_mem_to_mb('512M'), 512)
        self.assertEqual(parse_mem_to_mb('3.6G'), 3686)
        self.assertEqual(parse_mem_to_mb('1024K'), 1)
        self.assertEqual(parse_mem_to_mb('0'), 2048)
        self.assertEqual(parse_mem_to_mb('bad'), 2048)
        for value in ('3715M', '3.6G', '512M'):
            self.assertIsInstance(parse_mem_to_mb(value), int)


class BatchScriptTests(unittest.TestCase):
    def test_pcl_template_and_javaw_rewrite(self):
        command = [
            r'C:\Program Files\Java\bin\javaw.exe',
            '-Xmx3715m',
            '-Xms512m',
            '-cp',
            r'C:\lib a\x.jar;C:\lib b\y.jar',
            'net.fabricmc.loader.impl.launch.knot.KnotClient',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            lines = _serialize_batch_command(
                command, '1.21.11 EMC', Path(tmp) / 'versions' / '1.21.11 EMC'
            )
        text = '\r\n'.join(lines)
        self.assertEqual(lines[0], 'chcp 936>nul')
        self.assertEqual(lines[1], '@echo off')
        self.assertIn('setlocal DisableDelayedExpansion', lines)
        self.assertIn('title 启动 - 1.21.11 EMC', lines)
        self.assertIn('echo 游戏正在启动，请稍候。', lines)
        self.assertTrue(any(line.startswith('cd /D "') for line in lines))
        self.assertIn('-Xmx3715m', text)
        self.assertIn('-Xms512m', text)
        self.assertIn('java.exe', text)
        self.assertNotIn('javaw.exe', text)
        self.assertNotIn('--launch-b64', text)
        self.assertIn('echo 游戏已退出。', lines)
        self.assertEqual(lines[-1], 'pause')

    def test_percent_and_special_chars_are_escaped(self):
        lines = _serialize_batch_command(
            [
                r'C:\Java\bin\java.exe',
                '-Dfoo=100%',
                '--username',
                'a&b',
                '--server',
                'x<y',
            ],
            '1.0',
            r'C:\mc',
        )
        java_line = next(line for line in lines if 'java.exe' in line)
        self.assertIn('100%%', java_line)
        self.assertIn('"a&b"', java_line)
        self.assertIn('"x<y"', java_line)
        self.assertIn('cd /D "C:\\mc"', lines)

    def test_javaagent_localhost_and_chinese_path(self):
        command = [
            r'C:\Program Files\Java\bin\java.exe',
            r'-javaagent:C:\Users\测试 用户\authlib-injector.jar=http://127.0.0.1:5000/api',
            '-Xmx3715m',
            '-Xms512m',
            r'-Djava.library.path=C:\中文 目录\natives',
            'Main',
        ]
        lines = _serialize_batch_command(
            command, '1.21.11 中文版', r'D:\游戏 目录\.minecraft\versions\1.21.11 中文版'
        )
        text = '\r\n'.join(lines)
        self.assertIn('127.0.0.1:5000/api', text)
        self.assertIn('-javaagent:', text)
        self.assertIn('C:\\Users\\测试 用户\\authlib-injector.jar', text)
        self.assertIn('cd /D "D:\\游戏 目录', text)
        self.assertIn('title 启动 - 1.21.11 中文版', text)
        # 导出端按 GBK 写盘；确保这些字符都能编码。
        text.encode('gbk')

    def test_long_classpath_uses_variable(self):
        classpath = ';'.join(
            r'C:\Users\tester\.minecraft\libraries\mod%04d\x.jar' % index
            for index in range(400)
        )
        lines = _serialize_batch_command(
            [r'C:\Java\bin\java.exe', '-cp', classpath, 'Main'],
            '1.0',
            r'C:\mc',
        )
        java_line = next(line for line in lines if 'java.exe' in line)
        self.assertIn('-cp "%CP%"', java_line)
        self.assertTrue(any(line.startswith('set "CP=') for line in lines))
        self.assertNotIn(classpath, java_line)
        self.assertGreater(len(classpath), 5000)

    def test_build_launch_script_uses_full_command(self):
        class Dummy:
            def __init__(self, game_dir):
                self.game_dir = Path(game_dir)

            def build_launch_command(self, version_id, prompt_java=True):
                self.prompt_java = prompt_java
                return [
                    r'C:\Java\bin\javaw.exe',
                    '-Xmx3715m',
                    '-Xms512m',
                    'Main',
                ], ''

            def get_game_dir(self, version_id):
                return self.game_dir

        with tempfile.TemporaryDirectory() as tmp:
            dummy = Dummy(tmp)
            script, error = LaunchManager.build_launch_script(
                dummy, '1.21.11 EMC'
            )
        self.assertEqual(error, '')
        self.assertIn('title 启动 - 1.21.11 EMC', script)
        self.assertIn('java.exe', script)
        self.assertNotIn('javaw.exe', script)
        self.assertIn('-Xmx3715m', script)
        self.assertIn('pause', script)
        self.assertNotIn('--launch-b64', script)
        self.assertTrue(script.endswith('\r\n'))


if __name__ == '__main__':
    unittest.main()
