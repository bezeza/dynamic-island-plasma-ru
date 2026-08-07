"""Turning whatever an app sends us into a pixmap the island can show.

The notification spec offers three competing ways to supply artwork:

* the ``app_icon`` argument (an icon name, an absolute path or a file URI),
* the ``image-data`` hint (a raw RGB(A) bitmap inlined into the D-Bus call),
* the ``image-path`` hint (again a name, path or URI).

They are resolved in the order image-data > image-path > app_icon > desktop
entry icon, which matches what other notification servers do.
"""

from __future__ import annotations

import configparser
import os
from typing import Any

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtQuick import QQuickImageProvider

from . import apps

_desktop_icon_cache: dict[str, str] = {}

# An icon arrives as raw pixels over the bus, and the sender decides the
# dimensions. Nothing legitimate is anywhere near this large; the cap is what
# stops a bad number from asking for a gigabyte before anything is validated.
_MAX_IMAGE_EDGE = 8192


def _icon_from_desktop_entry(entry: str) -> str:
    """Look up ``Icon=`` in a .desktop file. Returns "" when not found.

    Which file that is, is :mod:`dynamicisland.apps`' problem — and it is a
    real one: applications name themselves "Firefox" or "DNF App Center" far
    more often than they name their desktop entry, and an exact file-name match
    would leave all of those without an icon.
    """
    if not entry:
        return ""
    if entry in _desktop_icon_cache:
        return _desktop_icon_cache[entry]

    result = ""
    path = apps.desktop_file(entry)
    if path is not None:
        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
            result = parser.get("Desktop Entry", "Icon", fallback="")
        except (configparser.Error, OSError, UnicodeDecodeError):
            result = ""
    _desktop_icon_cache[entry] = result
    return result


def _image_from_source(source: str, size: int) -> QImage:
    """Load an icon name / path / file URI into a QImage."""
    if not source:
        return QImage()

    path = source
    if source.startswith("file://"):
        path = QUrl(source).toLocalFile()

    if os.path.isabs(path) and os.path.isfile(path):
        image = QImage(path)
        if not image.isNull():
            return image

    icon = QIcon.fromTheme(source)
    if icon.isNull() and os.path.basename(source) != source:
        icon = QIcon.fromTheme(os.path.basename(source))
    if not icon.isNull():
        pixmap = icon.pixmap(QSize(size, size))
        if not pixmap.isNull():
            return pixmap.toImage()
    return QImage()


def decode_image_data(value: Any) -> QImage:
    """Decode the ``image-data`` hint: (w, h, rowstride, alpha, bps, ch, bytes)."""
    try:
        width, height, rowstride, has_alpha, bits, channels, data = value
    except (TypeError, ValueError):
        return QImage()

    try:
        width, height, rowstride = int(width), int(height), int(rowstride)
        bits, channels = int(bits), int(channels)
    except (TypeError, ValueError):
        return QImage()

    if bits != 8 or channels not in (3, 4) or width <= 0 or height <= 0:
        return QImage()
    if width > _MAX_IMAGE_EDGE or height > _MAX_IMAGE_EDGE:
        return QImage()
    if isinstance(data, (list, tuple)):
        data = bytes(bytearray(data))
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return QImage()

    # Qt silently ignores a bytesPerLine that is not positive and reads the
    # natural stride instead, so validating the sender's number would be
    # checking something Qt is not going to use — and a rowstride of 0 would
    # shrink the length check to a single row while Qt reads the whole image
    # off the end of the buffer. Decide the stride here, then check against it.
    if rowstride <= 0:
        rowstride = width * channels
    if rowstride < width * channels:
        return QImage()
    if len(data) < rowstride * (height - 1) + width * channels:
        return QImage()

    fmt = QImage.Format_RGBA8888 if channels == 4 else QImage.Format_RGB888
    # Keep a reference to the buffer: QImage does not copy it.
    buffer = bytes(data)
    image = QImage(buffer, width, height, rowstride, fmt)
    if image.isNull():
        return QImage()
    target = QImage.Format_ARGB32 if has_alpha or channels == 4 else QImage.Format_RGB32
    return image.convertToFormat(target).copy()


