"""v0.9 现代学术风主题：颜色常量、全局 QSS、程序化矢量图标。

设计语言（参考高校科研平台 / 文献数据库 UI）：
- 主色：天大蓝 #1A5CB0（主按钮 / 选中态 / 强调线），深藏青 #143D7B（标题）
- 中性：浅蓝灰背景 #F4F6FA，白卡片 + 描边 #DFE6F0，正文 #1F2D3D / 辅助 #8093B4
- 比例：中性色约 70%（背景/卡片/表格），主蓝约 15%（动作与强调），状态绿/警示红合计 <5%
- 图标由 QPainter 矢量绘制；校徽是唯一图片资源（ui/assets/tju_logo.png）
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from tju_info_retrieval.app_paths import resource_path

# 主色板（与 THEME_QSS 保持一致）
COLORS: dict[str, str] = {
    "primary": "#1A5CB0",        # 天大蓝：主按钮 / 选中 / 强调
    "primary_hover": "#1F66C4",
    "primary_pressed": "#14488F",
    "navy": "#143D7B",           # 标题深藏青
    "bg": "#F4F6FA",             # 窗口浅蓝灰底
    "card_border": "#DFE6F0",    # 卡片描边
    "text": "#1F2D3D",           # 正文
    "muted": "#8093B4",          # 辅助文字
    "green": "#2E8B57",          # 授权正常
    "red": "#C2413B",            # 失败 / 错误
}

FONT_FAMILY = '"Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif'

# 全局主题。按 objectName 精确作用，通用选择器只覆盖输入框/表格等标准控件。
THEME_QSS = """
* { font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif; }
QMainWindow { background-color: #f4f6fa; }
QWidget#central { background-color: #f4f6fa; }

QFrame#appHeader {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #fdfeff, stop:1 #eef3fa);
    border-bottom: 3px solid #1a5cb0;
}
QLabel#appTitle { font-size: 21px; font-weight: 700; color: #143d7b; }
QLabel#appSubtitle { font-size: 12px; color: #8093b4; letter-spacing: 1px; }

QFrame#sectionTab {
    background-color: #ffffff;
    border: 1px solid #dfe6f0;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QLabel#sectionTabText { font-size: 14px; font-weight: 700; color: #1a5cb0; }

QFrame#searchCard {
    background-color: #ffffff;
    border: 1px solid #dfe6f0;
    border-top: none;
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
}
QFrame#resultsCard {
    background-color: #ffffff;
    border: 1px solid #dfe6f0;
    border-radius: 10px;
}
QLabel#cardSectionTitle { font-size: 15px; font-weight: 700; color: #1f2d3d; }
QLabel#formLabel { font-size: 13px; color: #3d4a5c; }

