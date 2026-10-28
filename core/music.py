'''背景音乐播放：SCL/music 下的音频循环播放。'''

from __future__ import annotations
import logging
from pathlib import Path
from typing import List, Optional
from PySide6.QtCore import (QObject, QUrl, Signal)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from core.config import ConfigManager
from core.paths import scl_dir
logger = logging.getLogger('Music')
MUSIC_EXTENSIONS = {'.wav', '.m4a', '.ogg', '.opus', '.flac', '.aac', '.mp3', '.wma', '.aiff', '.aif'}

def music_dir():
    path = scl_dir() / 'music'
    path.mkdir(parents=True, exist_ok=True)
    return path

def list_music_files():
    try:
        files = [path for path in music_dir().rglob('*') if path.is_file() if path.suffix.lower() in MUSIC_EXTENSIONS]
    except OSError:
        return []
    return sorted(files, key=(lambda p: (p.name.lower(), str(p).lower())))

class BackgroundMusicPlayer(QObject):
    state_changed = Signal()
    _instance: "Optional['BackgroundMusicPlayer']" = None
    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = BackgroundMusicPlayer()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.config = ConfigManager()
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._files = []
        self._index = 0
        self._source_path = ''
        self._user_paused = bool(self.config.get('background_music_paused', False))
        self._video_music_paused = bool(self.config.get('background_video_audio', False))
        raw_volume = self.config.get('background_music_volume', 60)
        try:
            volume = int(raw_volume) if raw_volume is not None else 60
        except (TypeError, ValueError):
            volume = 60
        volume = max(0, min(100, volume))
        self._audio.setVolume(volume / 100.0)
        self._audio.setMuted(bool(self.config.get('background_music_muted', False)))
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.playbackStateChanged.connect((lambda _state: self.state_changed.emit()))
        self._player.errorOccurred.connect(
            (lambda error, message: logger.warning(f'背景音乐播放失败: {message or error}'))
        )
        self.reload(play=not self._user_paused and not self._video_music_paused)

    def reload(self, play=True):
        previous = self._source_path
        self._files = list_music_files()
        if not self._files:
            self._source_path = ''
            try:
                self._player.stop()
                self._player.setSource(QUrl())
            except RuntimeError:
                pass
            self.state_changed.emit()
            return
        index = 0
        if previous:
            for i, path in enumerate(self._files):
                if str(path) == previous:
                    index = i
                    break
        index = int(self.config.get('background_music_index', 0) or 0)
        index = max(0, min(index, len(self._files) - 1))
        self._index = index
        path = self._files[self._index]
        if str(path) != self._source_path:
            self._source_path = str(path)
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self.config.set('background_music_index', self._index)
        if play:
            if not self._video_music_paused:
                if self.is_muted():
                    self.set_muted(False)
                self._player.play()
        self.state_changed.emit()

    def play(self, remember=True):
        if remember:
            self._user_paused = False
            self.config.set('background_music_paused', False)
        self._video_music_paused = False
        if self.is_muted():
            self.set_muted(False)
        if not self._files:
            self.reload(play=True)
            return
        if not self._source_path:
            self._index = 0
            self._source_path = str(self._files[0])
            self._player.setSource(QUrl.fromLocalFile(self._source_path))
        self._player.play()

    def pause(self, remember=True):
        if remember:
            self._user_paused = True
            self.config.set('background_music_paused', True)
        self._player.pause()

    def set_video_music_enabled(self, enabled):
        '''视频音乐开启时临时暂停自定义音乐；关闭后恢复用户未主动暂停的音乐。'''

        self._video_music_paused = bool(enabled)
        if self._video_music_paused:
            self._player.pause()
        elif not self._user_paused and not self.is_muted():
            if not self._files:
                self.reload(play=True)
            else:
                self._player.play()
        self.state_changed.emit()

    def user_paused(self):
        '''用户主动暂停过（区别于启动器退到后台时的临时暂停）。'''

        return bool(self._user_paused)

    def toggle_play(self):
        if self.is_playing():
            self.pause()
            return
        self.play()

    def is_playing(self):
        return self._player.playbackState() == QMediaPlayer.PlayingState

    def has_tracks(self):
        return bool(self._files)

    def clear_playback(self):
        '''停止播放并清空当前曲目，下次播放从第一首开始。'''

        self._user_paused = True
        self._index = 0
        self._source_path = ''
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        except RuntimeError:
            pass
        self.config.set('background_music_paused', True)
        self.config.set('background_music_index', 0)
        self.state_changed.emit()

    def current_name(self):
        if self._source_path:
            return Path(self._source_path).name
        return ''

    def next_track(self):
        if not self._files:
            return
        self._index = (self._index + 1) % len(self._files)
        self._source_path = str(self._files[self._index])
        self._player.setSource(QUrl.fromLocalFile(self._source_path))
        self._player.play()
        self.config.set('background_music_index', self._index)
        self.state_changed.emit()

    def _on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.next_track()
        self.state_changed.emit()

    def volume(self):
        return int(round(self._audio.volume() * 100))

    def set_volume(self, value):
        value = max(0, min(100, int(value)))
        self._audio.setVolume(value / 100.0)
        self.config.set('background_music_volume', value)
        self.state_changed.emit()

    def is_muted(self):
        return bool(self._audio.isMuted())

    def set_muted(self, muted):
        self._audio.setMuted(bool(muted))
        self.config.set('background_music_muted', bool(muted))
        self.state_changed.emit()

    def toggle_muted(self):
        self.set_muted(not self.is_muted())


