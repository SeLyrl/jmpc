import os
import re
import math
import colorsys
import ctypes
from pathlib import Path
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QRadialGradient, QConicalGradient, QGuiApplication
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QStackedWidget, QSlider, QFrame, QDialog,
    QSpinBox, QGridLayout, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCheckBox
)


def get_windows_wallpaper_palette() -> dict | None:
    try:
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.SystemParametersInfoW(0x0073, 512, buf, 0)
        wp_path = Path(buf.value)
        if not wp_path.exists():
            wp_path = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
        if not wp_path.exists():
            return None

        from PIL import Image
        img = Image.open(wp_path).convert("RGB").resize((150, 150), Image.Resampling.LANCZOS)
        quant = img.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette()

        counts = sorted(quant.getcolors(), reverse=True)
        clusters = []
        for count, idx in counts:
            r, g, b = palette[idx * 3 : idx * 3 + 3]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            clusters.append({"count": count, "rgb": (r, g, b), "hsv": (h, s, v)})

        if not clusters:
            return None

        colorful = [c for c in clusters if c["hsv"][1] > 0.15 and 0.15 < c["hsv"][2] < 0.96]
        if not colorful:
            colorful = clusters

        colorful.sort(key=lambda c: c["count"] * (0.4 + c["hsv"][1]), reverse=True)
        primary = colorful[0]

        secondary = None
        for cand in colorful[1:]:
            hue_dist = min(abs(cand["hsv"][0] - primary["hsv"][0]), 1.0 - abs(cand["hsv"][0] - primary["hsv"][0]))
            if hue_dist > 0.08:
                secondary = cand
                break
        if not secondary:
            secondary = colorful[1] if len(colorful) > 1 else primary

        def make_vibrant_hex(c_dict, min_sat=0.35, min_val=0.72, max_val=0.92):
            h, s, v = c_dict["hsv"]
            s = max(min_sat, min(0.85, s * 1.15))
            v = max(min_val, min(max_val, v * 1.1))
            r, g, b = colorsys.hsv_to_rgb(h, s, v)
            return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"

        acc_hex = make_vibrant_hex(primary, 0.35, 0.78, 0.92)
        sec_hex = make_vibrant_hex(secondary, 0.35, 0.72, 0.90)

        bg_base = secondary if secondary["hsv"][2] < primary["hsv"][2] else primary
        bh, bs, _ = bg_base["hsv"]
        bg_r, bg_g, bg_b = colorsys.hsv_to_rgb(bh, min(0.55, max(0.25, bs * 0.75)), 0.17)
        pn_r, pn_g, pn_b = colorsys.hsv_to_rgb(bh, min(0.60, max(0.28, bs * 0.80)), 0.12)

        hh, hs, _ = primary["hsv"]
        hi_r, hi_g, hi_b = colorsys.hsv_to_rgb((hh + 0.05) % 1.0, min(0.50, hs * 0.7), 0.94)

        return {
            "bg": f"#{int(bg_r * 255):02x}{int(bg_g * 255):02x}{int(bg_b * 255):02x}",
            "panel": f"#{int(pn_r * 255):02x}{int(pn_g * 255):02x}{int(pn_b * 255):02x}",
            "text": "#f2f4f8",
            "accent": acc_hex,
            "secondary": sec_hex,
            "highlight": f"#{int(hi_r * 255):02x}{int(hi_g * 255):02x}{int(hi_b * 255):02x}",
        }
    except Exception:
        return None


