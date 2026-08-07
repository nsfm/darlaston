#   make            what you can do
#   make install    a virtualenv you can work in
#   make test       the suite
#   make run        the application
#   make package    the double-clickable artifact for this machine

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

# What `bundle.py` will call the artifact. CI passes this per matrix
# entry; here there is only ever one machine, so it is derived. The
# spelling matches the workflow's labels so a local build and a released
# one are named the same thing.
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_S),Darwin)
  LABEL := macos-$(UNAME_M)
else ifeq ($(UNAME_S),Linux)
  LABEL := linux-$(UNAME_M)
else
  LABEL := windows-x86_64
endif

.DEFAULT_GOAL := help
.PHONY: help install test test-fast test-serial run mock cameras \
        package build smoke clean

## help: what you can do
help:
	@sed -n 's/^## //p' $(MAKEFILE_LIST)

## install: a virtualenv with darlaston and its test tools in it
install:
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

## test: the whole suite, one pass. Slower, and always right.
test:
	$(PY) -m pytest -q

## test-fast: forked across cores, then the timing tests alone afterwards.
##            About half the wall clock but it fails perhaps one run in three
test-fast:
	$(PY) -m pytest -q -n auto -m "not serial"
	$(PY) -m pytest -q -m serial

## test-serial: only the tests that assert something about elapsed time
test-serial:
	$(PY) -m pytest -q -m serial

## run: the application, against whatever camera is attached
run:
	$(VENV)/bin/darlaston

## mock: the application, against the synthetic camera
mock:
	$(VENV)/bin/darlaston --mock

## cameras: everything darlaston can see on this machine, and how well
cameras:
	$(VENV)/bin/darlaston --list-cameras

# ---- packaging -------------------------------------------------------
#
# The same four steps `.github/workflows/build.yml` runs, in the same
# order, calling the same scripts. Both smoke tests are here because both
# are in CI and for the reason the workflow gives: the build is not the
# deliverable, a program that starts is, and the wrapping is its own
# chance to lose a resource.

## build: freeze the application into dist/pyi
build:
	$(VENV)/bin/pyinstaller --noconfirm --distpath dist/pyi \
		--workpath build/pyi packaging/darlaston.spec

## smoke: make the frozen build prove it can reach its own resources
smoke:
	QT_QPA_PLATFORM=offscreen $(PY) packaging/smoke.py

## package: build, smoke, wrap, and smoke the wrapper. Needs the
##          packaging extra: pip install -e ".[package]"
package: build smoke
	$(PY) packaging/bundle.py --label "$(LABEL)"
	QT_QPA_PLATFORM=offscreen $(PY) packaging/smoke.py --packaged

## clean: remove everything build, packaging and pytest leave behind
clean:
	rm -rf build dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
