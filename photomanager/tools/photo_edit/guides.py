"""構圖格線（對應網頁版 js/tools/photo-edit/guides.js）。

每一種格線就是一個函式：拿到裁切框在畫面上的實際寬高，回傳 [(QPainterPath, faint)]。
直接用畫面像素算 —— 線條粗細才不會跟著裁切框比例被拉扁。
variant 是「方向」：螺旋與三角構圖有四個角可以起頭，按一下換一個。
"""
from __future__ import annotations


from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainterPath, QTransform

GUIDES = [("none", "無"), ("thirds", "九宮格"), ("golden", "黃金比例"), ("spiral", "黃金螺旋"),
          ("triangles", "黃金三角"), ("diagonal", "對角線法"), ("grid", "細格線"), ("center", "中心十字")]
DIRECTIONAL = {"golden", "spiral", "triangles", "diagonal"}


def _line(path, x1, y1, x2, y2):
    path.moveTo(x1, y1)
    path.lineTo(x2, y2)


def thirds(w, h, _v):
    p = QPainterPath()
    for t in (1 / 3, 2 / 3):
        _line(p, w * t, 0, w * t, h)
        _line(p, 0, h * t, w, h * t)
    return [(p, False)]


def subdivide(w, h, steps=12):
    """一路從矩形上切下最大的正方形，記下每一刀。"""
    r = (0.0, 0.0, float(w), float(h))
    cuts = []
    for i in range(steps):
        x, y, rw, rh = r
        s = min(rw, rh)
        if s < 1.5:
            break
        d = i % 4
        if d == 0:
            sq, rest = (x, y, s), (x + s, y, rw - s, rh)
        elif d == 1:
            sq, rest = (x, y, s), (x, y + s, rw, rh - s)
        elif d == 2:
            sq, rest = (x + rw - s, y, s), (x, y, rw - s, rh)
        else:
            sq, rest = (x, y + rh - s, s), (x, y, rw, rh - s)
        cuts.append((d, sq, r))
        r = rest
    return cuts


def cut_lines(cuts):
    p = QPainterPath()
    for d, (sx, sy, s), (rx, ry, rw, rh) in cuts:
        if d == 0:
            _line(p, sx + s, ry, sx + s, ry + rh)
        elif d == 1:
            _line(p, rx, sy + s, rx + rw, sy + s)
        elif d == 2:
            _line(p, sx, ry, sx, ry + rh)
        else:
            _line(p, rx, sy, rx + rw, sy)
    return p


def oriented(paths, w, h, variant):
    """依方向把整組圖形鏡射過去，不必重算。"""
    sx = -1 if variant in (1, 2) else 1
    sy = -1 if variant in (2, 3) else 1
    t = QTransform().translate(w if sx < 0 else 0, h if sy < 0 else 0).scale(sx, sy)
    return [(t.map(p), faint) for p, faint in paths]


def golden(w, h, v):
    return oriented([(cut_lines(subdivide(w, h)), False)], w, h, v)


def grid(w, h, _v):
    p = QPainterPath()
    cols = 6
    rows = max(3, round(cols * h / w))
    for i in range(1, cols):
        _line(p, w * i / cols, 0, w * i / cols, h)
    for i in range(1, rows):
        _line(p, 0, h * i / rows, w, h * i / rows)
    return [(p, True)]


def center(w, h, _v):
    p = QPainterPath()
    _line(p, w / 2, 0, w / 2, h)
    _line(p, 0, h / 2, w, h / 2)
    return [(p, False)]


def diagonal(w, h, _v):
    d = max(w, h)
    p = QPainterPath()
    _line(p, 0, 0, d, d)
    _line(p, w, 0, w - d, d)
    _line(p, 0, h, d, h - d)
    _line(p, w, h, w - d, h - d)
    return [(p, False)]


def triangles(w, h, v):
    flip = v % 2 == 1
    ax, ay, bx, by = (w, 0, 0, h) if flip else (0, 0, w, h)
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    corners = [(0, 0), (w, h)] if flip else [(w, 0), (0, h)]
    p = QPainterPath()
    _line(p, ax, ay, bx, by)
    for cx, cy in corners:
        t = ((cx - ax) * dx + (cy - ay) * dy) / len2
        _line(p, cx, cy, ax + dx * t, ay + dy * t)
    return [(p, False)]


def spiral(w, h, v):
    """每個正方形裡畫一段四分之一圓弧；同一個順時針方向，相鄰圓弧切線連續。"""
    cuts = subdivide(w, h)
    path = QPainterPath()
    first = True
    for d, (sx, sy, s), _ in cuts:
        # 圓心與起訖角（Qt 的角度：0° 在右、逆時針為正；這裡用負的掃掠角走順時針）
        if d == 0:
            rect, start = QRectF(sx, sy, 2 * s, 2 * s), 180      # 圓心右下: 左下 → 右上
        elif d == 1:
            rect, start = QRectF(sx - s, sy, 2 * s, 2 * s), 90   # 圓心左下: 左上 → 右下
        elif d == 2:
            rect, start = QRectF(sx - s, sy - s, 2 * s, 2 * s), 0  # 圓心左上: 右上 → 左下
        else:
            rect, start = QRectF(sx, sy - s, 2 * s, 2 * s), 270  # 圓心右上: 右下 → 左上
        if first:
            path.arcMoveTo(rect, start)
            first = False
        path.arcTo(rect, start, -90)
    return oriented([(cut_lines(cuts), True), (path, False)], w, h, v)


RENDERERS = {"thirds": thirds, "golden": golden, "grid": grid, "center": center,
             "diagonal": diagonal, "triangles": triangles, "spiral": spiral}


def guide_paths(kind, w, h, variant=0):
    fn = RENDERERS.get(kind)
    if not fn or w < 4 or h < 4:
        return []
    return fn(w, h, variant)
