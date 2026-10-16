import math
import random
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QPen, QPolygonF, QBrush
from PyQt6.QtWidgets import QWidget, QPushButton


class CavaVisualizer(QWidget):
    MODES = [
        "Classic Bars", "Segmented LEDs", "Mirror Center",
        "Neon Wave", "Cyber Dots", "Pulse Blocks"
    ]

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(120)
        self.mode_idx = 0
        self.num_bars = 38
        self.values = [0.05] * self.num_bars
        self.peaks = [0.05] * self.num_bars
        self.phase = 0.0
        self.c1 = QColor("#eba0ac")
        self.c2 = QColor("#89b4fa")

        self.style_btn = QPushButton("Classic Bars ", self)
        self.style_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.style_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.style_btn.clicked.connect(self.next_mode)

    def set_gradient_colors(self, bottom_hex: str, top_hex: str):
        self.c1 = QColor(bottom_hex)
        self.c2 = QColor(top_hex)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.style_btn.adjustSize()
        self.style_btn.move(self.width() - self.style_btn.width() - 8, 6)

    def next_mode(self):
        self.mode_idx = (self.mode_idx + 1) % len(self.MODES)
        self.style_btn.setText(f"{self.MODES[self.mode_idx]} ")
        self.style_btn.adjustSize()
        self.style_btn.move(self.width() - self.style_btn.width() - 8, 6)
        self.update()

    def step(self, active: bool, position_ms: int = 0):
        self.phase += 0.35
        for i in range(self.num_bars):
            if active:
                wave = (math.sin(self.phase + i * 0.42) + 1.0) * 0.4
                bass = 1.35 if i < 10 else (1.0 if i < 25 else 0.8)
                jitter = random.uniform(0.1, 0.6) * bass
                target = min(0.95, max(0.08, (wave * 0.5) + (jitter * 0.5)))
                self.values[i] += (target - self.values[i]) * 0.55
                if self.values[i] > self.peaks[i]:
                    self.peaks[i] = self.values[i]
                else:
                    self.peaks[i] = max(0.05, self.peaks[i] - 0.02)
            else:
                self.values[i] = max(0.03, self.values[i] * 0.8)
                self.peaks[i] = max(0.03, self.peaks[i] * 0.85)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        grad = QLinearGradient(0, h, 0, 0)
        grad.setColorAt(0.0, self.c1)
        grad.setColorAt(1.0, self.c2)

        bar_space = w / self.num_bars
        bar_w = max(4.0, bar_space * 0.68)

        if self.mode_idx == 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            for i, val in enumerate(self.values):
                bh = max(4.0, val * (h - 16))
                x = i * bar_space + (bar_space - bar_w) / 2
                painter.drawRoundedRect(QRectF(x, h - bh - 4, bar_w, bh), 2.0, 2.0)
        elif self.mode_idx == 1:
            seg_h, gap = 5.0, 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            for i, val in enumerate(self.values):
                bh = max(4.0, val * (h - 18))
                x = i * bar_space + (bar_space - bar_w) / 2
                num_segs = int(bh // (seg_h + gap))
                painter.setBrush(grad)
                for s in range(num_segs):
                    painter.drawRect(QRectF(x, h - 6 - (s + 1) * (seg_h + gap), bar_w, seg_h))
                painter.setBrush(QColor("#f9e2af"))
                painter.drawRect(QRectF(x, h - 6 - self.peaks[i] * (h - 18), bar_w, 3.0))
        elif self.mode_idx == 2:
            mid_y = h / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            for i, val in enumerate(self.values):
                bh = max(4.0, val * (h - 20))
                x = i * bar_space + (bar_space - bar_w) / 2
                painter.drawRoundedRect(QRectF(x, mid_y - bh / 2.0, bar_w, bh), 2.0, 2.0)
        elif self.mode_idx == 3:
            poly = QPolygonF([QPointF(0, h)])
            for i, val in enumerate(self.values):
                poly.append(QPointF(i * bar_space + bar_space / 2.0, h - 6 - val * (h - 22)))
            poly.append(QPointF(w, h))
            c_fill = QColor(self.c2)
            c_fill.setAlpha(90)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c_fill)
            painter.drawPolygon(poly)
            painter.setPen(QPen(self.c2, 2.2))
            for i in range(len(self.values) - 1):
                painter.drawLine(
                    QPointF(i * bar_space + bar_space / 2.0, h - 6 - self.values[i] * (h - 22)),
                    QPointF((i + 1) * bar_space + bar_space / 2.0, h - 6 - self.values[i + 1] * (h - 22))
                )
        elif self.mode_idx == 4:
            painter.setPen(Qt.PenStyle.NoPen)
            for i, val in enumerate(self.values):
                x = i * bar_space + (bar_space - bar_w) / 2
                y = h - max(6.0, val * (h - 18)) - 6
                py = h - max(6.0, self.peaks[i] * (h - 18)) - 6
                painter.setBrush(self.c2)
                painter.drawEllipse(QRectF(x, y, bar_w, bar_w))
                painter.setBrush(self.c1)
                painter.drawEllipse(QRectF(x, py - 6, bar_w * 0.8, bar_w * 0.8))
        else:
            painter.setPen(QPen(self.c2, 1.2))
            c_fill = QColor(self.c1)
            c_fill.setAlpha(150)
            painter.setBrush(c_fill)
            step = 2
            for i in range(0, self.num_bars, step):
                val = self.values[i]
                bh = max(4.0, val * (h - 16))
                x = i * bar_space + 2
                bw = bar_space * step - 4
                painter.drawRect(QRectF(x, h - bh - 4, bw, bh))