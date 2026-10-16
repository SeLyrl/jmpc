import os
import sys
import re
import json
import random
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from mutagen import File as MutagenFile
from PyQt6.QtCore import Qt, QTimer, QUrl, QThread, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QIcon,
    QPen, QPolygonF, QBrush, QKeySequence
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QSystemTrayIcon, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QListWidget, QListWidgetItem, QStackedWidget,
    QSlider, QSplitter, QFrame, QAbstractItemView, QFileDialog,
    QDialog, QMenu, QProgressBar
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from cava_engine import CavaVisualizer
import jmpc_enhancements

PRESET_THEMES = {
    "Catppuccin Mocha": {
        "bg": "#1e1e2e", "panel": "#181825", "text": "#cdd6f4",
        "accent": "#89b4fa", "secondary": "#eba0ac", "highlight": "#f9e2af"
    },
    "Tokyo Night": {
        "bg": "#1a1b26", "panel": "#16161e", "text": "#c0caf5",
        "accent": "#7aa2f7", "secondary": "#bb9af7", "highlight": "#e0af68"
    },
    "Gruvbox Dark": {
        "bg": "#282828", "panel": "#1d2021", "text": "#ebdbb2",
        "accent": "#fe8019", "secondary": "#b8bb26", "highlight": "#fabd2f"
    },
    "Nord": {
        "bg": "#2e3440", "panel": "#242933", "text": "#eceff4",
        "accent": "#88c0d0", "secondary": "#81a1c1", "highlight": "#ebcb8b"
    },
    "Dracula": {
        "bg": "#282a36", "panel": "#1e1f29", "text": "#f8f8f2",
        "accent": "#bd93f9", "secondary": "#ff79c6", "highlight": "#f1fa8c"
    },
}

KEYBIND_LABELS = {
    "editor_mode": "Editor Mode",
    "youtube_bar": "YouTube Bar",
    "play_pause": "Play / Pause",
    "next_track": "Next Track",
    "prev_track": "Previous Track",
    "vol_up": "Volume Up",
    "vol_down": "Volume Down",
    "add_queue": "Add To Queue",
    "delete_queue": "Delete From Queue",
    "cava_mode": "Visualizer Style",
    "random_mode": "Random Mode",
    "consume_mode": "Consume Mode",
    "loop_mode": "Loop Mode",
    "fullscreen_toggle": "Fullscreen Toggle",
    "tray_minimize": "Minimize",
}

SUPPORTED_AUDIO_EXTS = {
    ".mp3", ".flac", ".ogg", ".m4a", ".wav",
    ".webm", ".opus", ".aac", ".wma", ".mp4"
}


def resource_path(relative_path: str) -> Path:
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).parent
    return base_path / relative_path


