"""darlaston -- capture, calibration and mosaicking for photomicrography.

Named for Herbert William Hutton Darlaston (1867-1949), a Birmingham mounter
who turned professional around 1905 because too many people wanted his slides.
His diatom mounts survive from Constantinople to Santa Maria; this is software
for photographing that kind of work properly.

Copyright (C) 2026 Nate Dube. Free software under the GNU General Public
Licence version 3 or later (see LICENSE), with an additional permission
under section 7 for linking against camera and hardware-control
libraries -- see LICENSE.EXCEPTION.
"""
try:
    # Written at build time by setuptools-scm, from the git tags. Absent in
    # a bare source tree that was never built, which is the normal state of
    # a fresh clone, so it is not an error.
    from ._version import __version__
except ImportError:                                  # pragma: no cover
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _installed

        __version__ = _installed("darlaston")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
