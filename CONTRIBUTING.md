# Contributing to darlaston

Thank you for your interest in darlaston! Contributions are always welcome. Some of the most important things you can offer are observations: we'd love to hear that a camera worked or didn't, the kinds of specimens you're observing, and the sort of workflow you're using the tool for. Please open an issue on Github to share information with us.

## AI disclosure

Claude has been used extensively for development, but all code is human reviewed. If you plan on using AI to develop a new feature, please exercise taste, judgment, and due diligence. Run adversarial review passes on your changes for correctness and pattern adherence before submitting any change.

## Running from source

_darlaston_ requires Python 3.11 or newer.

```sh
git clone https://github.com/nsfm/darlaston
cd darlaston
make install
make test
make run
```

You can run the tool with `--mock` to test against a synthetic camera.

## Pull requests

Please be as descriptive as possible when you open a pull request. Tests will run against Python 3.11 and 3.13, and builds will run on all supported platforms. If your change affects capture, calibration, or the preview pipeline, please let us know what hardware you tested with and what sort of specimen you captured images of.
