# Contributing to darlaston

Thank you for looking. This is a capture and processing tool for
photomicrography, and the most useful contributions are often not code:
a camera model confirmed working, a slide that breaks the stitcher, or a
measurement that shows something here is wrong.

## Reporting things

- **A bug**: use the bug report template. The single most useful thing you
  can include is the output of `darlaston --selftest`, which says what your
  build can and cannot reach.
- **A camera**: the camera support template. darlaston drives the whole
  ToupTek family and its rebadges (Altair, Meade, Omegon, Risingcam and
  others), and the only way to know which ones really work is for someone
  to plug one in.
- **A feature**: the feature request template. Say what you were trying to
  photograph -- the workflow matters more than the button.

## Running from source

```sh
git clone https://github.com/nsfm/darlaston
cd darlaston
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
darlaston
```

Python 3.11 or newer. The camera SDK is **not** required to run the tests
or the interface; without one, `--mock` gives a synthetic camera that
moves, focuses and rotates its turret.

## Things that are not up for negotiation

These are load-bearing, and a pull request that changes them will be asked
to change back.

- **Three runtime dependencies: numpy, OpenCV and PySide6.** Nothing else.
  A microscope in a lab is often a machine that cannot reach the internet,
  and every dependency is something that has to arrive with the program.
  `opencv-python-headless` specifically, never plain `opencv-python`: the
  full wheel bundles its own Qt, which loads alongside PySide6's and
  crashes at startup.
- **The vendor SDK is never vendored, mirrored or committed.** It ships
  with no licence or copyright notice of any kind, so it is loaded from
  wherever the user installed it, at runtime. See `SUPPORT.md`.
- **GPLv3, with the linking exception in `LICENSE.EXCEPTION`** for the
  camera libraries. Contributions are under the same terms.
- **No em dashes anywhere in the package.** Use `--`. This is a house style
  and it is applied consistently.

## Style

Read the file you are changing before you change it. The comments here
explain *why*, especially where something was measured, and several say
what was tried and reverted -- that is deliberate, and worth matching.

Two habits that matter more than formatting:

- **Measure before fixing.** Several comments in this codebase record a
  theory that the measurement refuted. If you are making something faster
  or more accurate, say what you measured and how.
- **Say what you did not verify.** An honest "I could not test this on
  real hardware" is worth more than a confident guess.

Tests are named as full sentences describing the behaviour, for example
`test_a_slow_pan_is_not_quietly_lost`. The suite builds real widgets and
grabs them, so it needs a Qt platform plugin; `tests/conftest.py` sets the
offscreen one for you.

## Commits and versions

**Every commit's version is worked out from the history behind it.** Start
at 0.0.0 and walk forward: a `[MAJOR]` at the front of a subject bumps the
major, `[MINOR]` bumps the minor, anything else bumps the patch.

```
[MINOR] Split setup into Microscopes and Cameras windows
Fix histogram crash on the first paint
```

Most commits are patches and need no marker. Mark a commit only when it
adds a feature or breaks something. Every push to `main` builds all three
platforms and publishes a release, so the number takes care of itself and
nobody types one.

Keep subjects short and technical. The body is for what changed and why,
including what went wrong on the way.

## Pull requests

CI has to be green: the tests on Python 3.11 and 3.13, and a build on
Linux, macOS and Windows that is launched and made to prove it can reach
its own fonts and icons.

If your change touches capture, calibration or the live pipeline, say
whether it was tested against real hardware or only the mock. Both are
acceptable. Not saying which is not.
