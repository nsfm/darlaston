"""The slide map: everywhere you have already looked, and a way back.

Built passively from the preview stream while hunting -- no captures, no
button. Thumbnails accumulate at their tracked positions and the slide charts
itself. Pins mark points of interest; since nothing here can move the stage,
selecting a pin gives a live distance-and-direction readout that counts down
as the operator cranks toward it, which is the correct interface to a manual
stage.

This widget is the seed of the mosaic minimap: same canvas, same world space.
When mosaic mode arrives, captured tiles paint over the reconnaissance layer.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..capture.mosaic import overlap_fraction
from ..live.pipeline import LiveSignals
from ..live.tracker import SlideMap
from ..i18n import _, n_
from . import theme

#: Fields-to-widget conversion never zooms a single field bigger than this
#: fraction of the canvas, so an unexplored map is not one giant blurry thumb.
_MAX_FIELD_FRACTION = 0.55
#: Click tolerance for selecting a pin, in widget pixels.
_PIN_GRAB_PX = 12.0


class _Canvas(QtWidgets.QWidget):
    """World-space paint surface. Fit-all, aspect preserved."""

    pin_clicked = QtCore.Signal(int)

    def __init__(self, model: SlideMap) -> None:
        super().__init__()
        self._model = model
        self._pos: tuple[float, float] | None = None
        self._frame: tuple[int, int] = (0, 0)
        self._tracking = False
        self.setMinimumHeight(120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def set_state(self, pos, frame_size, tracking: bool,
                  changed: bool) -> None:
        moved = (pos is not None and self._pos is not None
                 and (abs(pos[0] - self._pos[0]) > 0.5
                      or abs(pos[1] - self._pos[1]) > 0.5))
        went = (pos is None) != (self._pos is None)
        state_flip = tracking != self._tracking
        self._pos, self._frame, self._tracking = pos, frame_size, tracking
        # Repaint only when something is visibly different; this runs at
        # frame rate and a parked view should cost nothing.
        if changed or moved or went or state_flip:
            self.update()

    # ---- transform -------------------------------------------------------

    def _fit(self) -> tuple[float, float, float] | None:
        """Returns (scale, ox, oy) mapping world -> widget, or None."""
        extra = None
        if self._pos is not None and self._frame[0] > 0:
            extra = (self._pos, self._frame)
        b = self._model.bounds(extra)
        if b is None:
            return None
        x0, y0, x1, y1 = b
        bw, bh = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        cw, ch = self.width() - 16, self.height() - 16
        if cw <= 0 or ch <= 0:
            return None
        scale = min(cw / bw, ch / bh)
        if self._frame[0] > 0:
            scale = min(scale, _MAX_FIELD_FRACTION * cw / self._frame[0])
        ox = (self.width() - bw * scale) / 2 - x0 * scale
        oy = (self.height() - bh * scale) / 2 - y0 * scale
        return scale, ox, oy

    def _to_widget(self, fit, wx: float, wy: float) -> QtCore.QPointF:
        s, ox, oy = fit
        return QtCore.QPointF(wx * s + ox, wy * s + oy)

    def _to_world(self, fit, px: float, py: float) -> tuple[float, float]:
        s, ox, oy = fit
        return (px - ox) / s, (py - oy) / s

    # ---- interaction -----------------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        fit = self._fit()
        if fit is None:
            return
        wx, wy = self._to_world(fit, event.position().x(),
                                event.position().y())
        pin = self._model.pin_near((wx, wy), _PIN_GRAB_PX / fit[0])
        if pin is not None:
            self.pin_clicked.emit(pin.id)

    # ---- paint -----------------------------------------------------------

    def paintEvent(self, event) -> None:
        import time as _t

        from .widgets import UI_METER
        _m = _t.perf_counter()
        try:
            self._paint(event)
        finally:
            UI_METER.since("slide map paint", _m)

    def _paint(self, event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(theme.SUNK))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.LINE)))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        fit = self._fit()
        if fit is None:
            p.setPen(QtGui.QColor(theme.DIM))
            p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                       "move the stage to start mapping")
            p.end()
            return

        s, _, _ = fit
        # Terrain: the whole explored slide is one picture now, blitted
        # in one call. The alpha channel is the validity mask, so the
        # unexplored void simply is not drawn -- and the paint cost no
        # longer grows with how much slide has been seen.
        ground = self._model.terrain
        if ground.ready:
            gh, gw = ground.rgba.shape[:2]
            img = QtGui.QImage(ground.rgba.data, gw, gh,
                               ground.rgba.strides[0],
                               QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            tl = self._to_widget(fit, ground.org[0], ground.org[1])
            extent = ground.scale * s
            p.drawImage(QtCore.QRectF(tl.x(), tl.y(),
                                      gw * extent, gh * extent), img)

        # Mosaic tiles over the reconnaissance layer: same terrain, but
        # outlined -- these are the frames that exist on disk.
        for i, tile in enumerate(self._model.tiles):
            th, tw = tile.thumb.shape[:2]
            img = QtGui.QImage(tile.thumb.data, tw, th, tile.thumb.strides[0],
                               QtGui.QImage.Format.Format_BGR888)
            w, h = tile.size
            tl = self._to_widget(fit, tile.pos[0] - w / 2, tile.pos[1] - h / 2)
            rect = QtCore.QRectF(tl.x(), tl.y(), w * s, h * s)
            p.drawImage(rect, img)
            last = i == len(self._model.tiles) - 1
            if tile.state == "merging":
                # A hatched veil: this tile's stack is still merging in
                # the background. The veil lifts when it lands.
                p.setPen(QtCore.Qt.PenStyle.NoPen)
                veil = QtGui.QColor(theme.BRASS)
                veil.setAlpha(70)
                p.setBrush(QtGui.QBrush(veil,
                                        QtCore.Qt.BrushStyle.BDiagPattern))
                p.drawRect(rect)
            if tile.state == "failed":
                colour = QtGui.QColor(200, 60, 50)
            else:
                colour = QtGui.QColor(theme.BRASS)
                if not last:
                    colour.setAlpha(140)
            p.setPen(QtGui.QPen(colour, 1.4 if last or tile.state == "failed"
                                else 1.0))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
            if tile.label:
                f = p.font()
                f.setPointSizeF(7.0)
                p.setFont(f)
                p.drawText(rect.adjusted(0, 0, -2, -1),
                           QtCore.Qt.AlignmentFlag.AlignRight
                           | QtCore.Qt.AlignmentFlag.AlignBottom, tile.label)

        # Guidance line under the pins, so the target stays legible.
        target = next((q for q in self._model.pins
                       if q.id == self._model.target), None)
        if target is not None and self._pos is not None:
            pen = QtGui.QPen(QtGui.QColor(theme.BRASS))
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(self._to_widget(fit, *self._pos),
                       self._to_widget(fit, *target.pos))

        for pin in self._model.pins:
            at = self._to_widget(fit, *pin.pos)
            selected = pin.id == self._model.target
            colour = QtGui.QColor(theme.BRASS if selected else theme.INK)
            p.setPen(QtGui.QPen(colour, 1.5))
            p.setBrush(QtGui.QColor(theme.BRASS) if selected
                       else QtCore.Qt.BrushStyle.NoBrush)
            p.drawEllipse(at, 4.0, 4.0)
            p.setPen(colour)
            p.drawText(QtCore.QPointF(at.x() + 7, at.y() + 4), str(pin.id))

        # The current view, drawn last: it is the one thing that must always
        # be findable at a glance.
        if self._pos is not None and self._frame[0] > 0:
            w, h = self._frame
            colour = QtGui.QColor(theme.BRASS if self._tracking
                                  else theme.DIM)
            p.setPen(QtGui.QPen(colour, 1.0))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            tl = self._to_widget(fit, self._pos[0] - w / 2,
                                 self._pos[1] - h / 2)
            p.drawRect(QtCore.QRectF(tl.x(), tl.y(), w * s, h * s))
        p.end()


class SlideMapPanel(QtWidgets.QWidget):
    """The map, its controls, and a one-line status.

    Lives as an overlay in a corner of the live view, not in the rail: a map
    is only meaningful in reference to the image it maps, and the rail's 258
    pixels squashed it into a postage stamp. The container paints itself
    mostly-opaque so the terrain stays legible over any illumination mode.
    """

    #: The tracker's origin lives in the pipeline; clearing the map must
    #: clear that too, and only the main window can reach both.
    reset_requested = QtCore.Signal()
    #: The operator asked to start or stop a mosaic. The main window owns the
    #: session; it confirms by calling set_mosaic, so a failed start cannot
    #: leave the button lying.
    mosaic_requested = QtCore.Signal(bool)
    undo_tile = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.model = SlideMap()
        self.canvas = _Canvas(self.model)
        self.canvas.pin_clicked.connect(self._on_pin_clicked)
        self._mosaic_on = False
        #: Frames stamped with an older tracking generation than this are
        #: refused: they were measured before the map's last clear.
        self._min_gen = 0

        self.pin_btn = QtWidgets.QPushButton(_("map.pin.action"))
        self.pin_btn.setProperty("role", "seg")
        self.pin_btn.setToolTip(_("map.pin.tooltip"))
        self.pin_btn.clicked.connect(self._on_pin)
        self.pin_btn.setEnabled(False)

        self.clear_btn = QtWidgets.QPushButton(_("map.clear.action"))
        self.clear_btn.setProperty("role", "seg")
        self.clear_btn.setToolTip(_("map.clear.tooltip"))
        self.clear_btn.clicked.connect(self.clear)

        self.mosaic_btn = QtWidgets.QPushButton(_("map.mosaic.action"))
        self.mosaic_btn.setCheckable(True)
        self.mosaic_btn.setProperty("role", "seg")
        self.mosaic_btn.setToolTip(_("map.mosaic.tooltip"))
        self.mosaic_btn.clicked.connect(
            lambda on: self.mosaic_requested.emit(bool(on)))

        self.undo_btn = QtWidgets.QPushButton(_("map.undo.action"))
        self.undo_btn.setProperty("role", "seg")
        self.undo_btn.setToolTip(_("map.undo.tooltip"))
        self.undo_btn.clicked.connect(self.undo_tile)
        self.undo_btn.hide()

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("role", "key")
        self.status.setWordWrap(True)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.mosaic_btn)
        row.addWidget(self.undo_btn)
        row.addStretch(1)
        row.addWidget(self.pin_btn)
        row.addWidget(self.clear_btn)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addWidget(self.canvas, 1)
        col.addWidget(self.status)
        col.addLayout(row)

        self._pos: tuple[float, float] | None = None
        self._frame: tuple[int, int] = (0, 0)
        self._tracking = False

    def preferred_size(self, host: QtWidgets.QWidget) -> tuple[int, int]:
        """About a third of the view wide, bounded, plus room for controls."""
        w = int(max(280, min(500, host.width() * 0.34)))
        return w, int(w * 0.58) + 76

    # ---- feed ------------------------------------------------------------

    def update_live(self, s: LiveSignals) -> None:
        # Frames measured before the last clear still arrive after it --
        # the analysis thread was mid-frame when the reset landed -- and
        # their positions describe an origin that no longer exists.
        # Banking one repainted the current view at its stale position,
        # which is why clearing the map used to take two presses.
        fresh = s.track_gen >= self._min_gen
        pos = s.stage_pos if fresh else None
        tracking = s.stage_tracking and fresh
        h, w = s.preview.shape[:2]
        self._pos = pos
        self._frame = (w, h)
        self._tracking = tracking
        changed = self.model.observe(pos, s.preview, tracking)
        self.canvas.set_state(pos, (w, h), tracking, changed)
        self.pin_btn.setEnabled(pos is not None)
        self._update_status()

    def ignore_before(self, generation: int) -> None:
        """Refuse frames measured under an origin older than `generation`.
        Handed back by `reset_tracking` at every clear."""
        self._min_gen = int(generation)

    def set_mosaic(self, on: bool) -> None:
        """Confirmation from the session owner, not the click itself."""
        self._mosaic_on = on
        self.mosaic_btn.setChecked(on)
        self.undo_btn.setVisible(on)
        if not on:
            self.model.clear_tiles()
            self.canvas.update()
        self._update_status()

    def tile_added(self, pos, preview, state: str = "ok",
                   label: str = "") -> None:
        if pos is not None and preview is not None:
            self.model.add_tile(pos, preview, state=state, label=label)
        self.canvas.update()
        self._update_status()

    def set_tile_state(self, index: int, state: str) -> None:
        self.model.set_tile_state(index, state)
        self.canvas.update()

    def tile_removed(self) -> None:
        self.model.pop_tile()
        self.canvas.update()
        self._update_status()

    def _mosaic_status(self) -> tuple[str, str] | None:
        """Tiles down, and how much the current view overlaps them --
        the number the operator steers by while laying a mosaic."""
        if not self._mosaic_on:
            return None
        n = len(self.model.tiles)
        if n == 0:
            return _("map.mosaic.first"), theme.BRASS
        if self._pos is None or self._frame[0] <= 0:
            return n_("map.mosaic.tiles", n), theme.DIM
        best = max((overlap_fraction(self._pos, t.pos, self._frame)
                    for t in self.model.tiles), default=0.0)
        if best <= 0.005:
            return n_("map.mosaic.no_overlap", n), theme.BAD
        colour = theme.GOOD if 0.12 <= best <= 0.35 else theme.BRASS
        return (n_("map.mosaic.overlap", n, percent=f"{best * 100:.0f}"),
                colour)

    def _update_status(self) -> None:
        mosaic = self._mosaic_status()
        if mosaic is not None:
            text, colour = mosaic
            self.status.setText(text)
            self.status.setStyleSheet(f"color: {colour};")
            return
        guide = self.model.guidance(self._pos, self._frame[0])
        if guide is not None:
            fields, compass = guide
            if fields < 0.15:
                text, colour = _("map.guide.arrived"), theme.GOOD
            else:
                text = _("map.guide.distance", pin=self.model.target,
                         fields=f"{fields:.1f}", compass=compass)
                colour = theme.BRASS
        elif self._pos is None:
            text, colour = _("map.state.waiting"), theme.DIM
        elif not self._tracking:
            # Held, not lost: the position resumes when structure returns,
            # but anything cranked over blank glass is not measured.
            text, colour = _("map.state.holding"), theme.DIM
        else:
            # Painted ground in fields -- area, where the old number was
            # postcards. Honest for the first time: a heavily revisited
            # region used to count once per postcard however it overlapped.
            n = int(round(self.model.terrain.fields_painted))
            text = f"tracking · {n} field{'s' if n != 1 else ''} mapped"
            colour = theme.DIM
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour};")

    # ---- actions ---------------------------------------------------------

    def _on_pin(self) -> None:
        if self._pos is None:
            return
        self.model.add_pin(self._pos)
        self.canvas.update()
        self._update_status()

    def _on_pin_clicked(self, pin_id: int) -> None:
        self.model.target = None if self.model.target == pin_id else pin_id
        self.canvas.update()
        self._update_status()

    def clear(self) -> None:
        self.model.reset()
        self.reset_requested.emit()
        self.canvas.update()
        self._update_status()
