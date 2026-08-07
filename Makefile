# Short names for the things worth typing often. Nothing here is
# required -- every target is one command you could run yourself.

VENV := .venv/bin
PY   := $(VENV)/python

.PHONY: test test-fast test-serial

## test: the whole suite, one pass. Slower, and the answer is always right.
test:
	$(PY) -m pytest -q

## test-fast: the same suite forked across cores, then the handful of
## tests that assert something about elapsed time run alone afterwards.
##
## About half the wall clock of `make test` -- and NOT yet trustworthy.
## It still fails perhaps one run in three or four, on a different test
## each time, and every one investigated so far has been starvation
## rather than a real defect: sixteen workers each asking OpenCV and BLAS
## for sixteen threads. Pinning them to one apiece (see tests/conftest.py)
## made it much rarer and did not make it go away.
##
## Use it while working, when a red test costs you a re-run. Do not
## believe it over `make test`, and do not gate anything on it.
test-fast:
	$(PY) -m pytest -q -n auto -m "not serial"
	$(PY) -m pytest -q -m serial

## test-serial: only the tests that make claims about elapsed time.
test-serial:
	$(PY) -m pytest -q -m serial
