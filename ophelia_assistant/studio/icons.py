"""Small hand-drawn line icons for the studio rail and toolbars."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


GRID = 20.0


def _g(value: float, size: float) -> float:
    return value * size / GRID


def _point(size: float, x: float, y: float) -> QPointF:
    return QPointF(_g(x, size), _g(y, size))


def _line(painter, size: float, x1: float, y1: float, x2: float, y2: float) -> None:
    painter.drawLine(_point(size, x1, y1), _point(size, x2, y2))


def _poly(painter, size: float, points, closed: bool = False) -> None:
    polygon = QPolygonF([_point(size, x, y) for x, y in points])
    if closed:
        painter.drawPolygon(polygon)
    else:
        painter.drawPolyline(polygon)


def _path(painter, size: float, points, closed: bool = False) -> None:
    path = QPainterPath()
    path.moveTo(_point(size, *points[0]))
    for point in points[1:]:
        path.lineTo(_point(size, *point))
    if closed:
        path.closeSubpath()
    painter.drawPath(path)


def _circle(painter, size: float, cx: float, cy: float, radius: float) -> None:
    painter.drawEllipse(
        QRectF(
            _g(cx - radius, size),
            _g(cy - radius, size),
            _g(radius * 2, size),
            _g(radius * 2, size),
        )
    )


def _dot(painter, color, size: float, cx: float, cy: float, radius: float) -> None:
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(
        QRectF(
            _g(cx - radius, size),
            _g(cy - radius, size),
            _g(radius * 2, size),
            _g(radius * 2, size),
        )
    )
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(color))


def _pen(color, width: float = 1.55):
    return QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def _draw_activity(p, s, color):
    _line(p, s, 4, 6, 16, 6)
    _line(p, s, 4, 10, 12, 10)
    _line(p, s, 4, 14, 8, 14)


def _draw_contacts(p, s, color):
    _circle(p, s, 10, 6.8, 2.6)
    path = QPainterPath()
    path.moveTo(_point(s, 4.6, 16.2))
    path.quadTo(_point(s, 10, 11.0), _point(s, 15.4, 16.2))
    p.drawPath(path)


def _draw_template(p, s, color):
    path = QPainterPath()
    path.moveTo(_point(s, 5.2, 3.4))
    path.lineTo(_point(s, 12.4, 3.4))
    path.lineTo(_point(s, 15.0, 6.0))
    path.lineTo(_point(s, 15.0, 16.6))
    path.lineTo(_point(s, 5.2, 16.6))
    path.closeSubpath()
    p.drawPath(path)
    _line(p, s, 12.4, 3.4, 12.4, 6.0)
    _line(p, s, 12.4, 6.0, 15.0, 6.0)
    _line(p, s, 7.6, 9.2, 12.6, 9.2)
    _line(p, s, 7.6, 12.0, 12.6, 12.0)
    _line(p, s, 7.6, 14.8, 10.6, 14.8)


def _draw_history(p, s, color):
    _circle(p, s, 10, 10, 6.2)
    _line(p, s, 10, 10, 10, 6.6)
    _line(p, s, 10, 10, 13.1, 11.3)


def _draw_settings(p, s, color):
    _line(p, s, 4.2, 6.0, 15.8, 6.0)
    _line(p, s, 4.2, 10.0, 15.8, 10.0)
    _line(p, s, 4.2, 14.0, 15.8, 14.0)
    _dot(p, color, s, 8.4, 6.0, 1.8)
    _dot(p, color, s, 12.8, 10.0, 1.8)
    _dot(p, color, s, 7.2, 14.0, 1.8)


def _draw_search(p, s, color):
    _circle(p, s, 9.0, 9.0, 5.0)
    _line(p, s, 12.9, 12.9, 16.2, 16.2)


def _draw_plus(p, s, color):
    _line(p, s, 10, 4.4, 10, 15.6)
    _line(p, s, 4.4, 10, 15.6, 10)


def _draw_trash(p, s, color):
    _line(p, s, 4.8, 6.0, 15.2, 6.0)
    _line(p, s, 8.6, 3.8, 11.4, 3.8)
    _line(p, s, 8.2, 6.0, 8.6, 3.8)
    _line(p, s, 11.8, 6.0, 11.4, 3.8)
    _path(p, s, [(6.2, 6.0), (6.5, 16.4), (13.5, 16.4), (13.8, 6.0)])
    _line(p, s, 8.7, 8.4, 8.8, 14.2)
    _line(p, s, 11.3, 8.4, 11.2, 14.2)


def _draw_refresh(p, s, color):
    arc = QRectF(_g(4.0, s), _g(4.0, s), _g(12.0, s), _g(12.0, s))
    p.drawArc(arc, -35 * 16, -250 * 16)
    _poly(p, s, [(14.6, 4.2), (16.2, 5.8), (15.0, 6.8)])
    _poly(p, s, [(5.6, 16.0), (4.2, 14.4), (5.4, 13.4)])


def _draw_play(p, s, color):
    _poly(p, s, [(7.2, 5.8), (7.2, 14.2), (14.2, 10.0)], closed=True)


def _draw_stop(p, s, color):
    _path(p, s, [(6.6, 6.6), (13.4, 6.6), (13.4, 13.4), (6.6, 13.4)], closed=True)


def _draw_copy(p, s, color):
    _path(p, s, [(5.4, 3.6), (12.2, 3.6), (12.2, 12.2), (5.4, 12.2)], closed=True)
    _path(p, s, [(8.0, 7.8), (14.8, 7.8), (14.8, 16.4), (8.0, 16.4)], closed=True)


def _draw_import(p, s, color):
    _line(p, s, 10, 4.2, 10, 12.8)
    _poly(p, s, [(6.8, 10.2), (10, 13.4), (13.2, 10.2)])
    _line(p, s, 4.8, 16.2, 15.2, 16.2)


def _draw_export(p, s, color):
    _line(p, s, 10, 12.8, 10, 4.2)
    _poly(p, s, [(6.8, 6.8), (10, 3.6), (13.2, 6.8)])
    _line(p, s, 4.8, 16.2, 15.2, 16.2)


def _draw_send(p, s, color):
    _path(p, s, [(4.0, 10.0), (16.2, 3.8), (12.2, 16.0), (9.6, 12.4), (4.0, 10.0)])


def _draw_mail(p, s, color):
    _path(p, s, [(3.6, 5.6), (3.6, 14.8), (16.4, 14.8), (16.4, 5.6)], closed=True)
    _path(p, s, [(3.6, 5.8), (10, 11.0), (16.4, 5.8)])


def _draw_mail_open(p, s, color):
    _path(p, s, [(3.4, 9.2), (10, 4.4), (16.6, 9.2), (16.6, 15.6), (3.4, 15.6)], closed=True)
    _path(p, s, [(3.4, 9.2), (10, 12.4), (16.6, 9.2)])


def _draw_window(p, s, color):
    _path(p, s, [(3.4, 4.6), (3.4, 13.6), (16.6, 13.6), (16.6, 4.6)], closed=True)
    _line(p, s, 10, 13.6, 10, 16.4)
    _line(p, s, 7.0, 16.4, 13.0, 16.4)


def _draw_chevron_right(p, s, color):
    _poly(p, s, [(8.0, 5.4), (13.2, 10.0), (8.0, 14.6)])


def _draw_chevron_left(p, s, color):
    _poly(p, s, [(12.0, 5.4), (6.8, 10.0), (12.0, 14.6)])


def _draw_check(p, s, color):
    _poly(p, s, [(4.6, 10.6), (8.4, 14.4), (15.4, 6.2)])


def _draw_alert(p, s, color):
    _path(p, s, [(10, 4.0), (16.6, 15.8), (3.4, 15.8)], closed=True)
    _line(p, s, 10, 8.4, 10, 12.2)
    _dot(p, color, s, 10, 13.8, 0.9)


def _draw_pencil(p, s, color):
    _line(p, s, 4.6, 15.4, 13.4, 6.6)
    _line(p, s, 11.8, 5.0, 15.0, 8.2)
    _line(p, s, 4.6, 15.4, 3.8, 16.2)


def _draw_more(p, s, color):
    _dot(p, color, s, 5.0, 10.0, 1.4)
    _dot(p, color, s, 10.0, 10.0, 1.4)
    _dot(p, color, s, 15.0, 10.0, 1.4)


def _draw_x(p, s, color):
    _line(p, s, 5.2, 5.2, 14.8, 14.8)
    _line(p, s, 14.8, 5.2, 5.2, 14.8)


def _draw_folder(p, s, color):
    _path(p, s, [(3.6, 6.0), (7.6, 6.0), (9.2, 8.0), (16.4, 8.0), (16.4, 15.8), (3.6, 15.8)], closed=True)
    _line(p, s, 3.6, 6.0, 3.6, 15.8)


def _draw_folder_plus(p, s, color):
    _draw_folder(p, s, color)
    _line(p, s, 10, 10.2, 10, 14.0)
    _line(p, s, 8.2, 12.1, 11.8, 12.1)


def _draw_database(p, s, color):
    _circle(p, s, 10, 6.2, 6.0)
    p.drawArc(QRectF(_g(4.0, s), _g(6.2, s), _g(12.0, s), _g(6.0, s)), 0, -180 * 16)
    p.drawArc(QRectF(_g(4.0, s), _g(11.0, s), _g(12.0, s), _g(6.0, s)), 0, 180 * 16)


def _draw_calendar(p, s, color):
    _path(p, s, [(4.0, 5.2), (4.0, 16.2), (16.0, 16.2), (16.0, 5.2)], closed=True)
    _line(p, s, 4.0, 8.8, 16.0, 8.8)
    _line(p, s, 7.0, 3.4, 7.0, 6.4)
    _line(p, s, 13.0, 3.4, 13.0, 6.4)


def _draw_eye(p, s, color):
    _path(p, s, [(3.4, 10.0), (10, 5.2), (16.6, 10.0), (10, 14.8)])
    _circle(p, s, 10, 10, 2.6)


def _draw_filter(p, s, color):
    _path(p, s, [(4.0, 5.0), (16.0, 5.0), (11.8, 10.8), (11.8, 15.2), (8.2, 16.8), (8.2, 10.8)])


def _draw_layout(p, s, color):
    _path(p, s, [(4.0, 4.0), (9.0, 4.0), (9.0, 16.0), (4.0, 16.0)], closed=True)
    _path(p, s, [(11.0, 4.0), (16.0, 4.0), (16.0, 9.0), (11.0, 9.0)], closed=True)
    _path(p, s, [(11.0, 11.0), (16.0, 11.0), (16.0, 16.0), (11.0, 16.0)], closed=True)


def _draw_undo(p, s, color):
    _path(p, s, [(15.2, 14.6), (15.2, 11.6), (8.8, 11.6), (5.6, 8.4), (8.8, 5.2), (15.2, 5.2)])
    _poly(p, s, [(15.2, 14.6), (13.0, 14.6), (15.2, 16.4)])


def _draw_info(p, s, color):
    _circle(p, s, 10, 10, 6.4)
    _dot(p, color, s, 10, 7.0, 1.0)
    _line(p, s, 10, 9.4, 10, 14.0)


def _draw_globe(p, s, color):
    _circle(p, s, 10, 10, 6.4)
    p.drawEllipse(QRectF(_g(4.6, s), _g(6.8, s), _g(10.8, s), _g(6.4, s)))
    _line(p, s, 3.6, 10, 16.4, 10)
    _line(p, s, 10, 3.6, 10, 16.4)


def _draw_user_plus(p, s, color):
    _draw_contacts(p, s, color)
    _line(p, s, 16.0, 7.0, 16.0, 12.6)
    _line(p, s, 13.2, 9.8, 18.8, 9.8)


def _draw_zap(p, s, color):
    _path(p, s, [(11.4, 3.4), (6.2, 11.0), (9.8, 11.0), (8.6, 16.6), (13.8, 9.0), (10.2, 9.0)])


def _draw_external(p, s, color):
    _path(p, s, [(6.2, 4.2), (15.8, 4.2), (15.8, 13.8), (6.2, 13.8)], closed=True)
    _path(p, s, [(9.0, 11.0), (9.0, 9.0), (11.0, 9.0)])
    _line(p, s, 9.0, 9.0, 13.0, 5.0)
    _line(p, s, 13.0, 5.0, 11.0, 5.0)
    _line(p, s, 13.0, 5.0, 13.0, 7.0)


def _draw_save(p, s, color):
    _path(p, s, [(4.2, 4.0), (4.2, 16.0), (15.8, 16.0), (15.8, 4.0)], closed=True)
    _path(p, s, [(6.6, 4.0), (6.6, 8.6), (13.4, 8.6), (13.4, 4.0)])
    _path(p, s, [(7.4, 12.2), (7.4, 16.0), (12.6, 16.0), (12.6, 12.2)])


_DRAWERS = {
    "activity": _draw_activity,
    "contacts": _draw_contacts,
    "template": _draw_template,
    "history": _draw_history,
    "settings": _draw_settings,
    "search": _draw_search,
    "plus": _draw_plus,
    "trash": _draw_trash,
    "refresh": _draw_refresh,
    "play": _draw_play,
    "stop": _draw_stop,
    "copy": _draw_copy,
    "import": _draw_import,
    "export": _draw_export,
    "send": _draw_send,
    "mail": _draw_mail,
    "mail_open": _draw_mail_open,
    "window": _draw_window,
    "chevron_right": _draw_chevron_right,
    "chevron_left": _draw_chevron_left,
    "check": _draw_check,
    "alert": _draw_alert,
    "pencil": _draw_pencil,
    "more": _draw_more,
    "x": _draw_x,
    "folder": _draw_folder,
    "folder_plus": _draw_folder_plus,
    "database": _draw_database,
    "calendar": _draw_calendar,
    "eye": _draw_eye,
    "filter": _draw_filter,
    "layout": _draw_layout,
    "undo": _draw_undo,
    "info": _draw_info,
    "globe": _draw_globe,
    "user_plus": _draw_user_plus,
    "zap": _draw_zap,
    "external": _draw_external,
    "save": _draw_save,
}


@lru_cache(maxsize=512)
def make_pixmap(name: str, color_hex: str, size: int = 18) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(_pen(color_hex))
    painter.setBrush(Qt.NoBrush)
    drawer = _DRAWERS.get(name)
    if drawer is not None:
        drawer(painter, float(size), color_hex)
    painter.end()
    return pixmap


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(make_pixmap(name, color, size))

