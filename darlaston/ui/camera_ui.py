"""Which of the cameras on the bench is the one on the microscope.

Only ever shown when that is a real question. With one camera it is not
a question, and putting a dialog in front of somebody to confirm the
only possible answer is ceremony.

The list is complete and unfiltered. It is tempting to leave out the
laptop's own webcam, and the infrared face-unlock sensor beside it, on
the grounds that nobody photographs a diatom with either -- but a rule
confident enough to hide a device will one day hide the right one, and
the recovery from that is a command line flag. So everything is here,
with the likeliest first and each one described well enough that the
wrong choice is obviously wrong.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..camera.discovery import Camera, trouble
from ..i18n import _
from . import theme
from .framed import FramedDialog


def describe(camera: Camera) -> str:
    """The line under the name: what this camera can actually do.

    Said plainly, because the differences between these devices are
    large and invisible. One here offers sixteen controls and
    uncompressed frames; another, costing three times as much, offers
    neither -- and nothing on the outside of either says so.
    """
    bits = []
    if camera.width and camera.height:
        bits.append(_("camera.pick.size", width=camera.width,
                      height=camera.height,
                      mp=f"{camera.megapixels:.1f}"))
    if camera.detail:
        bits.append(camera.detail)
    if camera.kind == "v4l2":
        bits.append(_("camera.pick.controls", n=camera.controls)
                    if camera.controls else _("camera.pick.no_controls"))
    if not camera.uncompressed and camera.kind == "v4l2":
        bits.append(_("camera.pick.compressed"))
    return "  ·  ".join(bits)


class CameraPicker(FramedDialog):
    """Pick one, and remember it."""

    def __init__(self, cameras: list[Camera], current: str = "",
                 parent=None) -> None:
        super().__init__(parent, width=520)
        self.setWindowTitle(_("camera.pick.title"))
        self.cameras = cameras
        self._chosen = current

        heading = QtWidgets.QLabel(_("camera.pick.heading"))
        heading.setProperty("role", "heading")
        body = QtWidgets.QLabel(_("camera.pick.body"))
        body.setWordWrap(True)
        body.setProperty("role", "body")

        self.list = QtWidgets.QListWidget()
        self.list.setAlternatingRowColors(False)
        for camera in cameras:
            item = QtWidgets.QListWidgetItem(f"{camera.name}\n{describe(camera)}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, camera.key)
            self.list.addItem(item)
            if camera.key == current:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and cameras:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.list.setMinimumHeight(min(320, 64 * max(1, len(cameras))))

        col = self.content
        col.addWidget(heading)
        col.addWidget(body)
        col.addWidget(self.list)

        # Anything plugged in that we cannot reach, and why. A camera
        # that is attached and unusable is the most confusing state there
        # is, because the application looks like it has not noticed.
        for line in trouble():
            note = QtWidgets.QLabel(line)
            note.setWordWrap(True)
            note.setProperty("role", "key")
            note.setStyleSheet(f"color: {theme.BRASS}; background: transparent;")
            col.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addSpacing(8)
        col.addWidget(buttons)
        self.finish()

    def chosen(self) -> str:
        item = self.list.currentItem()
        return item.data(QtCore.Qt.ItemDataRole.UserRole) if item else ""
