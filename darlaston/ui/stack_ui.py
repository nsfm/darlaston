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
        self.progress.hide()
        self.progress.setStyleSheet(
            f"QProgressBar {{ border: 0; background: {theme.SUNK}; }}"
            f"QProgressBar::chunk {{ background: {theme.BRASS}; }}")

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

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addWidget(self.canvas, 1)
        col.addWidget(self.progress)
        col.addLayout(row)

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
        menu.setStyleSheet(self.window().styleSheet())

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
        # Presets rather than a slider because the surrounding menu is
        # presets. The values are the measured range: 0.20 suited a smooth
        # carapace, 0.60 a mount of crossing diatoms, and 0.35 is what the
        # optics imply for both. A continuous control belongs with the
        # side by side sampler, where there is something to judge it by.
        group(N_("stack.halo.heading"), "stack_clamp_slope",
              [(N_("stack.halo.off.label"), 0.0),
               (N_("stack.halo.strong.label"), 0.20),
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
            p.fillRect(QtCore.QRect(x + 6, y + 6, 72, 18),
                       QtGui.QColor(0, 0, 0, 150))
            p.drawText(QtCore.QRect(x + 10, y + 6, 68, 18),
                       QtCore.Qt.AlignmentFlag.AlignVCenter,
                       n_("stack.canvas.detail.slices", o._count))
