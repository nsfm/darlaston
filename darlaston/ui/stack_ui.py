"""Watching a stack assemble.

A floating panel that shows the all-in-focus image building live: every
captured slice contributes its sharp regions, and the composite fills in as
the operator racks. It is a preview-resolution approximation of the real
merge -- winner-takes-all, no alignment -- and that is fine, because its job
is not accuracy. Its job is to show that the thing is working and to make
racking through a diatom feel like developing a photograph: the subject
emerges.

The panel is also the stack's control surface: finish and discard live here,
with the merge's progress, because this window is where the operator is
already looking. A second view tints each region by the slice that won it --
the depth data showing itself off while it is still being collected.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import N_, _, n_
from . import theme

#: Width of the running composite. Small enough that folding a slice in is
#: a few milliseconds, large enough to be worth watching.
WIDTH = 456


class StackAssembly(QtWidgets.QWidget):
    """The running composite, its depth view, and the stack's controls."""

    finish_requested = QtCore.Signal()
    discard_requested = QtCore.Signal()
    close_requested = QtCore.Signal()
    wiggle_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self._rgb: np.ndarray | None = None
        self._sharp: np.ndarray | None = None
        self._which: np.ndarray | None = None      # winning slice per pixel
        self._image: QtGui.QImage | None = None
        self._count = 0
        self._mode = "image"

        self.canvas = _AssemblyCanvas(self)

        self.view_toggle = QtWidgets.QPushButton(_("stack.view.action.depth"))
        self.view_toggle.setCheckable(True)
        self.view_toggle.setToolTip(_("stack.view.tooltip"))
        self.finish = QtWidgets.QPushButton(_("stack.finish.action"))
        self.finish.setToolTip(_("stack.finish.tooltip"))
        self.discard = QtWidgets.QPushButton(_("stack.discard.action"))
        self.discard.setToolTip(_("stack.discard.tooltip"))
        self.close_btn = QtWidgets.QPushButton(_("stack.close.action"))
        self.close_btn.hide()
        self.wiggle_btn = QtWidgets.QPushButton(_("stack.render.action"))
        self.wiggle_btn.setToolTip(_("stack.render.tooltip"))
        self.wiggle_btn.hide()
        self.options = QtWidgets.QToolButton()
        # A cog, so there is nothing here to translate. The tooltip carries
        # the words.
        self.options.setText("⚙")
        self.options.setToolTip(_("stack.options.tooltip"))
        self.options.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.options.setProperty("role", "seg")
        for b in (self.view_toggle, self.finish, self.discard,
                  self.close_btn, self.wiggle_btn):
            b.setProperty("role", "seg")
        self.finish.clicked.connect(self.finish_requested)
        self.discard.clicked.connect(self.discard_requested)
        self.close_btn.clicked.connect(self.close_requested)
        self.wiggle_btn.clicked.connect(self.wiggle_requested)
        self.view_toggle.toggled.connect(self._on_view)
        # Depth view is the default: watching the shape of the subject fill
        # in is the show, and it is a better teacher of the rack-pause
        # rhythm than the composite alone.
        self.view_toggle.setChecked(True)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        # Keep its three pixels reserved while it is hidden. Without this
        # the whole panel jumps taller the moment a merge starts, which is
        # exactly when the operator is watching it.
        policy = self.progress.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(policy)
        self.progress.hide()
        theme.dress(self.progress, lambda: (
            f"QProgressBar {{ border: 0; background: {theme.SUNK}; }}"
            f"QProgressBar::chunk {{ background: {theme.BRASS}; }}"))

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("role", "key")

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.view_toggle)
        row.addWidget(self.options)
        row.addWidget(self.status, 1)
        row.addWidget(self.discard)
        row.addWidget(self.finish)
        row.addWidget(self.wiggle_btn)
        row.addWidget(self.close_btn)

        # The focus motor's row, for the few cameras that have one. Hidden
        # otherwise, so a camera with no motor shows no control for it.
        self.focus_row = FocusRow()
        self.focus_row.hide()

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addWidget(self.canvas, 1)
        col.addWidget(self.progress)
        col.addLayout(row)
        col.addWidget(self.focus_row)

    def set_focus_axis(self, axis) -> None:
        """Show the motor's row when the camera has one, and not otherwise."""
        self.focus_row.set_axis(axis)
        self.focus_row.setVisible(axis is not None)

    def _on_view(self, on: bool) -> None:
        self._mode = "depth" if on else "image"
        self._render()

    def configure(self, settings) -> None:
        """Build the merge-options menu, bound straight to the settings.

        Three knobs, each a statement about the subject that the merge
        cannot make alone. Defaults are the measured winners from
        tools/stack_bench.py; the menu exists for the subjects the bench
        has not met yet.
        """
        menu = QtWidgets.QMenu(self.options)
        theme.dress(menu, theme.stylesheet)

        def group(heading, field, choices):
            # A disabled item as the header -- our stylesheet renders
            # QMenu sections as bare separators, which is too cryptic for
            # three anonymous groups. The heading and every choice arrive
            # as catalogue keys and are looked up here.
            if menu.actions():
                menu.addSeparator()
            head = menu.addAction(_(heading).upper())
            head.setEnabled(False)
            acts = QtGui.QActionGroup(menu)
            current = getattr(settings, field)
            for label, value in choices:
                act = menu.addAction(_(label))
                act.setCheckable(True)
                act.setChecked(value == current)
                acts.addAction(act)

                # `checked` rather than `_`, which is the catalogue lookup.
                def pick(checked=False, f=field, v=value):
                    setattr(settings, f, v)
                    settings.save()
                act.triggered.connect(pick)

        group(N_("stack.smoothing.heading"), "stack_smoothing",
              [(N_("stack.smoothing.off.label"), "off"),
               (N_("stack.smoothing.light.label"), "light"),
               (N_("stack.smoothing.normal.label"), "normal"),
               (N_("stack.smoothing.strong.label"), "strong")])
        group(N_("stack.feather.heading"), "stack_feather",
              [(N_("stack.feather.subtle.label"), 1.0),
               (N_("stack.feather.normal.label"), 2.0),
               (N_("stack.feather.wide.label"), 4.0)])
        # The two halves of the halo work, and they are separate entries
        # on purpose: one acts on the geometry and one on the photograph,
        # and giving both the same treatment measured harmful each way.
        group(N_("stack.background.heading"), "stack_mask_background",
              [(N_("stack.background.flatten.label"), True),
               (N_("stack.background.keep.label"), False)])
        # How the bound gets chosen, then what it is. The presets stay
        # for the operator who knows their subject; "help me choose" is
        # for the one who does not, which on a new mount is everyone.
        group(N_("stack.halo.heading"), "stack_halo_mode",
              [(N_("stack.halo.mode.off"), "off"),
               (N_("stack.halo.mode.last"), "last"),
               (N_("stack.halo.mode.choose"), "choose")])
        # The values are the measured range: 0.20 suited a smooth
        # carapace, 0.60 a mount of crossing diatoms, and 0.35 is what the
        # optics imply for both.
        group(N_("stack.halo.strength.heading"), "stack_clamp_slope",
              [(N_("stack.halo.strong.label"), 0.20),
               (N_("stack.halo.balanced.label"), 0.35),
               (N_("stack.halo.gentle.label"), 0.60)])
        group(N_("stack.output.heading"), "stack_output",
              [(N_("stack.output.bayer.label"), "bayer"),
               (N_("stack.output.linear.label"), "linear")])
        group(N_("stack.wiggle.heading"), "wiggle_invert",
              [(N_("stack.wiggle.normal.label"), False),
               (N_("stack.wiggle.inverted.label"), True)])
        self.options.setMenu(menu)

    def set_merging(self, done: int | None, total: int | None,
                    message: str = "") -> None:
        """Progress of the final merge, in the window already being watched."""
        if done is None:
            self.progress.hide()
        else:
            self.progress.show()
            self.progress.setRange(0, total or 0)
            self.progress.setValue(done)
        self.status.setText(message)

    def set_finished(self, message: str, wiggle: bool = False) -> None:
        """The merge is over. The stack's endings have all happened, so the
        control buttons give way to closing -- and, when the merge
        succeeded, to the depth map's party trick."""
        self.progress.hide()
        self.status.setText(message)
        self.finish.hide()
        self.discard.hide()
        self.wiggle_btn.setVisible(wiggle)
        self.wiggle_btn.setEnabled(True)
        self.close_btn.show()

    def reset(self) -> None:
        self._rgb = None
        self._sharp = None
        self._which = None
        self._image = None
        self._count = 0
        self.progress.hide()
        self.status.setText("")
        self.close_btn.hide()
        self.wiggle_btn.hide()
        self.finish.show()
        self.finish.setEnabled(True)
        self.discard.show()
        self.discard.setEnabled(True)
        self.update()
        self.canvas.update()

    def set_context(self, mosaic: bool) -> None:
        """In a mosaic, Finish seals the field as a tile and Discard drops
        just that field -- both leave the mosaic running -- so the buttons
        say so rather than borrowing the free-standing stack's words."""
        self.finish.setText(_("stack.seal.action") if mosaic
                            else _("stack.finish.action"))
        self.finish.setToolTip(_("stack.seal.tooltip") if mosaic
                               else _("stack.finish.tooltip"))
        self.discard.setText(_("stack.discard_field.action") if mosaic
                             else _("stack.discard.action"))
        self.discard.setToolTip(_("stack.discard_field.tooltip") if mosaic
                                else _("stack.discard.tooltip"))

    @property
    def count(self) -> int:
        return self._count

    def add_slice(self, raw: np.ndarray) -> None:
        """Fold one captured slice into the running composite.

        Quick half-Bayer demosaic and a pooled sharpness field, then each
        pixel keeps whichever slice was sharpest there so far. The 2x2 green
        mean stands in for luma exactly as the merge engine's does.
        """
        g = ((raw[0::2, 0::2].astype(np.float32)
              + raw[1::2, 1::2].astype(np.float32)) / 2)
        r = raw[1::2, 0::2].astype(np.float32)
        b = raw[0::2, 1::2].astype(np.float32)
        h, w = g.shape
        scale = WIDTH / w
        size = (WIDTH, max(1, int(h * scale)))
        rgb = cv2.resize(np.dstack([b, g, r]), size,
                         interpolation=cv2.INTER_AREA)
        luma = cv2.resize(g, size, interpolation=cv2.INTER_AREA)
        gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1)
        sharp = cv2.GaussianBlur(gx * gx + gy * gy, (0, 0), 3.0)

        if self._rgb is None:
            self._rgb = rgb
            self._sharp = sharp
            self._which = np.zeros(sharp.shape, np.uint8)
        else:
            better = sharp > self._sharp
            self._rgb[better] = rgb[better]
            self._which[better] = self._count
            np.maximum(self._sharp, sharp, out=self._sharp)
        self._count += 1
        self._render()

    def _render(self) -> None:
        if self._rgb is None:
            return

        # Roughly balanced and gamma'd, or a raw microscope field shows as a
        # dark green rectangle.
        show = self._rgb.copy()
        means = [max(float(show[:, :, c].mean()), 1e-6) for c in range(3)]
        for c in range(3):
            show[:, :, c] *= means[1] / means[c]
        peak = float(np.percentile(show, 99.5)) or 1.0
        show = np.power(np.clip(show / peak, 0, 1), 1 / 2.2)
        out = (show * 255).astype(np.uint8)

        if self._mode == "depth" and self._which is not None:
            # Luma carries the structure, the colormap carries which slice
            # won: the depth data as a living image rather than a by-product.
            luma = out.mean(axis=2).astype(np.uint8)
            idx = (self._which.astype(np.float32)
                   / max(self._count - 1, 1) * 255).astype(np.uint8)
            tint = cv2.applyColorMap(idx, cv2.COLORMAP_VIRIDIS)
            out = ((luma[:, :, None].astype(np.uint16) * tint) // 255)                 .astype(np.uint8)

        self._buf = np.ascontiguousarray(out)
        hh, ww = self._buf.shape[:2]
        self._image = QtGui.QImage(self._buf.data, ww, hh,
                                   self._buf.strides[0],
                                   QtGui.QImage.Format.Format_BGR888)
        self.canvas.update()


class _AssemblyCanvas(QtWidgets.QWidget):
    def __init__(self, owner: "StackAssembly") -> None:
        super().__init__()
        self._owner = owner
        self.setMinimumHeight(120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event) -> None:
        o = self._owner
        with QtGui.QPainter(self) as p:
            p.fillRect(self.rect(), QtGui.QColor(theme.SUNK))
            if o._image is None:
                p.setPen(QtGui.QColor(theme.DIM))
                p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                           _("stack.canvas.detail.empty"))
                return
            scaled = o._image.size().scaled(
                self.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QtCore.QRect(x, y, scaled.width(), scaled.height()),
                        o._image)
            p.setPen(QtGui.QColor(theme.INK))
            f = p.font()
            f.setPointSizeF(8.5)
            p.setFont(f)
            scrim = QtGui.QColor(theme.BG)
            scrim.setAlpha(190)
            p.fillRect(QtCore.QRect(x + 6, y + 6, 72, 18), scrim)
            p.drawText(QtCore.QRect(x + 10, y + 6, 68, 18),
                       QtCore.Qt.AlignmentFlag.AlignVCenter,
                       n_("stack.canvas.detail.slices", o._count))