def parse_lrc_text(raw_text: str) -> list[tuple[float, str]]:
    entries: list[tuple[float, str]] = []
    plain_lines: list[str] = []
    tag_pattern = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[\.:](\d{1,3}))?\]")

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\[(ar|al|ti|au|length|by|offset|re|ve):", line, re.IGNORECASE):
            continue
        matches = list(tag_pattern.finditer(line))
        if matches:
            lyric_text = tag_pattern.sub("", line).strip()
            for m in matches:
                mins = int(m.group(1))
                secs = int(m.group(2))
                frac_str = m.group(3) or "0"
                frac = int(frac_str) / (1000.0 if len(frac_str) == 3 else 100.0)
                entries.append((mins * 60.0 + secs + frac, lyric_text))
        else:
            plain_lines.append(line)

    if entries:
        entries.sort(key=lambda x: x[0])
        return entries
    if plain_lines:
        return [(i * 4.0, txt) for i, txt in enumerate(plain_lines)]
    return []


def read_text_multi_encoding(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp932", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


class Track:
    def __init__(self, path: Path, base_dir: Path):
        self.path = path
        self.is_youtube = False
        self.yt_url = ""
        self.orig_yt_url = ""
        self.custom_lrc_path: Path | None = None
        self.embedded_lyrics: str = ""

        try:
            self.rel_file = str(path.relative_to(base_dir)).replace("\\", "/")
        except ValueError:
            self.rel_file = path.name

        self.filename = path.name
        self.title = path.stem
        self.artist = "Unknown"
        self.album_artist = "Unknown"
        self.album = "Unknown Album"
        self.track_num = "1"
        self.date = "2025"
        self.genre = "Unknown"
        self.duration = 0
        self.bitrate = 0
        self.added_iso = ""
        self.modified_iso = ""
        self.cover_bytes: bytes | None = None

        if " - " in path.stem:
            parts = path.stem.split(" - ", 1)
            self.artist = parts[0].strip() or "Unknown"
            self.album_artist = self.artist
            self.title = parts[1].strip() or path.stem

        if path.exists():
            self._load_metadata()

    def _load_metadata(self):
        try:
            st = self.path.stat()
            file_size = st.st_size
            self.added_iso = datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.modified_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            audio = MutagenFile(self.path)
            if audio and audio.info:
                self.duration = int(getattr(audio.info, "length", 0))
                raw_br = int(getattr(audio.info, "bitrate", 0))
                if raw_br > 0:
                    self.bitrate = raw_br // 1000 if raw_br > 1000 else raw_br

            if self.bitrate <= 0 and self.duration > 0 and file_size > 0:
                self.bitrate = max(32, int((file_size * 8) / (self.duration * 1000)))

            easy = MutagenFile(self.path, easy=True)
            if easy and easy.tags:
                self.title = easy.tags.get("title", [self.title])[0]
                self.artist = easy.tags.get("artist", [self.artist])[0]
                self.album_artist = easy.tags.get("albumartist", [self.artist])[0]
                self.album = easy.tags.get("album", [self.album])[0]
                self.track_num = easy.tags.get("tracknumber", ["1"])[0].split("/")[0]
                self.date = easy.tags.get("date", ["2025"])[0]
                self.genre = "; ".join(easy.tags.get("genre", ["Unknown"]))

            if audio and hasattr(audio, "tags") and audio.tags:
                for k in ("lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS", "USLT::eng", "SYLT"):
                    if k in audio.tags:
                        val = audio.tags[k]
                        self.embedded_lyrics = val[0] if isinstance(val, list) else str(val)
                        break
                for k, v in audio.tags.items():
                    if k.startswith("USLT") and not self.embedded_lyrics:
                        self.embedded_lyrics = str(getattr(v, "text", v))

                for k, v in audio.tags.items():
                    if k.startswith("APIC"):
                        self.cover_bytes = v.data
                        return
                if "covr" in audio.tags:
                    self.cover_bytes = bytes(audio.tags["covr"][0])
                    return

            if audio and hasattr(audio, "pictures") and audio.pictures:
                self.cover_bytes = audio.pictures[0].data
                return

            for img_name in ("cover.png", "cover.jpg", "folder.jpg", "album.jpg", "front.jpg"):
                p = self.path.parent / img_name
                if p.exists():
                    self.cover_bytes = p.read_bytes()
                    return
        except Exception:
            pass

    @property
    def duration_str(self) -> str:
        m, s = int(self.duration // 60), int(self.duration % 60)
        return f"{m}:{s:02d}"


class YouTubeStreamWorker(QThread):
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, url: str, resume_pos_ms: int = 0):
        super().__init__()
        self.url = url
        self.resume_pos_ms = resume_pos_ms

    def run(self):
        try:
            import yt_dlp
            # Intentar extraer usando cookies de navegadores disponibles para evitar el throttling de YouTube
            cookies_sources = ["chrome", "firefox", "edge", "safari", None]
            info = None

            for browser in cookies_sources:
                try:
                    ydl_opts = {
                        "format": "bestaudio/best",
                        "quiet": True,
                        "no_warnings": True,
                        "noplaylist": True,
                        "socket_timeout": 15,
                        "retries": 3,
                    }
                    if browser:
                        ydl_opts["cookiesfrombrowser"] = (browser,)

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(self.url, download=False)
                        if info:
                            break
                except Exception:
                    continue

            if not info:
                raise Exception("No se pudo extraer el enlace de streaming de YouTube.")

            thumb_url = info.get("thumbnail", "")
            cover_bytes = None
            if thumb_url and self.resume_pos_ms == 0:
                try:
                    req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as r:
                        cover_bytes = r.read()
                except Exception:
                    pass
            abr = int(info.get("abr") or info.get("tbr") or 160)
            self.finished_ok.emit({
                "stream_url": info.get("url", ""),
                "orig_url": self.url,
                "title": info.get("title", "YouTube Track"),
                "artist": info.get("uploader", "YouTube"),
                "duration": int(info.get("duration") or 0),
                "bitrate": abr,
                "cover_bytes": cover_bytes,
                "resume_pos_ms": self.resume_pos_ms,
            })
        except Exception as e:
            self.failed.emit(str(e))

class YouTubeDownloadWorker(QThread):
    progress_updated = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, url: str, dest_dir: Path):
        super().__init__()
        self.url = url
        self.dest_dir = dest_dir

    def _progress_hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                self.progress_updated.emit(percent)
            else:
                self.progress_updated.emit(0)
        elif d["status"] == "finished":
            self.progress_updated.emit(100)

    def run(self):
        try:
            import yt_dlp
            self.dest_dir.mkdir(parents=True, exist_ok=True)
            outtmpl = str(self.dest_dir / "%(uploader)s - %(title)s.%(ext)s")
            
            cookies_sources = ["chrome", "firefox", "edge", "safari"]
            success = False
            info = None
            filename = ""

            for browser in cookies_sources:
                try:
                    ydl_opts = {
                        "format": "bestaudio/best",
                        "outtmpl": outtmpl,
                        "noplaylist": True,
                        "quiet": True,
                        "retries": 3,
                        "cookiesfrombrowser": (browser,),
                        "writethumbnail": True,
                        "progress_hooks": [self._progress_hook],
                        "postprocessors": [
                            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                            {"key": "EmbedThumbnail"},
                            {"key": "FFmpegMetadata"},
                        ],
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(self.url, download=True)
                        filename = ydl.prepare_filename(info)
                        success = True
                        break
                except Exception:
                    continue

            if not success:
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": outtmpl,
                    "noplaylist": True,
                    "quiet": True,
                    "retries": 3,
                    "writethumbnail": True,
                    "progress_hooks": [self._progress_hook],
                    "postprocessors": [
                        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                        {"key": "EmbedThumbnail"},
                        {"key": "FFmpegMetadata"},
                    ],
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self.url, download=True)
                    filename = ydl.prepare_filename(info)

            mp3_path = str(Path(filename).with_suffix(".mp3"))
            self.finished_ok.emit(mp3_path)
        except Exception as e:
            self.failed.emit(str(e))


class CleanLineEdit(QLineEdit):
    def __init__(self, placeholder: str = ""):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #181825; color: #cdd6f4; border: 1px solid #89b4fa; padding: 4px; font-family: 'Cascadia Mono', monospace; font-size: 12px; }
            QMenu::item { padding: 5px 22px; border-radius: 2px; }
            QMenu::item:selected { background-color: #89b4fa; color: #11111b; font-weight: bold; }
            QMenu::separator { height: 1px; background: #313244; margin: 4px 2px; }
        """)
        act_paste = menu.addAction("Paste       Ctrl+V")
        act_copy = menu.addAction("Copy        Ctrl+C")
        act_cut = menu.addAction("Cut         Ctrl+X")
        menu.addSeparator()
        act_all = menu.addAction("Select All  Ctrl+A")
        act_clear = menu.addAction("Clear")

        chosen = menu.exec(event.globalPos())
        if chosen == act_paste: self.paste()
        elif chosen == act_copy: self.copy()
        elif chosen == act_cut: self.cut()
        elif chosen == act_all: self.selectAll()
        elif chosen == act_clear: self.clear()


class ModernIconButton(QPushButton):
    def __init__(self, icon_type: str, text_or_size=None, size: tuple[int, int] = (85, 26)):
        text = ""
        actual_size = size

        if isinstance(text_or_size, str):
            text = text_or_size
        elif isinstance(text_or_size, tuple):
            actual_size = text_or_size

        super().__init__(text)
        self.icon_type = icon_type
        self.icon_color = QColor("#cdd6f4")
        self.accent_color = QColor("#89b4fa")
        self.hovered = False
        self.setFixedSize(actual_size[0], actual_size[1])
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_mode(self, mode: str):
        self.icon_type = mode
        self.update()

    def set_colors(self, fg: str, accent: str):
        self.icon_color = QColor(fg)
        self.accent_color = QColor(accent)
        self.update()

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = self.accent_color if (self.hovered or self.isChecked()) else self.icon_color
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(col))

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        if self.icon_type == "play":
            poly = QPolygonF([QPointF(cx - 5, cy - 6.5), QPointF(cx - 5, cy + 6.5), QPointF(cx + 7, cy)])
            painter.drawPolygon(poly)
        elif self.icon_type == "pause":
            painter.drawRoundedRect(QRectF(cx - 6, cy - 6.5, 4.0, 13.0), 1.0, 1.0)
            painter.drawRoundedRect(QRectF(cx + 2, cy - 6.5, 4.0, 13.0), 1.0, 1.0)
        elif self.icon_type == "prev":
            painter.drawRoundedRect(QRectF(cx - 7, cy - 6.0, 2.5, 12.0), 0.8, 0.8)
            poly = QPolygonF([QPointF(cx + 6, cy - 6.0), QPointF(cx + 6, cy + 6.0), QPointF(cx - 4, cy)])
            painter.drawPolygon(poly)
        elif self.icon_type == "next":
            painter.drawRoundedRect(QRectF(cx + 4.5, cy - 6.0, 2.5, 12.0), 0.8, 0.8)
            poly = QPolygonF([QPointF(cx - 6, cy - 6.0), QPointF(cx - 6, cy + 6.0), QPointF(cx + 4, cy)])
            painter.drawPolygon(poly)
        elif self.icon_type == "edit":
            painter.setPen(QPen(col, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
        elif self.icon_type == "yt":
            poly = QPolygonF([QPointF(cx - 4, cy - 5), QPointF(cx - 4, cy + 5), QPointF(cx + 6, cy)])
            painter.drawPolygon(poly)
        elif self.icon_type == "random":
            painter.setPen(QPen(col, 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(cx - 7, cy - 3.5), QPointF(cx - 2.5, cy - 3.5))
            painter.drawLine(QPointF(cx - 2.5, cy - 3.5), QPointF(cx + 2.5, cy + 3.5))
            painter.drawLine(QPointF(cx + 2.5, cy + 3.5), QPointF(cx + 5.5, cy + 3.5))
            painter.drawLine(QPointF(cx - 7, cy + 3.5), QPointF(cx - 2.5, cy + 3.5))
            painter.drawLine(QPointF(cx - 2.5, cy + 3.5), QPointF(cx + 2.5, cy - 3.5))
            painter.drawLine(QPointF(cx + 2.5, cy - 3.5), QPointF(cx + 5.5, cy - 3.5))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(col))
            head1 = QPolygonF([QPointF(cx + 4.5, cy - 6.0), QPointF(cx + 4.5, cy - 1.0), QPointF(cx + 8.0, cy - 3.5)])
            head2 = QPolygonF([QPointF(cx + 4.5, cy + 1.0), QPointF(cx + 4.5, cy + 6.0), QPointF(cx + 8.0, cy + 3.5)])
            painter.drawPolygon(head1)
            painter.drawPolygon(head2)
        elif self.icon_type == "consume":
            painter.drawRect(QRectF(cx - 6, cy - 6, 12, 3))
            poly = QPolygonF([QPointF(cx - 5, cy - 1), QPointF(cx + 5, cy - 1), QPointF(cx, cy + 6)])
            painter.drawPolygon(poly)
        elif self.icon_type == "loop":
            painter.setPen(QPen(col, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(int(cx - 6), int(cy - 6), 12, 12, 0 * 16, 270 * 16)
        elif self.icon_type == "vol":
            painter.drawRect(QRectF(cx - 6, cy - 3, 4, 6))
            poly = QPolygonF([QPointF(cx - 2, cy - 3), QPointF(cx + 3, cy - 7), QPointF(cx + 3, cy + 7), QPointF(cx - 2, cy + 3)])
            painter.drawPolygon(poly)
        elif self.icon_type == "yt_play":
            poly = QPolygonF([QPointF(cx - 4, cy - 5), QPointF(cx - 4, cy + 5), QPointF(cx + 5, cy)])
            painter.drawPolygon(poly)
            painter.setPen(QPen(col, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(cx - 7, cy - 7, 14, 14), 3, 3)
        elif self.icon_type == "yt_download":
            painter.drawRect(QRectF(cx - 5, cy - 6, 10, 3))
            poly = QPolygonF([QPointF(cx - 4, cy - 1), QPointF(cx + 4, cy - 1), QPointF(cx, cy + 5)])
            painter.drawPolygon(poly)
            painter.setPen(QPen(col, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(cx - 7, cy - 7, 14, 14), 3, 3)


class HDCoverWidget(QLabel):
    def __init__(self, fixed_side: int | None = None):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fixed_side = fixed_side
        if fixed_side:
            self.setFixedSize(fixed_side, fixed_side)
        else:
            self.setMinimumSize(180, 180)
        self.orig_pixmap: QPixmap | None = None
        self.setText("No Album Art")

    def set_cover_bytes(self, data: bytes | None):
        if not data:
            self.orig_pixmap = None
            self.setPixmap(QPixmap())
            self.setText("No Album Art")
            return
        img = QImage.fromData(data)
        if not img.isNull():
            self.orig_pixmap = QPixmap.fromImage(img)
            self._rescale()
        else:
            self.orig_pixmap = None
            self.setText("Cover Error")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        if self.orig_pixmap:
            side = self.fixed_side - 4 if self.fixed_side else min(self.width() - 8, self.height() - 8)
            if side > 20:
                self.setPixmap(self.orig_pixmap.scaled(
                    side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                ))


class RMPCWindow(QMainWindow):
    def __init__(self, config_dir: Path, initial_music_dir: Path | None = None):
        super().__init__()
        self.config_dir = config_dir
        self.custom_file = config_dir / "user_customization.json"

        default_music = Path.home() / "Music" if (Path.home() / "Music").exists() else Path.home() / "Música"

        self.user_cfg = {
            "music_dir": str(initial_music_dir or default_music),
            "show_welcome": True,
            "colors": dict(PRESET_THEMES["Catppuccin Mocha"]),
            "win_opacity": 100,
            "progress_style": "Pill Capsule",
            "cover_on_right": False,
            "cava_on_top": False,
            "block_order": ["header", "tabs", "content", "bottom"],
            "keybinds": {
                "editor_mode": "p", "youtube_bar": "y", "play_pause": "space",
                "next_track": ">", "prev_track": "<", "vol_up": "+", "vol_down": "-",
                "add_queue": "a", "delete_queue": "d", "cava_mode": "v",
                "random_mode": "x", "consume_mode": "c", "loop_mode": "r",
                "fullscreen_toggle": "Ctrl+F", "tray_minimize": "Ctrl+Q",
            },
        }
        self._load_user_customization()

        if initial_music_dir and initial_music_dir.exists():
            self.music_dir = initial_music_dir
            self.user_cfg["music_dir"] = str(initial_music_dir)
        else:
            saved_dir = Path(self.user_cfg.get("music_dir", str(default_music)))
            self.music_dir = saved_dir if saved_dir.exists() else default_music

        self.setWindowTitle("JMPC")
        
        icon_file = resource_path("assets/icon.ico")
        if icon_file.exists():
            self.setWindowIcon(QIcon(str(icon_file)))

        self.resize(1380, 840)

        self.edit_mode = False
        self.editor_dialog = None

        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.audio_out.setVolume(0.70)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_player_error)

        self.all_tracks = []
        self.queue = []
        self.current_track = None
        self.current_idx = -1
        self.play_counts = {}

        self.st_random = False
        self.st_consume = False
        self.st_loop = False

        self.user_wants_playing = False
        self.last_known_pos_ms = 0
        self.stall_ticks = 0
        self.is_recovering_stream = False

        self.artists_map = {}
        self.albums_map = {}
        self.dirs_map = {}
        self.lyrics_lines = []
        self.yt_stream_worker = None
        self.yt_dl_worker = None

        self._build_ui()
        self._apply_dynamic_theme()
        self._apply_layout_configuration()
        self._scan_library(replace_queue=True)

        QApplication.instance().installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self._cleanup_tray_icon)

        self.vis_timer = QTimer(self)
        self.vis_timer.timeout.connect(self._tick_visualizer)
        self.vis_timer.start(35)

        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._check_stream_health)
        self.health_timer.start(1000)


        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(
            self.windowIcon() if not self.windowIcon().isNull()
            else QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_MediaPlay)
        )
        self.tray_icon.activated.connect(self._on_tray_icon_activated)

        tray_menu = QMenu()
        restore_action = tray_menu.addAction("Show / Restore")
        restore_action.triggered.connect(self._restore_from_tray)
        quit_action = tray_menu.addAction("Exit JMPC")
        quit_action.triggered.connect(self._cleanup_and_quit)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _cleanup_tray_icon(self):
        if hasattr(self, "tray_icon") and self.tray_icon is not None:
            self.tray_icon.hide()
            self.tray_icon.deleteLater()
            self.tray_icon = None

    def _cleanup_and_quit(self):
        self._cleanup_tray_icon()
        QApplication.quit()

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._restore_from_tray()

    def _load_user_customization(self):
        if self.custom_file.exists():
            try:
                data = json.loads(self.custom_file.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, dict) and k in self.user_cfg and isinstance(self.user_cfg[k], dict):
                        self.user_cfg[k].update(v)
                    elif k in self.user_cfg:
                        self.user_cfg[k] = v
                if self.user_cfg["keybinds"].get("tray_minimize", "").strip().lower() in ("minimize", "minimize to tray", ""):
                    self.user_cfg["keybinds"]["tray_minimize"] = "Ctrl+Q"
                    self._save_user_customization()
            except Exception:
                pass

    def _save_user_customization(self):
        try:
            self.custom_file.write_text(json.dumps(self.user_cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(8, 6, 8, 8)
        self.root_layout.setSpacing(5)

        self.yt_panel = QFrame()
        self.yt_panel.setObjectName("boxFrame")
        self.yt_panel.setVisible(False)
        yt_layout = QHBoxLayout(self.yt_panel)
        yt_layout.setContentsMargins(8, 4, 8, 4)

        yt_lbl = QLabel("YouTube:")
        yt_lbl.setStyleSheet("font-weight: bold;")
        self.yt_input = CleanLineEdit("Click here to type or paste YouTube URL...")
        self.yt_input.returnPressed.connect(self.play_youtube_url)

        self.yt_progress = QProgressBar()
        self.yt_progress.setRange(0, 100)
        self.yt_progress.setValue(0)
        self.yt_progress.setFixedHeight(22)
        self.yt_progress.setVisible(False)

        self.yt_play_btn = ModernIconButton("yt_play", "", (40, 28))
        self.yt_play_btn.clicked.connect(self.play_youtube_url)

        self.yt_dl_btn = ModernIconButton("yt_download", "", (40, 28))
        self.yt_dl_btn.clicked.connect(self.download_youtube_url)

        yt_layout.addWidget(yt_lbl)
        yt_layout.addWidget(self.yt_input, 1)
        yt_layout.addWidget(self.yt_progress, 1)
        yt_layout.addWidget(self.yt_play_btn)
        yt_layout.addWidget(self.yt_dl_btn)
        self.root_layout.addWidget(self.yt_panel)

        self.dynamic_blocks_container = QWidget()
        self.blocks_layout = QVBoxLayout(self.dynamic_blocks_container)
        self.blocks_layout.setContentsMargins(0, 0, 0, 0)
        self.blocks_layout.setSpacing(5)
        self.root_layout.addWidget(self.dynamic_blocks_container, 1)

        self.hdr_frame = QFrame()
        self.hdr_frame.setObjectName("boxFrame")
        hdr_layout = QHBoxLayout(self.hdr_frame)
        hdr_layout.setContentsMargins(12, 6, 12, 6)

        self.hdr_left = QLabel("[Stopped]\n0:00 / 0:00 (0 kbps)")
        self.hdr_center = QLabel("No Song\nUnknown - Unknown Album")
        self.hdr_center.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hdr_right_box = QVBoxLayout()
        vol_row = QHBoxLayout()
        self.vol_lbl = QLabel("70%")

        self.btn_vol_down = QPushButton("-")
        self.btn_vol_up = QPushButton("+")
        for b, step in ((self.btn_vol_down, -5), (self.btn_vol_up, 5)):
            b.setFixedWidth(26)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, s=step: self.change_volume(s))

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(100)
        self.vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vol_slider.valueChanged.connect(self._on_vol_changed)

        vol_row.addStretch()
        vol_row.addWidget(self.vol_lbl)
        vol_row.addWidget(self.btn_vol_down)
        vol_row.addWidget(self.vol_slider)
        vol_row.addWidget(self.btn_vol_up)

        states_row = QHBoxLayout()
        states_row.addStretch()
        self.btn_random = ModernIconButton("random", "", (40, 20))
        self.btn_consume = ModernIconButton("consume", "", (40, 20))
        self.btn_loop = ModernIconButton("loop", "", (40, 20))
        for b, prop in ((self.btn_random, "st_random"), (self.btn_consume, "st_consume"), (self.btn_loop, "st_loop")):
            b.setCheckable(True)
            b.clicked.connect(lambda checked, p=prop: setattr(self, p, checked))
            states_row.addWidget(b)

        hdr_right_box.addLayout(vol_row)
        hdr_right_box.addLayout(states_row)
        hdr_layout.addWidget(self.hdr_left, 1)
        hdr_layout.addWidget(self.hdr_center, 2)
        hdr_layout.addLayout(hdr_right_box, 1)

        self.tabs_frame = QFrame()
        self.tabs_frame.setObjectName("boxFrame")
        tabs_layout = QHBoxLayout(self.tabs_frame)
        tabs_layout.setContentsMargins(8, 4, 8, 4)

        self.edit_toggle_btn = ModernIconButton("edit", "", (45, 26))
        self.edit_toggle_btn.setCheckable(True)
        self.edit_toggle_btn.clicked.connect(lambda _: self.toggle_editor_mode())
        tabs_layout.addWidget(self.edit_toggle_btn)

        tabs_layout.addStretch()
        self.tab_btns = []
        self.tab_names = ["Queue", "Artists", "Albums", "Search", "Directories", "Lyrics"]
        for i, name in enumerate(self.tab_names):
            b = QPushButton(f"   {name}   ")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, idx=i: self.switch_tab(idx))
            tabs_layout.addWidget(b)
            self.tab_btns.append(b)
        tabs_layout.addStretch()

        self.yt_toggle_btn = ModernIconButton("yt", "", (45, 26))
        self.yt_toggle_btn.setCheckable(True)
        self.yt_toggle_btn.clicked.connect(lambda _: self.toggle_yt_bar())
        tabs_layout.addWidget(self.yt_toggle_btn)

        self.stack = QStackedWidget()
        self.stack.setObjectName("boxFrame")

        q_page = QWidget()
        q_layout = QHBoxLayout(q_page)
        q_layout.setContentsMargins(4, 4, 4, 4)

        self.queue_main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.queue_cover = HDCoverWidget()
        self.queue_right_splitter = QSplitter(Qt.Orientation.Vertical)

        self.queue_table = QTableWidget(0, 5)
        self.queue_table.setHorizontalHeaderLabels(["Artist", "Title", "Album", "Playcount", "Len"])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setShowGrid(False)
        self.queue_table.cellDoubleClicked.connect(self._on_queue_double_click)

        self.cava_widget = CavaVisualizer()
        self.queue_right_splitter.addWidget(self.queue_table)
        self.queue_right_splitter.addWidget(self.cava_widget)
        self.queue_right_splitter.setStretchFactor(0, 3)
        self.queue_right_splitter.setStretchFactor(1, 2)

        self.queue_main_splitter.addWidget(self.queue_cover)
        self.queue_main_splitter.addWidget(self.queue_right_splitter)
        self.queue_main_splitter.setStretchFactor(0, 2)
        self.queue_main_splitter.setStretchFactor(1, 4)

        q_layout.addWidget(self.queue_main_splitter)
        self.stack.addWidget(q_page)

        self.stack.addWidget(self._create_browser_with_info_page("artists"))
        self.stack.addWidget(self._create_browser_with_info_page("albums"))

        s_page = QWidget()
        s_layout = QHBoxLayout(s_page)
        self.search_tag_list = QListWidget()
        for tag_id, lbl in [
            ("any", "Any Tag"), ("artist", "Artist"), ("album", "Album"),
            ("title", "Title"), ("filename", "Filename"), ("genre", "Genre"), ("albumartist", "Featured")
        ]:
            it = QListWidgetItem(lbl)
            it.setData(Qt.ItemDataRole.UserRole, tag_id)
            self.search_tag_list.addItem(it)
        self.search_tag_list.setCurrentRow(0)
        self.search_tag_list.currentRowChanged.connect(lambda _: self._run_search())

        s_mid = QVBoxLayout()
        self.search_input = CleanLineEdit("Click here to filter by artist, title, album or genre...")
        self.search_input.textChanged.connect(self._run_search)
        self.search_results_list = QListWidget()
        self.search_results_list.currentItemChanged.connect(
            lambda cur, _: self._update_info_panel(cur, self.search_info_lbl, self.search_info_cover)
        )
        self.search_results_list.itemDoubleClicked.connect(self._on_track_item_activated)
        s_mid.addWidget(self.search_input)
        s_mid.addWidget(self.search_results_list)

        s_right_widget, self.search_info_cover, self.search_info_lbl = self._create_right_info_column()
        s_layout.addWidget(self.search_tag_list, 1)
        s_layout.addLayout(s_mid, 3)
        s_layout.addWidget(s_right_widget, 2)
        self.stack.addWidget(s_page)

        self.stack.addWidget(self._create_browser_with_info_page("dirs", include_folder_picker=True))

        lyr_page = QWidget()
        lyr_layout = QHBoxLayout(lyr_page)
        self.lyrics_cover = HDCoverWidget()

        lyr_right = QVBoxLayout()
        lyr_top_bar = QHBoxLayout()
        self.lyr_status_lbl = QLabel("")
        self.btn_load_lrc = QPushButton("Load .lrc File...")
        self.btn_load_lrc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_load_lrc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_load_lrc.clicked.connect(self.manual_load_lrc)
        lyr_top_bar.addWidget(self.lyr_status_lbl)
        lyr_top_bar.addStretch()
        lyr_top_bar.addWidget(self.btn_load_lrc)

        self.lyrics_lbl = QLabel("No synchronized lyrics (.lrc) loaded.")
        self.lyrics_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyrics_lbl.setWordWrap(True)

        lyr_right.addLayout(lyr_top_bar)
        lyr_right.addWidget(self.lyrics_lbl, 1)

        lyr_layout.addWidget(self.lyrics_cover, 2)
        lyr_layout.addLayout(lyr_right, 3)
        self.stack.addWidget(lyr_page)

        self.bot_frame = QFrame()
        self.bot_frame.setObjectName("boxFrame")
        bot_layout = QHBoxLayout(self.bot_frame)
        bot_layout.setContentsMargins(10, 4, 10, 4)
        bot_layout.setSpacing(6)

        self.btn_prev = ModernIconButton("prev", (38, 26))
        self.btn_play = ModernIconButton("play", (46, 26))
        self.btn_next = ModernIconButton("next", (38, 26))
        self.btn_prev.clicked.connect(self.prev_track)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next.clicked.connect(self.next_track)

        bot_layout.addWidget(self.btn_prev)
        bot_layout.addWidget(self.btn_play)
        bot_layout.addWidget(self.btn_next)

        self.prog_slider = QSlider(Qt.Orientation.Horizontal)
        self.prog_slider.setRange(0, 1000)
        self.prog_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prog_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prog_slider.sliderMoved.connect(self._on_seek_slider)
        bot_layout.addWidget(self.prog_slider, 1)

        self.time_lbl = QLabel("0:00 / 0:00")
        bot_layout.addWidget(self.time_lbl)

    def toggle_editor_mode(self):
        if self.editor_dialog and self.editor_dialog.isVisible():
            self.editor_dialog.close()
            self.edit_mode = False
            self.edit_toggle_btn.setChecked(False)
            self._apply_dynamic_theme()
            self._save_user_customization()
            return

        self.edit_mode = True
        self.edit_toggle_btn.setChecked(True)
        self._apply_dynamic_theme()

        self.editor_dialog = jmpc_enhancements.EditorSettingsDialog(self, PRESET_THEMES)
        
        # Apply window opacity to editor dialog
        opacity = self.user_cfg.get("win_opacity", 100) / 100.0
        self.editor_dialog.setWindowOpacity(opacity)

        self.editor_dialog.finished.connect(self._on_editor_closed)
        self.editor_dialog.show()

    def _on_editor_closed(self):
        self.edit_mode = False
        self.edit_toggle_btn.setChecked(False)
        self._apply_dynamic_theme()
        self._save_user_customization()

    def _apply_layout_configuration(self):
        while self.blocks_layout.count():
            self.blocks_layout.takeAt(0)

        block_map = {
            "header": (self.hdr_frame, 0),
            "tabs": (self.tabs_frame, 0),
            "content": (self.stack, 1),
            "bottom": (self.bot_frame, 0),
        }
        for key in self.user_cfg["block_order"]:
            if key in block_map:
                w, stretch = block_map[key]
                self.blocks_layout.addWidget(w, stretch)

        if self.user_cfg["cover_on_right"]:
            self.queue_main_splitter.insertWidget(0, self.queue_right_splitter)
            self.queue_main_splitter.insertWidget(1, self.queue_cover)
        else:
            self.queue_main_splitter.insertWidget(0, self.queue_cover)
            self.queue_main_splitter.insertWidget(1, self.queue_right_splitter)

        if self.user_cfg["cava_on_top"]:
            self.queue_right_splitter.insertWidget(0, self.cava_widget)
            self.queue_right_splitter.insertWidget(1, self.queue_table)
        else:
            self.queue_right_splitter.insertWidget(0, self.queue_table)
            self.queue_right_splitter.insertWidget(1, self.cava_widget)

    def _on_opacity_changed(self, val: int):
        self.user_cfg["win_opacity"] = val
        self._apply_dynamic_theme()
        self._save_user_customization()

    def _on_progress_style_changed(self, style_name: str):
        self.user_cfg["progress_style"] = style_name
        self._apply_dynamic_theme()
        self._save_user_customization()

    def _toggle_cover_side(self):
        self.user_cfg["cover_on_right"] = not self.user_cfg["cover_on_right"]
        self._apply_layout_configuration()
        self._save_user_customization()

    def _toggle_cava_position(self):
        self.user_cfg["cava_on_top"] = not self.user_cfg["cava_on_top"]
        self._apply_layout_configuration()
        self._save_user_customization()

    def _swap_header_and_tabs(self):
        order = self.user_cfg["block_order"]
        if "header" in order and "tabs" in order:
            i1, i2 = order.index("header"), order.index("tabs")
            order[i1], order[i2] = order[i2], order[i1]
            self._apply_layout_configuration()
            self._save_user_customization()

    def _toggle_bottom_bar_position(self):
        order = self.user_cfg["block_order"]
        was_at_top = (order[0] == "bottom")
        order.remove("bottom")
        if was_at_top:
            order.append("bottom")
        else:
            order.insert(0, "bottom")
        self._apply_layout_configuration()
        self._save_user_customization()

    def _on_preset_selected(self, name: str):
        if name in PRESET_THEMES:
            self.user_cfg["current_theme"] = name
            self.user_cfg["colors"] = dict(PRESET_THEMES[name])
            self._apply_dynamic_theme()
            self._save_user_customization()

    def apply_wallpaper_colors(self):
        pal = jmpc_enhancements.get_windows_wallpaper_palette()
        if pal:
            self.user_cfg["colors"] = pal
            self._apply_dynamic_theme()
            self._save_user_customization()

    def changeEvent(self, event):
        if event.type() == event.Type.WindowStateChange:
            if self.isMinimized():
                self.hide()
                event.accept()
        super().changeEvent(event)

    def closeEvent(self, event):
        self._cleanup_tray_icon()
        event.accept()

    def _open_keybinds_dialog(self):
        dlg = jmpc_enhancements.KeybindsDialog(self, self.user_cfg["keybinds"], KEYBIND_LABELS)
        
        # Apply window opacity to keybinds dialog
        opacity = self.user_cfg.get("win_opacity", 100) / 100.0
        dlg.setWindowOpacity(opacity)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.user_cfg["keybinds"] = dlg.get_updated_keybinds()
            self._save_user_customization()

    def _apply_dynamic_theme(self):
        c = self.user_cfg["colors"]
        bg, pn, fg = c["bg"], c["panel"], c["text"]
        acc, sec, hi = c["accent"], c["secondary"], c["highlight"]

        opacity = self.user_cfg.get("win_opacity", 100) / 100.0
        self.setWindowOpacity(opacity)

        border_style = f"1px dashed {hi}" if self.edit_mode else f"1px solid {acc}"

        PROGRESS_STYLES = {
            "Pill Capsule": ("10px", "5px", "16px", "-3px", "8px"),
            "Neon Line": ("4px", "2px", "12px", "-4px", "6px"),
            "Block Retro": ("12px", "0px", "10px", "0px", "0px"),
            "Minimal Flat": ("6px", "0px", "6px", "-2px", "0px"),
        }
        p_style = self.user_cfg.get("progress_style", "Pill Capsule")
        gh, gr, hw, hm, hr = PROGRESS_STYLES.get(p_style, PROGRESS_STYLES["Pill Capsule"])

        self.setStyleSheet(f"""
            * {{ outline: none; }}
            QMainWindow, QDialog, QWidget {{
                background-color: {bg}; color: {fg};
                font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 13px;
            }}
            QFrame#boxFrame, QStackedWidget#boxFrame {{
                background-color: {bg}; border: {border_style}; border-radius: 3px;
            }}
            QListWidget, QTableWidget {{
                background-color: {bg}; border: 1px solid {acc};
                selection-background-color: {acc}; selection-color: #11111b;
            }}
            QHeaderView::section {{
                background-color: {pn}; color: {acc}; font-weight: bold; border: none; padding: 5px;
            }}
            QPushButton, QComboBox, QSpinBox {{
                background-color: {pn}; color: {fg}; border: 1px solid {acc}; padding: 4px 10px; border-radius: 3px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {pn}; color: {fg}; border: 1px solid {acc};
            }}
            QPushButton:hover, QPushButton:checked {{
                background-color: {acc}; color: #11111b; font-weight: bold;
            }}
            QLineEdit {{
                background-color: {pn}; color: {fg}; border: 1px solid {acc}; padding: 4px 6px; border-radius: 2px;
            }}
            QLineEdit:focus {{ border: 1px solid {sec}; }}
            QSlider::groove:horizontal {{ height: {gh}; border-radius: {gr}; background: {pn}; }}
            QSlider::sub-page:horizontal {{ background: {acc}; border-radius: {gr}; }}
            QSlider::handle:horizontal {{
                background: {sec}; width: {hw}; height: {hw};
                margin: {hm} 0; border-radius: {hr};
            }}
            QSplitter::handle {{ background-color: {acc}; }}
            QProgressBar {{
                background-color: {pn}; border: 1px solid {acc}; border-radius: 2px; text-align: center; color: {fg}; font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: {acc}; border-radius: 1px;
            }}
        """)

        self.vol_lbl.setStyleSheet(f"color: {acc}; font-weight: bold;")
        self.cava_widget.set_gradient_colors(sec, acc)
        for btn in (
            self.btn_prev, self.btn_play, self.btn_next,
            self.btn_random, self.btn_consume, self.btn_loop,
            self.edit_toggle_btn, self.yt_toggle_btn,
            self.yt_play_btn, self.yt_dl_btn
        ):
            btn.set_colors(fg, sec)
        self.switch_tab(self.stack.currentIndex())
        self._refresh_queue_table()

    def toggle_yt_bar(self):
        vis = not self.yt_panel.isVisible()
        self.yt_panel.setVisible(vis)
        self.yt_toggle_btn.setChecked(vis)
        if not vis:
            self._defocus_inputs()

    def _defocus_inputs(self):
        self.yt_input.clearFocus()
        self.search_input.clearFocus()
        self._focus_current_tab_list()

    def _create_right_info_column(self):
        container = QFrame()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(10, 8, 8, 8)
        vbox.setSpacing(8)
        cover_row = QHBoxLayout()
        cover_row.addStretch()
        small_cover = HDCoverWidget(fixed_side=165)
        cover_row.addWidget(small_cover)
        cover_row.addStretch()
        info_lbl = QLabel("--- [Info]\nSelect a track to inspect metadata.")
        info_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        info_lbl.setWordWrap(True)
        vbox.addLayout(cover_row)
        vbox.addWidget(info_lbl, 1)
        return container, small_cover, info_lbl

    def _create_browser_with_info_page(self, mode: str, include_folder_picker: bool = False):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 4, 4, 4)
        if include_folder_picker:
            top_bar = QHBoxLayout()
            self.dir_path_lbl = QLabel(f"Current Library: {self.music_dir}")
            btn_pick_folder = QPushButton("Select Music Folder...")
            btn_pick_folder.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_pick_folder.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_pick_folder.clicked.connect(self.choose_music_directory)
            top_bar.addWidget(self.dir_path_lbl, 1)
            top_bar.addWidget(btn_pick_folder)
            outer.addLayout(top_bar)
        layout = QHBoxLayout()
        cat_list = QListWidget()
        songs_list = QListWidget()
        right_col, small_cover, info_lbl = self._create_right_info_column()
        setattr(self, f"{mode}_cat_list", cat_list)
        setattr(self, f"{mode}_songs_list", songs_list)
        setattr(self, f"{mode}_info_lbl", info_lbl)
        setattr(self, f"{mode}_info_cover", small_cover)
        cat_list.currentTextChanged.connect(lambda txt, m=mode: self._populate_browser_songs(m, txt))
        songs_list.currentItemChanged.connect(lambda cur, _, lbl=info_lbl, cov=small_cover: self._update_info_panel(cur, lbl, cov))
        songs_list.itemDoubleClicked.connect(self._on_track_item_activated)
        layout.addWidget(cat_list, 2)
        layout.addWidget(songs_list, 4)
        layout.addWidget(right_col, 3)
        outer.addLayout(layout, 1)
        return page

    def choose_music_directory(self):
        selected = QFileDialog.getExistingDirectory(self, "Select Music Folder", str(self.music_dir))
        if selected:
            self.music_dir = Path(selected)
            self.user_cfg["music_dir"] = str(self.music_dir)
            if hasattr(self, "dir_path_lbl"):
                self.dir_path_lbl.setText(f"Current Library: {self.music_dir}")
            self._save_user_customization()
            self._scan_library(replace_queue=True)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.MouseButtonPress:
            focused = QApplication.focusWidget()
            if isinstance(focused, QLineEdit) and obj is not focused:
                self._defocus_inputs()
        if event.type() == event.Type.KeyPress:
            if QApplication.activeModalWidget() is not None:
                return super().eventFilter(obj, event)
            focused = QApplication.focusWidget()
            if isinstance(focused, QLineEdit):
                if event.key() == Qt.Key.Key_Escape:
                    self._defocus_inputs()
                    return True
                return super().eventFilter(obj, event)

            key = event.key()
            mods = event.modifiers()
            txt = event.text()
            kb = self.user_cfg["keybinds"]

            current_combo = ""
            if mods & Qt.KeyboardModifier.ControlModifier: current_combo += "ctrl+"
            if mods & Qt.KeyboardModifier.ShiftModifier: current_combo += "shift+"
            if mods & Qt.KeyboardModifier.AltModifier: current_combo += "alt+"
            current_combo += QKeySequence(key).toString().lower()

            kb_full = kb.get("fullscreen_toggle", "ctrl+f").lower().replace(" ", "")
            if current_combo == kb_full or ((mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_F):
                if self.isFullScreen(): self.showNormal()
                else: self.showFullScreen()
                return True

            kb_tray = kb.get("tray_minimize", "ctrl+q").lower().replace(" ", "")
            if current_combo == kb_tray or ((mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_Q):
                self.hide()
                return True

            if key == Qt.Key.Key_Left and (mods & Qt.KeyboardModifier.ShiftModifier):
                self.seek_relative(-5000)
                return True
            if key == Qt.Key.Key_Right and (mods & Qt.KeyboardModifier.ShiftModifier):
                self.seek_relative(5000)
                return True
            if key == Qt.Key.Key_Left:
                if self.stack.currentIndex() in (0, 5): self.seek_relative(-5000)
                else: self._move_browser_column(-1)
                return True
            if key == Qt.Key.Key_Right:
                if self.stack.currentIndex() in (0, 5): self.seek_relative(5000)
                else: self._move_browser_column(1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_focused_item()
                return True
            if txt in ("1", "2", "3", "4", "5", "6"):
                self.switch_tab(int(txt) - 1)
                return True
            if txt == kb.get("editor_mode", "p"):
                self.toggle_editor_mode()
                return True
            if txt == kb.get("youtube_bar", "y"):
                self.toggle_yt_bar()
                return True
            if (kb.get("play_pause", "space").lower() == "space" and key == Qt.Key.Key_Space) or txt == kb.get("play_pause"):
                self.toggle_play()
                return True
            if txt in (kb.get("vol_up", "+"), ".", "="):
                self.change_volume(5)
                return True
            if txt in (kb.get("vol_down", "-"), ","):
                self.change_volume(-5)
                return True
            if txt == kb.get("next_track", ">"):
                self.next_track()
                return True
            if txt == kb.get("prev_track", "<"):
                self.prev_track()
                return True
            if txt == kb.get("cava_mode", "v"):
                self.cava_widget.next_mode()
                return True
            if txt == kb.get("add_queue", "a"):
                self.add_highlighted_to_queue()
                return True
            if txt == kb.get("delete_queue", "d") and self.stack.currentIndex() == 0:
                self._delete_selected_from_queue()
                return True
            if txt == kb.get("random_mode", "x"):
                self.btn_random.setChecked(not self.btn_random.isChecked())
                self.st_random = self.btn_random.isChecked()
                return True
            if txt == kb.get("consume_mode", "c"):
                self.btn_consume.setChecked(not self.btn_consume.isChecked())
                self.st_consume = self.btn_consume.isChecked()
                return True
            if txt == kb.get("loop_mode", "r"):
                self.btn_loop.setChecked(not self.btn_loop.isChecked())
                self.st_loop = self.btn_loop.isChecked()
                return True
        return super().eventFilter(obj, event)

    def _move_browser_column(self, direction: int):
        idx = self.stack.currentIndex()
        col_pairs = {
            1: (self.artists_cat_list, self.artists_songs_list),
            2: (self.albums_cat_list, self.albums_songs_list),
            3: (self.search_tag_list, self.search_results_list),
            4: (self.dirs_cat_list, self.dirs_songs_list),
        }
        if idx in col_pairs:
            left_w, right_w = col_pairs[idx]
            (left_w if direction < 0 else right_w).setFocus()

    def _activate_focused_item(self):
        w = QApplication.focusWidget()
        if w == self.queue_table and self.queue_table.currentRow() >= 0:
            self.play_index(self.queue_table.currentRow())
        elif isinstance(w, QListWidget) and w.currentItem():
            t = w.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(t, Track):
                self._on_track_item_activated(w.currentItem())
            else:
                self._move_browser_column(1)

    def _focus_current_tab_list(self):
        idx = self.stack.currentIndex()
        targets = {
            0: self.queue_table, 1: self.artists_cat_list,
            2: self.albums_cat_list, 3: self.search_results_list, 4: self.dirs_cat_list,
        }
        if idx in targets:
            targets[idx].setFocus()

    def switch_tab(self, idx: int):
        self.stack.setCurrentIndex(idx)
        acc = self.user_cfg["colors"]["accent"]
        for i, b in enumerate(self.tab_btns):
            if i == idx: b.setStyleSheet(f"background-color: {acc}; color: #11111b; font-weight: bold;")
            else: b.setStyleSheet("")
        self._focus_current_tab_list()

    def _refresh_browser_widgets(self):
        for mode, data_map in (("artists", self.artists_map), ("albums", self.albums_map), ("dirs", self.dirs_map)):
            cat_w = getattr(self, f"{mode}_cat_list")
            prev_selected = cat_w.currentItem().text() if cat_w.currentItem() else None
            cat_w.clear()
            for k in sorted(data_map.keys()):
                cat_w.addItem(k)
            if cat_w.count() > 0:
                if prev_selected:
                    matches = cat_w.findItems(prev_selected, Qt.MatchFlag.MatchExactly)
                    if matches:
                        cat_w.setCurrentItem(matches[0])
                        continue
                cat_w.setCurrentRow(0)
            else:
                getattr(self, f"{mode}_songs_list").clear()

    def _scan_library(self, replace_queue: bool = False):
        self.all_tracks.clear()
        self.artists_map.clear()
        self.albums_map.clear()
        self.dirs_map.clear()

        if self.music_dir.exists():
            max_tracks, count = 3500, 0
            for root, dirs, files in os.walk(self.music_dir):
                dirs[:] = [d for d in dirs if not d.startswith((".", "$"))]
                for f in sorted(files):
                    if Path(f).suffix.lower() in SUPPORTED_AUDIO_EXTS:
                        full_path = Path(root) / f
                        try:
                            if full_path.stat().st_size > 350 * 1024 * 1024:
                                continue
                        except Exception:
                            continue

                        t = Track(full_path, self.music_dir)
                        self.all_tracks.append(t)
                        self.artists_map.setdefault(t.artist, []).append(t)
                        self.albums_map.setdefault(t.album, []).append(t)
                        self.dirs_map.setdefault(t.path.parent.name or str(t.path.parent), []).append(t)
                        count += 1
                        if count >= max_tracks:
                            break
                if count >= max_tracks:
                    break

        if replace_queue:
            if self.all_tracks:
                if self.current_track and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    if self.current_track not in self.all_tracks:
                        self.queue = [self.current_track] + list(self.all_tracks)
                        self.current_idx = 0
                    else:
                        self.queue = list(self.all_tracks)
                        self.current_idx = self.queue.index(self.current_track)
                else:
                    self.queue = list(self.all_tracks)
                    self.current_track = self.queue[0]
                    self.current_idx = 0
                    self._update_metadata_views(self.current_track)
        elif not self.queue and self.all_tracks:
            self.queue = list(self.all_tracks)

        self._refresh_queue_table()
        self._refresh_browser_widgets()
        self._run_search()

    def _populate_browser_songs(self, mode: str, key: str):
        songs_w = getattr(self, f"{mode}_songs_list")
        songs_w.clear()
        data_map = {"artists": self.artists_map, "albums": self.albums_map, "dirs": self.dirs_map}[mode]
        for t in data_map.get(key, []):
            try: tn = f"{int(t.track_num):02d}"
            except ValueError: tn = t.track_num
            it = QListWidgetItem(f"{tn}  {t.artist} - {t.title}")
            it.setData(Qt.ItemDataRole.UserRole, t)
            songs_w.addItem(it)
        if songs_w.count() > 0:
            songs_w.setCurrentRow(0)

    def _update_info_panel(self, item: QListWidgetItem | None, label: QLabel, cover_widget: HDCoverWidget):
        if not item:
            return
        t = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(t, Track):
            return
        cover_widget.set_cover_bytes(t.cover_bytes)
        hi = self.user_cfg["colors"]["highlight"]
        label.setText(
            f"<b style='color:{hi};'>--- [Info]</b><br>"
            f"<span style='color:{hi};'>File:</span> {t.rel_file}<br>"
            f"<span style='color:{hi};'>Filename:</span> {t.filename}<br>"
            f"<span style='color:{hi};'>Title:</span> {t.title}<br>"
            f"<span style='color:{hi};'>Artist:</span> {t.artist}<br>"
            f"<span style='color:{hi};'>Album:</span> {t.album}<br>"
            f"<span style='color:{hi};'>Duration:</span> {t.duration_str} ({t.bitrate} kbps)<br><br>"
            f"<b style='color:{hi};'>--- [Tags]</b><br>"
            f"<span style='color:{hi};'>added:</span> {t.added_iso}<br>"
            f"<span style='color:{hi};'>albumartist:</span> {t.album_artist}<br>"
            f"<span style='color:{hi};'>date:</span> {t.date}<br>"
            f"<span style='color:{hi};'>genre:</span> {t.genre}<br>"
            f"<span style='color:{hi};'>last-modified:</span> {t.modified_iso}<br>"
            f"<span style='color:{hi};'>track:</span> {t.track_num}"
        )

    def _on_track_item_activated(self, item: QListWidgetItem):
        t = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(t, Track):
            if t not in self.queue:
                self.queue.append(t)
                self._refresh_queue_table()
            self.play_index(self.queue.index(t))

    def add_highlighted_to_queue(self):
        w = QApplication.focusWidget()
        if isinstance(w, QListWidget) and w.currentItem():
            t = w.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(t, Track):
                self.queue.append(t)
                self._refresh_queue_table()

    def _delete_selected_from_queue(self):
        row = self.queue_table.currentRow()
        if 0 <= row < len(self.queue):
            self.queue.pop(row)
            if row < self.current_idx:
                self.current_idx -= 1
            self._refresh_queue_table()

    def _run_search(self):
        self.search_results_list.clear()
        q = self.search_input.text().lower().strip()
        tag_item = self.search_tag_list.currentItem()
        tag_mode = tag_item.data(Qt.ItemDataRole.UserRole) if tag_item else "any"

        for t in self.all_tracks:
            if not q: ok = True
            elif tag_mode == "artist": ok = q in t.artist.lower()
            elif tag_mode == "album": ok = q in t.album.lower()
            elif tag_mode == "title": ok = q in t.title.lower()
            elif tag_mode == "filename": ok = q in t.filename.lower()
            elif tag_mode == "genre": ok = q in t.genre.lower()
            elif tag_mode == "albumartist": ok = q in t.album_artist.lower()
            else: ok = any(q in f.lower() for f in (t.artist, t.album, t.title, t.genre, t.filename))
            if ok:
                it = QListWidgetItem(f"{t.track_num}  {t.artist} - {t.title}")
                it.setData(Qt.ItemDataRole.UserRole, t)
                self.search_results_list.addItem(it)
        if self.search_results_list.count() > 0:
            self.search_results_list.setCurrentRow(0)

    def _refresh_queue_table(self):
        self.queue_table.setRowCount(len(self.queue))
        sec_col = QColor(self.user_cfg["colors"]["secondary"])
        for row, t in enumerate(self.queue):
            pc = self.play_counts.get(t.rel_file, 0)
            vals = [t.artist, t.title, t.album, str(pc), t.duration_str]
            is_cur = (t == self.current_track)
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col in (3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if is_cur:
                    item.setForeground(sec_col)
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.queue_table.setItem(row, col, item)

    def _on_queue_double_click(self, row: int, _col: int):
        self.play_index(row)

    def play_index(self, idx: int):
        if not (0 <= idx < len(self.queue)):
            return
        self.current_idx = idx
        self.current_track = self.queue[idx]
        t = self.current_track
        self.play_counts[t.rel_file] = self.play_counts.get(t.rel_file, 0) + 1

        self.user_wants_playing = True
        self.stall_ticks = 0
        self.last_known_pos_ms = 0

        if t.is_youtube:
            self.player.setSource(QUrl(t.yt_url))
        else:
            self.player.setSource(QUrl.fromLocalFile(str(t.path)))

        self.player.play()
        self.btn_play.set_mode("pause")
        self._update_metadata_views(t)
        self._refresh_queue_table()

    def _update_metadata_views(self, t: Track):
        self.queue_cover.set_cover_bytes(t.cover_bytes)
        self.lyrics_cover.set_cover_bytes(t.cover_bytes)
        hi = self.user_cfg["colors"]["highlight"]
        self.hdr_center.setText(f"<b style='font-size:14px;'>{t.title}</b><br><b style='color:{hi};'>{t.artist}</b> - {t.album}")
        self._load_lyrics(t)

    def _find_lrc_for_track(self, t: Track):
        if t.custom_lrc_path and t.custom_lrc_path.exists():
            return t.custom_lrc_path
        exact = t.path.with_suffix(".lrc")
        if exact.exists():
            return exact
        parent = t.path.parent
        if not parent.exists():
            return None
        norm_stem = re.sub(r"[\W_]+", "", t.path.stem.lower())
        norm_title = re.sub(r"[\W_]+", "", t.title.lower())
        for lrc_candidate in parent.glob("*.lrc"):
            cand_norm = re.sub(r"[\W_]+", "", lrc_candidate.stem.lower())
            if cand_norm == norm_stem or (norm_title and norm_title in cand_norm):
                return lrc_candidate
        return None

    def _load_lyrics(self, t: Track):
        self.lyrics_lines.clear()
        if t.is_youtube:
            self.lyr_status_lbl.setText("Source: YouTube Stream")
            self.lyrics_lbl.setText("Playing YouTube Stream.")
            return

        lrc_file = self._find_lrc_for_track(t)
        if lrc_file:
            self.lyrics_lines = parse_lrc_text(read_text_multi_encoding(lrc_file))
            self.lyr_status_lbl.setText(f"Loaded: {lrc_file.name}")
        elif t.embedded_lyrics:
            self.lyrics_lines = parse_lrc_text(t.embedded_lyrics)
            self.lyr_status_lbl.setText("Loaded: Embedded Lyrics")
        else:
            self.lyr_status_lbl.setText("No local .lrc detected")

        if self.lyrics_lines:
            self._render_lyrics_at_pos(0)
        else:
            self.lyrics_lbl.setText("No synchronized lyrics (.lrc) found.")

    def manual_load_lrc(self):
        if not self.current_track:
            return
        start_dir = str(self.current_track.path.parent if self.current_track.path.exists() else self.music_dir)
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Lyrics File", start_dir, "Lyrics Files (*.lrc *.txt);;All Files (*)")
        if file_path:
            p = Path(file_path)
            self.current_track.custom_lrc_path = p
            self.lyrics_lines = parse_lrc_text(read_text_multi_encoding(p))
            self.lyr_status_lbl.setText(f"Loaded manually: {p.name}")
            self._render_lyrics_at_pos(self.player.position())

    def _render_lyrics_at_pos(self, pos_ms: int):
        if not self.lyrics_lines:
            return
        cur_s = pos_ms / 1000.0
        idx = 0
        for i, (t_s, _) in enumerate(self.lyrics_lines):
            if cur_s >= t_s:
                idx = i
        sec_hex = self.user_cfg["colors"]["secondary"]
        lines_html = []
        for i in range(max(0, idx - 4), min(len(self.lyrics_lines), idx + 6)):
            _, l_txt = self.lyrics_lines[i]
            display_txt = l_txt if l_txt else "..."
            if i == idx:
                lines_html.append(f"<b style='color:{sec_hex}; font-size:16px;'>&gt; {display_txt} &lt;</b>")
            else:
                lines_html.append(f"<span style='color:#6c7086;'>{display_txt}</span>")
        self.lyrics_lbl.setText("<br>".join(lines_html))

    def play_youtube_url(self):
        self._defocus_inputs()
        url = self.yt_input.text().strip()
        if not url:
            return
        self.yt_play_btn.setEnabled(False)
        self.yt_stream_worker = YouTubeStreamWorker(url, resume_pos_ms=0)
        self.yt_stream_worker.finished_ok.connect(self._on_youtube_ready)
        self.yt_stream_worker.failed.connect(self._on_youtube_error)
        self.yt_stream_worker.start()

    def _on_youtube_ready(self, data: dict):
        self.yt_play_btn.setEnabled(True)
        resume_ms = data.get("resume_pos_ms", 0)

        if resume_ms > 0 and self.current_track and self.current_track.is_youtube:
            self.is_recovering_stream = False
            self.current_track.yt_url = data["stream_url"]
            self.player.setSource(QUrl(data["stream_url"]))
            self.player.play()
            self.btn_play.set_mode("pause")
            QTimer.singleShot(400, lambda: self.player.setPosition(resume_ms))
            return

        yt_track = Track(Path("YouTube_Stream.mp3"), self.music_dir)
        yt_track.is_youtube = True
        yt_track.yt_url = data["stream_url"]
        yt_track.orig_yt_url = data["orig_url"]
        yt_track.rel_file = data["orig_url"]
        yt_track.filename = "YouTube Stream"
        yt_track.title = data["title"]
        yt_track.artist = data["artist"]
        yt_track.album = "YouTube"
        yt_track.duration = data["duration"]
        yt_track.bitrate = data.get("bitrate", 160)
        yt_track.cover_bytes = data["cover_bytes"]
        self.queue.append(yt_track)
        self._refresh_queue_table()
        self.play_index(len(self.queue) - 1)

    def _check_stream_health(self):
        if not self.user_wants_playing or not self.current_track or not self.current_track.is_youtube:
            self.stall_ticks = 0
            return
        if self.is_recovering_stream:
            return

        status = self.player.mediaStatus()
        state = self.player.playbackState()
        cur_pos = self.player.position()
        dur = self.player.duration()

        if dur > 0 and cur_pos >= dur - 1500:
            return

        is_stalled = (
            status in (QMediaPlayer.MediaStatus.StalledMedia, QMediaPlayer.MediaStatus.InvalidMedia)
            or state != QMediaPlayer.PlaybackState.PlayingState
            or (cur_pos == self.last_known_pos_ms and cur_pos > 0)
        )

        if is_stalled:
            self.stall_ticks += 1
            if self.stall_ticks == 2:
                self.player.play()
                if cur_pos > 0:
                    self.player.setPosition(cur_pos)
            elif self.stall_ticks >= 4:
                self.stall_ticks = 0
                self._recover_youtube_stream(cur_pos)
        else:
            self.stall_ticks = 0

    def _recover_youtube_stream(self, resume_ms: int):
        if not self.current_track or not self.current_track.orig_yt_url or self.is_recovering_stream:
            return
        self.is_recovering_stream = True
        self.yt_stream_worker = YouTubeStreamWorker(self.current_track.orig_yt_url, resume_pos_ms=resume_ms)
        self.yt_stream_worker.finished_ok.connect(self._on_youtube_ready)
        self.yt_stream_worker.failed.connect(self._on_stream_recovery_failed)
        self.yt_stream_worker.start()

    def _on_stream_recovery_failed(self, _err: str):
        self.is_recovering_stream = False

    def _on_player_error(self, _error, _error_str: str):
        if self.user_wants_playing and self.current_track and self.current_track.is_youtube:
            self._recover_youtube_stream(self.last_known_pos_ms)

    def download_youtube_url(self):
        self._defocus_inputs()
        url = self.yt_input.text().strip()
        if not url:
            return
        self.yt_dl_btn.setEnabled(False)
        self.yt_progress.setVisible(True)
        self.yt_progress.setValue(0)

        dest = self.music_dir if self.music_dir.name == "YouTube_Downloads" else (self.music_dir / "YouTube_Downloads")
        self.yt_dl_worker = YouTubeDownloadWorker(url, dest)
        self.yt_dl_worker.progress_updated.connect(lambda p: self.yt_progress.setValue(p))
        self.yt_dl_worker.finished_ok.connect(self._on_youtube_downloaded)
        self.yt_dl_worker.failed.connect(self._on_youtube_error)
        self.yt_dl_worker.start()

    def _on_youtube_downloaded(self, filepath: str):
        self.yt_dl_btn.setEnabled(True)
        self.yt_progress.setVisible(False)
        self.yt_progress.setValue(0)
        p = Path(filepath)
        if p.exists():
            new_track = Track(p, self.music_dir)
            self.all_tracks.append(new_track)
            self.queue.append(new_track)
            self.artists_map.setdefault(new_track.artist, []).append(new_track)
            self.albums_map.setdefault(new_track.album, []).append(new_track)
            self.dirs_map.setdefault(p.parent.name or str(p.parent), []).append(new_track)
            self._refresh_queue_table()
            self._refresh_browser_widgets()
            self._run_search()
            self.hdr_center.setText(f"<b>Downloaded:</b><br>{p.name}")

    def _on_youtube_error(self, err: str):
        self.is_recovering_stream = False
        self.yt_dl_btn.setEnabled(True)
        self.yt_progress.setVisible(False)
        self.yt_progress.setValue(0)
        self.hdr_center.setText(f"<b>YouTube Error:</b><br>{err[:80]}")

    def seek_relative(self, delta_ms: int):
        dur = self.player.duration()
        if dur > 0:
            target = max(0, min(dur - 500, self.player.position() + delta_ms))
            self.last_known_pos_ms = target
            self.player.setPosition(target)

    def change_volume(self, delta: int):
        self.vol_slider.setValue(max(0, min(100, self.vol_slider.value() + delta)))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.user_wants_playing = False
            self.player.pause()
            self.btn_play.set_mode("play")
        else:
            if self.current_idx == -1 and self.queue:
                self.play_index(0)
            else:
                self.user_wants_playing = True
                self.player.play()
                self.btn_play.set_mode("pause")

    def _consume_current_if_enabled(self):
        if self.st_consume and 0 <= self.current_idx < len(self.queue):
            self.queue.pop(self.current_idx)
            self.current_idx -= 1
            self._refresh_queue_table()

    def next_track(self):
        if not self.queue:
            return
        self._consume_current_if_enabled()
        if not self.queue:
            self.user_wants_playing = False
            self.player.stop()
            self.current_track = None
            self.current_idx = -1
            self.btn_play.set_mode("play")
            return
        if self.st_random:
            self.play_index(random.randint(0, len(self.queue) - 1))
        elif self.current_idx + 1 < len(self.queue):
            self.play_index(self.current_idx + 1)

    def prev_track(self):
        if self.current_idx - 1 >= 0:
            self.play_index(self.current_idx - 1)

    def _on_vol_changed(self, val: int):
        self.audio_out.setVolume(val / 100.0)
        self.vol_lbl.setText(f"{val}%")

    def _on_seek_slider(self, val: int):
        dur = self.player.duration()
        if dur > 0:
            target = int((val / 1000.0) * dur)
            self.last_known_pos_ms = target
            self.player.setPosition(target)

    def _on_position_changed(self, pos_ms: int):
        if pos_ms > 0:
            self.last_known_pos_ms = pos_ms
        dur_ms = self.player.duration()
        sec = pos_ms // 1000
        tot = (dur_ms // 1000) if dur_ms > 0 else (self.current_track.duration if self.current_track else 0)
        if dur_ms > 0 and not self.prog_slider.isSliderDown():
            self.prog_slider.setValue(int((pos_ms / dur_ms) * 1000))

        em, es = int(sec // 60), int(sec % 60)
        tm, ts = int(tot // 60), int(tot % 60)
        kbps = self.current_track.bitrate if self.current_track else 0
        state_txt = "Playing" if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState else "Paused"
        hi = self.user_cfg["colors"]["highlight"]

        self.hdr_left.setText(f"<b style='color:{hi};'>[{state_txt}]</b><br>{em}:{es:02d} / {tm}:{ts:02d} ({kbps} kbps)")
        self.time_lbl.setText(f"{em}:{es:02d} / {tm}:{ts:02d}")
        if self.lyrics_lines:
            self._render_lyrics_at_pos(pos_ms)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.st_loop and self.current_track:
                self.player.setPosition(0)
                self.player.play()
            else:
                self.next_track()
        elif status == QMediaPlayer.MediaStatus.BufferedMedia and self.user_wants_playing:
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.player.play()

    def _tick_visualizer(self):
        playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.cava_widget.step(playing, self.player.position())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    base_dir = Path(__file__).parent
    raw_arg = " ".join(sys.argv[1:]).strip('"').strip("'")
    arg_dir = Path(raw_arg) if raw_arg and Path(raw_arg).exists() else None
    win = RMPCWindow(config_dir=base_dir, initial_music_dir=arg_dir)
    win.show()
    sys.exit(app.exec())
    
# Hi