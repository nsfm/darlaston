"""A newer version exists, and where to get it.

Notify and hand off, deliberately: this downloads nothing and installs
nothing. The macOS and Windows builds are unsigned, and an updater that
swaps an unsigned binary in place walks into Gatekeeper and SmartScreen
with the added indignity that a *downloaded* file carries quarantine on
macOS -- so a self-updated copy can end up worse off than one somebody
dragged into Applications themselves. Until there is a certificate, the
honest thing is to say a release exists and open the page.

The check itself lives in `darlaston.update` and knows nothing about
windows. This is the half that interrupts somebody, and it tries to do
it once, quietly, and never again until there is something new to say.
"""
from __future__ import annotations

import threading

from PySide6 import QtCore, QtGui, QtWidgets

from .. import __version__
from ..i18n import _
from ..update import Release, latest_release, newer, parse
from . import theme
from .framed import FramedDialog


def mark_as_update(action) -> None:
    """Make one menu entry stand out from the ones above it.

    A menu item cannot be given a colour by stylesheet -- the rule
    applies to the whole menu or to nothing -- so the two levers that
    work everywhere are the font and an icon. Both are used, quietly: a
    brass disc and a bold label.

    The disc rather than an exclamation mark or a badge, because this
    program already means "state, at a glance" with a coloured dot in the
    status bar. Reusing that is one less thing to learn; inventing a new
    mark for one menu entry is one more.
    """
    from PySide6 import QtGui

    size = 12
    pip = QtGui.QPixmap(size, size)
    pip.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pip)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    # A soft ring under it, the way the streaming dot glows. Enough to
    # catch the eye in a list of plain entries without being a warning:
    # a new version is good news, not a fault.
    glow = QtGui.QColor(theme.BRASS)
    glow.setAlpha(70)
    p.setBrush(glow)
    p.drawEllipse(0, 0, size, size)
    p.setBrush(QtGui.QColor(theme.BRASS))
    p.drawEllipse(3, 3, size - 6, size - 6)
    p.end()

    action.setIcon(QtGui.QIcon(pip))
    font = action.font()
    font.setBold(True)
    action.setFont(font)


class UpdateWatch(QtCore.QObject):
    """Asks once, in the background, and says nothing if it cannot.

    On a thread because the request can hang for as long as its timeout
    -- a captive portal at a venue will happily accept the connection and
    then never answer -- and a program that stalls its interface to find
    out whether a newer one exists has its priorities the wrong way
    round. The result comes back through a signal, so it arrives on the
    interface thread like everything else.
    """

    #: Emitted only when there is genuinely something newer. Never for a
    #: failure, and never for "you are up to date": both are silence.
    found = QtCore.Signal(object)

    def __init__(self, parent=None, current: str = __version__) -> None:
        super().__init__(parent)
        self.current = current
        self._thread: threading.Thread | None = None

    def start(self, delay_ms: int = 4000) -> None:
        """Look, after the application has finished starting.

        Delayed because the first seconds belong to opening the camera,
        and because somebody who launches this to photograph something is
        not there to be told about version numbers.
        """
        QtCore.QTimer.singleShot(delay_ms, self._look)

    def _look(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._ask, daemon=True,
                                        name="update-check")
        self._thread.start()

    def _ask(self) -> None:
        try:
            release = latest_release()
            if release is not None and newer(self.current, release.tag):
                self.found.emit(release)
        except Exception:
            pass          # a failed check is not a thing to report


class UpdateDialog(FramedDialog):
    """What is new, and a way to go and get it."""

    def __init__(self, release: Release, parent=None) -> None:
        super().__init__(parent, width=460)
        self.release = release
        self.setWindowTitle(_("update.title"))

        fam = theme.load_fonts()

        heading = QtWidgets.QLabel(_("update.heading"))
        heading.setProperty("role", "heading")

        # The tidy form of our own version, not the raw one. A build from
        # between releases calls itself `0.7.1.dev1+gdfa022e2a.d20260802`,
        # which is the right thing to put in a bug report and the wrong
        # thing to put in front of somebody deciding whether to upgrade.
        # The full string is still in the About box, where it is wanted.
        here = parse(__version__)
        versions = QtWidgets.QLabel(
            _("update.versions", current=str(here) if here else __version__,
              latest=str(release.version)))
        versions.setStyleSheet(
            f"font-family: '{fam['mono']}'; font-size: 11px;"
            f"color: {theme.DIM}; background: transparent;")

        body = QtWidgets.QLabel(_("update.body"))
        body.setWordWrap(True)
        body.setProperty("role", "body")

        col = self.content
        col.addWidget(heading)
        col.addWidget(versions)
        col.addSpacing(6)
        col.addWidget(body)

        # The release notes, if there are any and they are short enough to
        # be a summary rather than a document. A scrolling wall of commit
        # subjects in a dialog is not something anybody reads.
        notes = (release.notes or "").strip()
        if notes:
            if len(notes) > 700:
                notes = notes[:700].rsplit("\n", 1)[0] + "\n..."
            block = QtWidgets.QLabel(notes)
            block.setWordWrap(True)
            block.setProperty("role", "key")
            block.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            col.addSpacing(10)
            col.addWidget(block)

        col.addSpacing(16)
        col.addLayout(self._buttons())
        self.finish()

    def _buttons(self) -> QtWidgets.QHBoxLayout:
        open_page = QtWidgets.QPushButton(_("update.open"))
        open_page.setProperty("role", "primary")
        open_page.clicked.connect(self._open)

        later = QtWidgets.QPushButton(_("update.later"))
        later.clicked.connect(self.reject)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(later)
        row.addStretch(1)
        row.addWidget(open_page)
        return row

    def _open(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self.release.page))
        self.accept()