class FocusRow(QtWidgets.QWidget):
    """Driving a focus motor by hand, and handing it to the sweep.

    One row: a slider that moves the motor as it is dragged (so the two
    ends of the subject are found by eye, the way they would be on the
    fine focus), two buttons that mark where the slider is as the near
    and far end, a count, and the sweep itself. The camera's own
    autofocus gets a button too, because finding the first plane is
    exactly what it is good at.
    """

    moved = QtCore.Signal(int)                   #: the slider, by hand
    autofocus_requested = QtCore.Signal()
    sweep_requested = QtCore.Signal(int, int, int)   #: near, far, count
    stop_requested = QtCore.Signal()

    #: Slices a sweep takes when nobody has said. Enough to cover a
    #: diatom at 40x with the step the motor has; the spinner is right
    #: there for anyone who knows better.
    DEFAULT_COUNT = 12

    def __init__(self) -> None:
        super().__init__()
        self._axis = None
        self._syncing = False
        self.near: int | None = None
        self.far: int | None = None
        self._running = False

        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setToolTip(_("stack.focus.slider.tooltip"))
        self.slider.valueChanged.connect(self._on_slider)

        self.near_btn = QtWidgets.QPushButton(_("stack.focus.near.action"))
        self.near_btn.setToolTip(_("stack.focus.near.tooltip"))
        self.far_btn = QtWidgets.QPushButton(_("stack.focus.far.action"))
        self.far_btn.setToolTip(_("stack.focus.far.tooltip"))
        self.count = QtWidgets.QSpinBox()
        self.count.setRange(2, 400)
        self.count.setValue(self.DEFAULT_COUNT)
        self.count.setToolTip(_("stack.focus.slices.tooltip"))
        self.auto_btn = QtWidgets.QPushButton(_("stack.focus.auto.action"))
        self.auto_btn.setToolTip(_("stack.focus.auto.tooltip"))
        self.sweep_btn = QtWidgets.QPushButton(_("stack.focus.sweep.action"))
        self.sweep_btn.setToolTip(_("stack.focus.sweep.tooltip"))
        self.marks = QtWidgets.QLabel(_("stack.focus.unmarked"))
        self.marks.setProperty("role", "key")
        for b in (self.near_btn, self.far_btn, self.auto_btn, self.sweep_btn):
            b.setProperty("role", "seg")

        self.near_btn.clicked.connect(self._mark_near)
        self.far_btn.clicked.connect(self._mark_far)
        self.auto_btn.clicked.connect(self.autofocus_requested)
        self.sweep_btn.clicked.connect(self._on_sweep)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(self.near_btn)
        top.addWidget(self.slider, 1)
        top.addWidget(self.far_btn)
        top.addWidget(self.auto_btn)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(4)
        bottom.addWidget(self.marks, 1)
        bottom.addWidget(self.count)
        bottom.addWidget(self.sweep_btn)
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addLayout(top)
        col.addLayout(bottom)
        self._refresh()

    # ---- the motor ---------------------------------------------------------

    def set_axis(self, axis) -> None:
        self._axis = axis
        self.near = self.far = None
        if axis is None:
            return
        r = axis.range()
        self._syncing = True
        try:
            self.slider.setRange(r.lo, r.hi)
            self.slider.setSingleStep(max(1, r.step))
            self.slider.setPageStep(max(1, r.span // 20))
            self.slider.setValue(axis.get())
        finally:
            self._syncing = False
        self._refresh()

    def show_position(self, position: int) -> None:
        """The motor moved on its own (a sweep, an autofocus): follow it
        without sending it anywhere."""
        self._syncing = True
        try:
            self.slider.setValue(int(position))
        finally:
            self._syncing = False

    def _on_slider(self, value: int) -> None:
        if not self._syncing:
            self.moved.emit(int(value))

    def _mark_near(self) -> None:
        self.near = self.slider.value()
        self._refresh()

    def _mark_far(self) -> None:
        self.far = self.slider.value()
        self._refresh()

    def _on_sweep(self) -> None:
        if self._running:
            self.stop_requested.emit()
            return
        if self.near is None or self.far is None:
            return
        self.sweep_requested.emit(self.near, self.far, self.count.value())

    def set_running(self, on: bool, done: int = 0, total: int = 0) -> None:
        self._running = on
        self.sweep_btn.setText(_("stack.focus.stop.action") if on
                               else _("stack.focus.sweep.action"))
        for w in (self.slider, self.near_btn, self.far_btn, self.auto_btn,
                  self.count):
            w.setEnabled(not on)
        if on:
            self.marks.setText(_("stack.focus.progress", done=done, total=total))
        else:
            self._refresh()

    def _refresh(self) -> None:
        if self.near is None or self.far is None:
            self.marks.setText(_("stack.focus.unmarked"))
        else:
            self.marks.setText(_("stack.focus.marked", near=self.near,
                                 far=self.far))
        self.sweep_btn.setEnabled(self._running
                                  or (self.near is not None
                                      and self.far is not None))