class SyncedColorWheel(QWidget):
    color_selected = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(210, 210)
        self.hue = 0
        self.sat = 0

    def set_hs(self, hue: int, sat: int):
        self.hue = max(0, hue) % 360
        self.sat = max(0, min(255, sat))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 14
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        center = rect.center()
        radius = side / 2.0

        conical = QConicalGradient(center, 0)
        for deg in range(0, 361, 60):
            conical.setColorAt(deg / 360.0, QColor.fromHsv(deg % 360, 255, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(conical)
        p.drawEllipse(rect)

        radial = QRadialGradient(center, radius)
        radial.setColorAt(0.0, QColor(255, 255, 255, 255))
        radial.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(radial)
        p.drawEllipse(rect)

        angle_rad = math.radians(self.hue)
        dist = (self.sat / 255.0) * radius
        px = center.x() + dist * math.cos(angle_rad)
        py = center.y() - dist * math.sin(angle_rad)
        p.setPen(QPen(QColor("#ffffff"), 3.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(px, py), 6, 6)
        p.setPen(QPen(QColor("#11111b"), 1.5))
        p.drawEllipse(QPointF(px, py), 6, 6)

    def mousePressEvent(self, event):
        self._pick(event.position())

    def mouseMoveEvent(self, event):
        self._pick(event.position())

    def _pick(self, pos: QPointF):
        side = min(self.width(), self.height()) - 14
        cx, cy = self.width() / 2.0, self.height() / 2.0
        radius = max(1.0, side / 2.0)
        dx, dy = pos.x() - cx, cy - pos.y()
        dist = min(radius, math.hypot(dx, dy))
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360
        self.hue = int(angle) % 360
        self.sat = int((dist / radius) * 255)
        self.color_selected.emit(self.hue, self.sat)
        self.update()


class SwatchGridCanvas(QWidget):
    color_selected = pyqtSignal(QColor)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(210, 210)
        self.swatches = []
        for v in (255, 210, 165, 120, 75, 40):
            for h in range(0, 360, 30):
                self.swatches.append(QColor.fromHsv(h, 200, v))

    def paintEvent(self, event):
        p = QPainter(self)
        cols, rows = 12, 6
        cw, ch = self.width() / cols, self.height() / rows
        for idx, col in enumerate(self.swatches):
            r, c = divmod(idx, cols)
            p.fillRect(QRectF(c * cw + 2, r * ch + 2, cw - 4, ch - 4), col)

    def mousePressEvent(self, event):
        cols, rows = 12, 6
        c = min(cols - 1, max(0, int(event.position().x() // (self.width() / cols))))
        r = min(rows - 1, max(0, int(event.position().y() // (self.height() / rows))))
        idx = r * cols + c
        if 0 <= idx < len(self.swatches):
            self.color_selected.emit(self.swatches[idx])


class CustomColorDialog(QDialog):
    def __init__(self, parent, initial_hex: str, title: str = "Select Color"):
        super().__init__(parent, Qt.WindowType.Dialog)
        
        bg_col = "#181825"
        text_col = "#cdd6f4"
        accent_col = "#89b4fa"
        opacity = 1.0
        
        if parent and hasattr(parent, "user_cfg"):
            opacity = parent.user_cfg.get("win_opacity", 100) / 100.0
            colors = parent.user_cfg.get("colors", {})
            bg_col = colors.get("bg", bg_col)
            text_col = colors.get("text", text_col)
            accent_col = colors.get("accent", accent_col)

        # Force window attribute for transparency and set opacity directly
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowOpacity(opacity)

        self.setWindowTitle(title)
        self.resize(480, 390)
        self.current_color = QColor(initial_hex) if QColor(initial_hex).isValid() else QColor("#89b4fa")
        
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background-color: {bg_col}; color: {text_col}; font-family: 'Cascadia Mono', monospace; font-size: 13px; }}
            QPushButton, QSpinBox, QLineEdit {{
                background-color: {bg_col}; color: {text_col};
                border: 1px solid {accent_col}; padding: 4px 8px; border-radius: 3px;
            }}
            QPushButton:checked, QPushButton:hover {{ background-color: {accent_col}; color: #11111b; font-weight: bold; }}
        """)

        root = QVBoxLayout(self)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Selector Mode:"))
        self.btn_bars = QPushButton("Bars")
        self.btn_circle = QPushButton("Circle")
        self.btn_squares = QPushButton("Squares")
        for idx, b in enumerate((self.btn_bars, self.btn_circle, self.btn_squares)):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, i=idx: self._switch_picker_mode(i))
            mode_row.addWidget(b)
        mode_row.addStretch()
        root.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        bars_page = QWidget()
        bars_layout = QGridLayout(bars_page)
        self.r_slider = QSlider(Qt.Orientation.Horizontal)
        self.g_slider = QSlider(Qt.Orientation.Horizontal)
        self.b_slider = QSlider(Qt.Orientation.Horizontal)
        self.v_slider = QSlider(Qt.Orientation.Horizontal)
        for row_i, (lbl, sld) in enumerate((
            ("Red", self.r_slider), ("Green", self.g_slider),
            ("Blue", self.b_slider), ("Brightness", self.v_slider)
        )):
            sld.setRange(0, 255)
            sld.valueChanged.connect(self._on_rgb_sliders_moved)
            bars_layout.addWidget(QLabel(lbl), row_i, 0)
            bars_layout.addWidget(sld, row_i, 1)
        self.mode_stack.addWidget(bars_page)

        circle_page = QWidget()
        circle_layout = QHBoxLayout(circle_page)
        self.wheel = SyncedColorWheel()
        self.wheel.color_selected.connect(self._on_wheel_picked)
        self.wheel_val_slider = QSlider(Qt.Orientation.Vertical)
        self.wheel_val_slider.setRange(0, 255)
        self.wheel_val_slider.setValue(220)
        self.wheel_val_slider.valueChanged.connect(self._on_wheel_val_changed)
        circle_layout.addWidget(self.wheel, 1)
        circle_layout.addWidget(self.wheel_val_slider)
        self.mode_stack.addWidget(circle_page)

        self.squares = SwatchGridCanvas()
        self.squares.color_selected.connect(self._set_color)
        self.mode_stack.addWidget(self.squares)
        root.addWidget(self.mode_stack, 1)

        bot_grid = QHBoxLayout()
        self.preview_box = QLabel()
        self.preview_box.setFixedSize(64, 52)
        bot_grid.addWidget(self.preview_box)

        rgb_form = QGridLayout()
        self.sp_r, self.sp_g, self.sp_b = QSpinBox(), QSpinBox(), QSpinBox()
        for idx, (lbl, sp) in enumerate((("Red:", self.sp_r), ("Green:", self.sp_g), ("Blue:", self.sp_b))):
            sp.setRange(0, 255)
            sp.valueChanged.connect(self._on_spinbox_changed)
            rgb_form.addWidget(QLabel(lbl), idx, 0)
            rgb_form.addWidget(sp, idx, 1)
        bot_grid.addLayout(rgb_form)

        hex_box = QVBoxLayout()
        hex_box.addWidget(QLabel("HTML / Hex Code:"))
        self.hex_input = QLineEdit()
        self.hex_input.textEdited.connect(self._on_hex_edited)
        hex_box.addWidget(self.hex_input)
        bot_grid.addLayout(hex_box, 1)
        root.addLayout(bot_grid)

        dlg_btns = QHBoxLayout()
        dlg_btns.addStretch()
        btn_ok = QPushButton("Apply Color")
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        dlg_btns.addWidget(btn_ok)
        dlg_btns.addWidget(btn_cancel)
        root.addLayout(dlg_btns)

        self._switch_picker_mode(1)
        self._set_color(self.current_color)

    def _switch_picker_mode(self, idx: int):
        self.mode_stack.setCurrentIndex(idx)
        for i, b in enumerate((self.btn_bars, self.btn_circle, self.btn_squares)):
            b.setChecked(i == idx)

    def _set_color(self, col: QColor):
        if not col.isValid():
            return
        self.current_color = col
        self.preview_box.setStyleSheet(f"background-color: {col.name()}; border: 1px solid #cdd6f4; border-radius: 3px;")
        for w in (self.r_slider, self.g_slider, self.b_slider, self.v_slider, self.sp_r, self.sp_g, self.sp_b, self.hex_input):
            w.blockSignals(True)
        self.r_slider.setValue(col.red())
        self.g_slider.setValue(col.green())
        self.b_slider.setValue(col.blue())
        self.v_slider.setValue(col.value())
        self.sp_r.setValue(col.red())
        self.sp_g.setValue(col.green())
        self.sp_b.setValue(col.blue())
        self.hex_input.setText(col.name())
        for w in (self.r_slider, self.g_slider, self.b_slider, self.v_slider, self.sp_r, self.sp_g, self.sp_b, self.hex_input):
            w.blockSignals(False)
        self.wheel_val_slider.blockSignals(True)
        self.wheel_val_slider.setValue(col.value())
        self.wheel_val_slider.blockSignals(False)
        if col.hue() >= 0:
            self.wheel.set_hs(col.hue(), col.saturation())

    def _on_rgb_sliders_moved(self):
        self._set_color(QColor(self.r_slider.value(), self.g_slider.value(), self.b_slider.value()))

    def _on_spinbox_changed(self):
        self._set_color(QColor(self.sp_r.value(), self.sp_g.value(), self.sp_b.value()))

    def _on_wheel_picked(self, hue: int, sat: int):
        self._set_color(QColor.fromHsv(hue, sat, self.wheel_val_slider.value()))

    def _on_wheel_val_changed(self, val: int):
        self._set_color(QColor.fromHsv(self.wheel.hue, self.wheel.sat, val))

    def _on_hex_edited(self, txt: str):
        if re.match(r"^#[0-9a-fA-F]{6}$", txt.strip()):
            self._set_color(QColor(txt.strip()))

    def selected_hex(self) -> str:
        return self.current_color.name()


class KeybindsDialog(QDialog):
    def __init__(self, parent, keybinds: dict, labels: dict):
        super().__init__(parent)
        
        if parent and hasattr(parent, "user_cfg"):
            opacity = parent.user_cfg.get("win_opacity", 100) / 100.0
            self.setWindowOpacity(opacity)

        self.setWindowTitle("Customize Keybinds")
        self.resize(460, 480)
        self.keybinds = dict(keybinds)
        self.inputs = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        title = QLabel("Keyboard Shortcuts Configuration")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        table = QTableWidget(len(labels), 2)
        table.setHorizontalHeaderLabels(["Action", "Assigned Key"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(1, 140)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        for row, (action_key, clean_label) in enumerate(labels.items()):
            table.setItem(row, 0, QTableWidgetItem(clean_label))
            le = QLineEdit(self.keybinds.get(action_key, ""))
            le.setAlignment(Qt.AlignmentFlag.AlignCenter)
            le.setMaxLength(16)
            table.setCellWidget(row, 1, le)
            self.inputs[action_key] = le
        layout.addWidget(table, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_save = QPushButton("Save Keybinds")
        btn_cancel = QPushButton("Cancel")
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

    def get_updated_keybinds(self):
        return {act: le.text().strip() or self.keybinds[act] for act, le in self.inputs.items()}


class EditorSettingsDialog(QDialog):
    def __init__(self, main_win, presets):
        super().__init__(main_win)
        self.win = main_win
        
        if main_win and hasattr(main_win, "user_cfg"):
            opacity = main_win.user_cfg.get("win_opacity", 100) / 100.0
            self.setWindowOpacity(opacity)

        self.setWindowTitle("Editor & Theme Customization")
        self.resize(440, 360)

        screen_geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen_geo.center() - self.rect().center())

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        hdr = QLabel("Appearance & Layout Editor")
        hdr.setStyleSheet("font-size: 14px; font-weight: bold;")
        root.addWidget(hdr)

        sec1 = QFrame()
        sec1.setObjectName("boxFrame")
        s1_layout = QVBoxLayout(sec1)
        s1_layout.setContentsMargins(6, 6, 6, 6)

        row_preset = QHBoxLayout()
        row_preset.addWidget(QLabel("Theme:"))
        self.preset_combo = QComboBox()
        for name in presets:
            self.preset_combo.addItem(name)
        self.preset_combo.setCurrentText(self.win.user_cfg.get("current_theme", "Catppuccin Mocha"))
        self.preset_combo.currentTextChanged.connect(self.win._on_preset_selected)
        row_preset.addWidget(self.preset_combo, 1)
        btn_wp = QPushButton("Sync Wallpaper")
        btn_wp.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_wp.clicked.connect(self.win.apply_wallpaper_colors)
        row_preset.addWidget(btn_wp)
        s1_layout.addLayout(row_preset)

        row_colors = QHBoxLayout()
        for label_txt, col_key in (("Background", "bg"), ("Accent", "accent"), ("Secondary", "secondary"), ("Text", "text")):
            b = QPushButton(label_txt)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, k=col_key, l=label_txt: self._open_color_picker(k, l))
            row_colors.addWidget(b)
        s1_layout.addLayout(row_colors)
        root.addWidget(sec1)

        sec2 = QFrame()
        sec2.setObjectName("boxFrame")
        s2_layout = QGridLayout(sec2)
        s2_layout.setContentsMargins(6, 6, 6, 6)

        s2_layout.addWidget(QLabel("Window Opacity:"), 0, 0)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(40, 100)
        self.opacity_slider.setValue(int(self.win.user_cfg.get("win_opacity", 100)))
        self.opacity_slider.valueChanged.connect(self.win._on_opacity_changed)
        s2_layout.addWidget(self.opacity_slider, 0, 1)

        s2_layout.addWidget(QLabel("Progress Bar:"), 1, 0)
        self.bar_combo = QComboBox()
        for name in ("Pill Capsule", "Neon Line", "Block Retro", "Minimal Flat"):
            self.bar_combo.addItem(name)
        self.bar_combo.setCurrentText(self.win.user_cfg.get("progress_style", "Pill Capsule"))
        self.bar_combo.currentTextChanged.connect(self.win._on_progress_style_changed)
        s2_layout.addWidget(self.bar_combo, 1, 1)
        root.addWidget(sec2)

        sec3 = QFrame()
        sec3.setObjectName("boxFrame")
        s3_layout = QGridLayout(sec3)
        s3_layout.setContentsMargins(6, 6, 6, 6)

        btn_cov = QPushButton("Swap Cover")
        btn_cov.clicked.connect(self.win._toggle_cover_side)
        btn_cava = QPushButton("Swap CAVA")
        btn_cava.clicked.connect(self.win._toggle_cava_position)
        btn_hdr = QPushButton("Swap Header")
        btn_hdr.clicked.connect(self.win._swap_header_and_tabs)
        btn_bar = QPushButton("Move Bar")
        btn_bar.clicked.connect(self.win._toggle_bottom_bar_position)
        s3_layout.addWidget(btn_cov, 0, 0)
        s3_layout.addWidget(btn_cava, 0, 1)
        s3_layout.addWidget(btn_hdr, 1, 0)
        s3_layout.addWidget(btn_bar, 1, 1)
        root.addWidget(sec3)

        bot = QHBoxLayout()
        btn_keys = QPushButton("Keybinds...")
        btn_keys.clicked.connect(self.win._open_keybinds_dialog)
        bot.addWidget(btn_keys)
        bot.addStretch()
        btn_close = QPushButton("Done")
        btn_close.clicked.connect(self.accept)
        bot.addWidget(btn_close)
        root.addLayout(bot)

    def _open_color_picker(self, key: str, label: str):
        cur_hex = self.win.user_cfg["colors"].get(key, "#89b4fa")
        dlg = CustomColorDialog(self, cur_hex, f"Select {label} Color")
        
        # Force opacity inheritance right before executing
        opacity = self.win.user_cfg.get("win_opacity", 100) / 100.0
        dlg.setWindowOpacity(opacity)
        
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.win.user_cfg["colors"][key] = dlg.selected_hex()
            self.win._apply_dynamic_theme()
            self.win._save_user_customization()


class WelcomeKeybindsDialog(QDialog):
    def __init__(self, parent, keybinds: dict, labels: dict):
        super().__init__(parent)
        
        if parent and hasattr(parent, "user_cfg"):
            opacity = parent.user_cfg.get("win_opacity", 100) / 100.0
            self.setWindowOpacity(opacity)

        self.setWindowTitle("Welcome to jmpc")
        self.resize(460, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel("Quick Start & Keyboard Shortcuts")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        extra_rows = [
            ("Switch Tabs", "1 - 6"),
            ("Seek / Navigate Columns", "Left / Right"),
            ("Global Audio Seek", "Shift + Left / Right"),
        ]
        table = QTableWidget(len(labels) + len(extra_rows), 2)
        table.setHorizontalHeaderLabels(["Function", "Shortcut"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        row_idx = 0
        for fn, ks in extra_rows:
            table.setItem(row_idx, 0, QTableWidgetItem(fn))
            table.setItem(row_idx, 1, QTableWidgetItem(ks))
            row_idx += 1
        for act_key, clean_name in labels.items():
            table.setItem(row_idx, 0, QTableWidgetItem(clean_name))
            table.setItem(row_idx, 1, QTableWidgetItem(keybinds.get(act_key, "").upper()))
            row_idx += 1
        layout.addWidget(table, 1)

        bot_row = QHBoxLayout()
        self.chk_dont_show = QCheckBox("Do not show this message again")
        bot_row.addWidget(self.chk_dont_show)
        bot_row.addStack = bot_row.addStretch()
        btn_start = QPushButton("Get Started")
        btn_start.clicked.connect(self.accept)
        bot_row.addWidget(btn_start)
        layout.addLayout(bot_row)