QLineEdit {
    background-color: #ffffff;
    border: 1px solid #ccd6e4;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    color: #1f2d3d;
    selection-background-color: #1a5cb0;
}
QLineEdit:hover { border-color: #a9bad4; }
QLineEdit:focus { border: 1px solid #1a5cb0; }
QLineEdit:disabled { background-color: #f5f7fa; color: #9aa7b8; }

QComboBox {
    background-color: #ffffff;
    border: 1px solid #ccd6e4;
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 13px;
    color: #1f2d3d;
}
QComboBox:hover { border-color: #a9bad4; }
QComboBox:focus { border: 1px solid #1a5cb0; }
QComboBox:disabled { background-color: #f5f7fa; color: #9aa7b8; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #dfe6f0;
    selection-background-color: #e3edfb;
    selection-color: #143d7b;
    outline: none;
}

QCheckBox { font-size: 13px; color: #1f2d3d; spacing: 7px; }
QCheckBox::indicator {
    width: 17px; height: 17px;
    border: 1px solid #c4cddb;
    border-radius: 4px;
    background-color: #ffffff;
}
QCheckBox::indicator:hover { border-color: #1a5cb0; }
QCheckBox::indicator:checked { background-color: #1a5cb0; border-color: #1a5cb0; }
QCheckBox:disabled { color: #9aa7b8; }

QToolButton#langButton {
    background-color: #ffffff;
    border: 1px solid #c9d6ea;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    color: #1a5cb0;
}
QToolButton#langButton:hover { border-color: #1a5cb0; }
QToolButton#langButton:checked {
    background-color: #1a5cb0;
    border: 1px solid #14488f;
    color: #ffffff;
    font-weight: 700;
}
QToolButton#langButton:disabled { color: #9aa7b8; border-color: #e3e8f0; }
QToolButton#langButton:checked:disabled { color: #7d9cc4; border-color: #9db8dc; }

QPushButton#btnSearchPrimary {
    background-color: #1a5cb0;
    color: #ffffff;
    border: none;
    border-radius: 7px;
    padding: 10px 18px;
    font-size: 15px;
    font-weight: 700;
}
QPushButton#btnSearchPrimary:hover { background-color: #1f66c4; }
QPushButton#btnSearchPrimary:pressed { background-color: #14488f; }
QPushButton#btnSearchPrimary:disabled { background-color: #a9bed9; }

QPushButton#btnSecondary {
    background-color: #ffffff;
    color: #1a5cb0;
    border: 1px solid #c3d2e8;
    border-radius: 7px;
    padding: 10px 18px;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#btnSecondary:hover { border-color: #1a5cb0; background-color: #f3f7fd; }
QPushButton#btnSecondary:pressed { background-color: #e6eefb; }
QPushButton#btnSecondary:disabled {
    color: #9aa7b8; border-color: #e3e8f0; background-color: #f8fafc;
}

QPushButton#btnGhost {
    background-color: #ffffff;
    color: #33517e;
    border: 1px solid #d7dfeb;
    border-radius: 7px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton#btnGhost:hover { color: #1a5cb0; border-color: #1a5cb0; background-color: #f3f7fd; }
QPushButton#btnGhost:disabled { color: #9aa7b8; border-color: #e7ecf3; background-color: #fafbfd; }

QFrame#statusStrip {
    background-color: #f7f9fc;
    border: 1px solid #e3e9f2;
    border-radius: 7px;
}
QLabel#statusText { font-size: 13px; }
QLabel#emptyCaption { font-size: 13px; color: #9aa7b8; }

QTableWidget#resultTable {
    background-color: #ffffff;
    alternate-background-color: #f8fafd;
    border: none;
    gridline-color: #eef2f7;
    font-size: 13px;
    color: #2b3a4e;
    selection-background-color: #e3edfb;
    selection-color: #143d7b;
}
QHeaderView::section {
    background-color: #f1f4f9;
    color: #3d4a5c;
    border: none;
    border-bottom: 1px solid #dfe6f0;
    padding: 9px 8px;
    font-size: 13px;
    font-weight: 700;
}
QTableCornerButton::section { background-color: #f1f4f9; border: none; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c9d4e4; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #aebdd4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c9d4e4; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QFrame#footerBand {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0d3d8c, stop:1 #15529f);
    border: none;
    border-radius: 8px;
}
QLabel#footerMotto { color: #e8eefb; font-size: 15px; font-weight: 600; letter-spacing: 4px; }

QToolTip {
    background-color: #143d7b;
    color: #ffffff;
    border: none;
    padding: 6px 8px;
    font-size: 12px;
}
"""


# ---------------------------------------------------------------------------
# 程序化矢量图标（不引入图片资源）
# ---------------------------------------------------------------------------

def _g_search(p: QPainter) -> None:
    p.drawEllipse(QPointF(9, 9), 5.6, 5.6)
    p.drawLine(QPointF(13.4, 13.4), QPointF(17.6, 17.6))


def _g_shield(p: QPainter) -> None:
    path = QPainterPath(QPointF(10, 2.2))
    path.lineTo(16.4, 4.8)
    path.lineTo(16.4, 9.6)
    path.cubicTo(16.4, 13.6, 13.6, 16.2, 10, 17.8)
    path.cubicTo(6.4, 16.2, 3.6, 13.6, 3.6, 9.6)
    path.lineTo(3.6, 4.8)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(6.9, 9.7), QPointF(9.1, 11.9))
    p.drawLine(QPointF(9.1, 11.9), QPointF(13.2, 7.6))


def _g_external(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.2, 4.6, 10.6, 11.6), 2, 2)
    p.drawLine(QPointF(10.8, 9.2), QPointF(16.8, 3.6))
    p.drawLine(QPointF(13.2, 3.6), QPointF(16.8, 3.6))
    p.drawLine(QPointF(16.8, 3.6), QPointF(16.8, 7.2))


def _g_download(p: QPainter) -> None:
    p.drawLine(QPointF(10, 3), QPointF(10, 12.6))
    p.drawLine(QPointF(6.2, 9.2), QPointF(10, 13))
    p.drawLine(QPointF(10, 13), QPointF(13.8, 9.2))
    p.drawLine(QPointF(4, 16.6), QPointF(16, 16.6))


def _g_document(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(4.6, 2.6, 10.8, 14.8), 2, 2)
    p.drawLine(QPointF(7.4, 7.4), QPointF(12.6, 7.4))
    p.drawLine(QPointF(7.4, 10.2), QPointF(12.6, 10.2))
    p.drawLine(QPointF(7.4, 13), QPointF(10.8, 13))


def _g_database(p: QPainter) -> None:
    p.drawEllipse(QPointF(10, 4.9), 5.9, 2.3)
    p.drawLine(QPointF(4.1, 4.9), QPointF(4.1, 15.1))
    p.drawLine(QPointF(15.9, 4.9), QPointF(15.9, 15.1))
    p.drawArc(QRectF(4.1, 10.4, 11.8, 4.6), 0, -180 * 16)
    p.drawArc(QRectF(4.1, 12.8, 11.8, 4.6), 0, -180 * 16)


def _g_info(p: QPainter) -> None:
    p.drawEllipse(QPointF(10, 10), 7.5, 7.5)
    pen_color = p.pen().color()
    p.save()
    p.setBrush(QColor(pen_color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(10, 6.4), 1.0, 1.0)
    p.restore()
    p.drawLine(QPointF(10, 9.6), QPointF(10, 14))


def _g_settings(p: QPainter) -> None:
    """齿轮（设置）：外圈齿 + 内孔。"""
    pen_color = p.pen().color()
    p.save()
    p.setBrush(QColor(pen_color))
    p.setPen(Qt.PenStyle.NoPen)
    # 8 个外齿
    for angle in range(0, 360, 45):
        import math

        rad = math.radians(angle)
        cx = 10 + 7.0 * math.cos(rad)
        cy = 10 + 7.0 * math.sin(rad)
        p.drawEllipse(QPointF(cx, cy), 2.3, 2.3)
    p.restore()
    # 外圈 + 内孔
    p.drawEllipse(QPointF(10, 10), 5.2, 5.2)
    pen_color = p.pen().color()
    p.save()
    p.setBrush(QColor(pen_color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(10, 10), 1.9, 1.9)
    p.restore()


_GLYPHS = {
    "search": _g_search,
    "shield": _g_shield,
    "external": _g_external,
    "download": _g_download,
    "document": _g_document,
    "database": _g_database,
    "info": _g_info,
    "settings": _g_settings,
}


def pixmap_painted(name: str, color: str | None = None, size: int = 20) -> QPixmap:
    """以 20×20 视窗绘制线性图标，返回高清 QPixmap（2x devicePixelRatio）。"""
    color = color or COLORS["primary"]
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.scale(size / 20.0, size / 20.0)
    _GLYPHS.get(name, _g_search)(p)
    p.end()
    return pm


def paint_icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    icon = QIcon()
    icon.addPixmap(pixmap_painted(name, color, size))
    return icon


_EMBLEM_CACHE: dict[str, QPixmap] = {}


def _emblem_source() -> QPixmap:
    """读取 ui/assets/tju_logo.png（参考图提取的真实校徽）；缺失返回空图。"""
    if "logo" not in _EMBLEM_CACHE:
        path = resource_path("tju_info_retrieval", "ui", "assets", "tju_logo.png")
        if path.exists():
            _EMBLEM_CACHE["logo"] = QPixmap(str(path))
    return _EMBLEM_CACHE.get("logo", QPixmap())


def _tinted_emblem(src: QPixmap, color: str) -> QPixmap:
    """由同一校徽资源生成单色变体：按暗度映射透明度后统一染色。

    页脚深蓝底上以浅色线章呈现（与参考图页脚一致），来源仍是同一资源。
    """
    key = f"tint:{color}"
    if key in _EMBLEM_CACHE:
        return _EMBLEM_CACHE[key]
    img = src.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    tint = QColor(color)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            a = c.alpha() * int((255 - lum) * 1.45) // 255
            c.setAlpha(min(255, a))
            c.setRed(tint.red())
            c.setGreen(tint.green())
            c.setBlue(tint.blue())
            img.setPixelColor(x, y, c)
    pm = QPixmap.fromImage(img)
    _EMBLEM_CACHE[key] = pm
    return pm


def _fallback_emblem(size: int = 48, ring: str | None = None) -> QPixmap:
    """资源缺失时的占位章：双环 + “TJU / 1895”。"""
    ring = ring or COLORS["primary"]
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(ring)
    p.setPen(QPen(color, max(1.6, size * 0.055)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    center = QPointF(size / 2, size / 2)
    p.drawEllipse(center, size * 0.46, size * 0.46)
    p.setPen(QPen(color, max(1.0, size * 0.022)))
    p.drawEllipse(center, size * 0.36, size * 0.36)
    f1 = QFont("Georgia", max(7, int(size * 0.21)))
    f1.setBold(True)
    p.setFont(f1)
    p.setPen(QPen(color))
    p.drawText(QRectF(0, size * 0.24, size, size * 0.3), Qt.AlignmentFlag.AlignCenter, "TJU")
    f2 = QFont("Georgia", max(5, int(size * 0.13)))
    p.setFont(f2)
    p.drawText(QRectF(0, size * 0.52, size, size * 0.2), Qt.AlignmentFlag.AlignCenter, "1895")
    p.end()
    return pm


def emblem_pixmap(size: int = 48, ring: str | None = None) -> QPixmap:
    """校徽：Header/窗口图标用原色资源，页脚小章用同一资源的浅色变体。"""
    src = _emblem_source()
    if src.isNull():
        return _fallback_emblem(size, ring)
    variant = _tinted_emblem(src, ring) if ring else src
    pm = variant.scaled(
        size * 2,
        size * 2,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pm.setDevicePixelRatio(2.0)
    return pm


def empty_state_pixmap(size: int = 120) -> QPixmap:
    """结果表空态插画：简约收纳盒 + 虚线。"""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    stroke = QColor("#c7d2e3")
    fill_front = QColor("#f6f9fd")
    fill_back = QColor("#edf2f9")
    s = size / 120.0
    p.scale(s, s)

    pen = QPen(stroke, 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)

    p.setBrush(QColor(fill_back))
    p.drawRoundedRect(QRectF(30, 38, 60, 28), 4, 4)
    p.setBrush(QColor(fill_front))
    poly_points = [
        QPointF(20, 58), QPointF(38, 74), QPointF(82, 74), QPointF(100, 58),
        QPointF(100, 98), QPointF(20, 98),
    ]
    path = QPainterPath(QPointF(20, 58))
    for pt in poly_points[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(46, 66, 28, 9), 3, 3)

    dash = QPen(stroke, 1.8, Qt.PenStyle.DashLine)
    dash.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(dash)
    p.drawArc(QRectF(66, 8, 34, 30), 20 * 16, 140 * 16)
    p.setPen(QPen(stroke, 1.8))
    p.setBrush(QColor(stroke))
    p.drawEllipse(QPointF(64, 14), 2.4, 2.4)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#dfe7f3"))
    p.drawEllipse(QPointF(14, 32), 3.2, 3.2)
    p.drawEllipse(QPointF(108, 50), 2.6, 2.6)
    p.end()
    return pm