def resolve(hints: dict[str, Any], app_icon: str, desktop_entry: str,
            size: int = 96, app_name: str = "") -> QImage:
    """Pick the best available artwork for one notification."""
    for key in ("image-data", "image_data", "icon_data"):
        if key in hints:
            image = decode_image_data(hints[key])
            if not image.isNull():
                return image

    for key in ("image-path", "image_path"):
        source = hints.get(key)
        if isinstance(source, str):
            image = _image_from_source(source, size)
            if not image.isNull():
                return image

    image = _image_from_source(app_icon, size)
    if not image.isNull():
        return image

    image = _image_from_source(_icon_from_desktop_entry(desktop_entry), size)
    if not image.isNull():
        return image

    # Nothing usable so far, and plenty of applications send neither an icon
    # nor a desktop entry — but they all say what they are called.
    if app_name:
        image = _image_from_source(_icon_from_desktop_entry(app_name), size)
        if not image.isNull():
            return image
        image = _image_from_source(app_name.lower().replace(" ", "-"), size)
        if not image.isNull():
            return image

    return _image_from_source("dialog-information", size)


def accent_color(image: QImage, fallback: str = "#0A84FF") -> str:
    """Average the saturated pixels of *image* into one glow colour.

    Grey and transparent pixels are ignored so that a mostly-white logo still
    yields its brand colour instead of washing out to grey.  The result is
    pushed to a fixed saturation/lightness so it always reads on pure black.
    """
    if image.isNull():
        return fallback

    thumb = image.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    thumb = thumb.convertToFormat(QImage.Format_ARGB32)

    r_sum = g_sum = b_sum = 0.0
    weight_sum = 0.0
    for y in range(thumb.height()):
        for x in range(thumb.width()):
            color = QColor.fromRgba(thumb.pixel(x, y))
            if color.alpha() < 96:
                continue
            saturation = color.saturationF()
            value = color.valueF()
            if saturation < 0.18 or value < 0.12:
                continue
            weight = saturation * value
            r_sum += color.redF() * weight
            g_sum += color.greenF() * weight
            b_sum += color.blueF() * weight
            weight_sum += weight

    if weight_sum <= 0.0:
        return fallback

    color = QColor.fromRgbF(
        min(1.0, r_sum / weight_sum),
        min(1.0, g_sum / weight_sum),
        min(1.0, b_sum / weight_sum),
    )
    hue = color.hueF()
    if hue < 0:  # achromatic
        return fallback
    color.setHsvF(hue, max(0.55, color.saturationF()), 1.0)
    return color.name()


class ArtworkProvider(QQuickImageProvider):
    """Serves ``image://island/<notification-id>`` to QML."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Pixmap)
        self._images: dict[int, QImage] = {}

    def put(self, notification_id: int, image: QImage) -> None:
        self._images[notification_id] = image

    def get(self, notification_id: int) -> QImage | None:
        return self._images.get(notification_id)

    def drop(self, notification_id: int) -> None:
        self._images.pop(notification_id, None)

    # PySide6 expects the pixmap alone here, not the (pixmap, size) pair the
    # C++ signature suggests — QML reads the size off the returned pixmap.
    def requestPixmap(self, image_id: str, size: QSize, requested: QSize) -> QPixmap:
        key = image_id.split("?", 1)[0]
        try:
            image = self._images.get(int(key), QImage())
        except ValueError:
            image = QImage()
        if image.isNull():
            return QPixmap()

        pixmap = QPixmap.fromImage(image)
        if requested.width() > 0 and requested.height() > 0:
            pixmap = pixmap.scaled(
                requested, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        return pixmap


class ThemeIconProvider(QQuickImageProvider):
    """Serves ``image://icon/<theme-icon-name>`` to QML."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Pixmap)

    def requestPixmap(self, image_id: str, size: QSize, requested: QSize) -> QPixmap:
        edge = max(requested.width(), requested.height(), 16)
        icon = QIcon.fromTheme(image_id)
        return QPixmap() if icon.isNull() else icon.pixmap(QSize(edge, edge))
