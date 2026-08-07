"""Names that only exist on one platform.

Most of this program runs everywhere and the tests exercise it. The
window frames do not: the Windows half is a few hundred lines that no
machine here can execute, so a plain typo in it is not a failure, it is
silence -- and every one of those methods catches `Exception` and falls
back, so the symptom on a tester's machine is a feature quietly not
working rather than a traceback.

This walks every module in the package and asks, of every name a
function reads from module scope, whether that name exists. It found a
real one: a struct referenced without its namespace inside
`WM_NCCALCSIZE`, which on Windows would have raised `NameError` into an
`except Exception` and left every maximised window with its toolbar cut
off, for good, with nothing in the log.

Not a substitute for a linter. It is the one check that matters most for
code the test suite cannot run.
"""
from __future__ import annotations

import builtins
import importlib
import pkgutil
import symtable
from pathlib import Path

import pytest

import darlaston

ROOT = Path(darlaston.__file__).parent

#: Read at runtime from a place this cannot see: a module that pulls
#: names out of another namespace, or defines them conditionally.
ALLOWED: dict[str, set[str]] = {}


def modules():
    """Every module in the package, by dotted name and path."""
    for info in pkgutil.walk_packages([str(ROOT)], "darlaston."):
        path = ROOT / (info.name[len("darlaston."):].replace(".", "/"))
        path = path / "__init__.py" if info.ispkg else path.with_suffix(".py")
        if path.exists():
            yield info.name, path


def free_names(table: symtable.SymbolTable, top: bool = True):
    """Names a nested scope reads but does not own.

    `is_global` is symtable's answer for a name that is neither local to
    the scope nor bound in an enclosing one -- exactly the set that has
    to resolve against module globals or builtins at call time. A name
    bound inside the scope is excluded three ways, because an import is
    reported as imported and local but *not* as assigned.

    The module's own table is skipped: a missing name there raises at
    import, which the line above already did.
    """
    if not top:
        for symbol in table.get_symbols():
            if symbol.is_global() and not (symbol.is_local()
                                           or symbol.is_assigned()
                                           or symbol.is_imported()):
                yield symbol.get_name()
    for child in table.get_children():
        yield from free_names(child, top=False)


@pytest.mark.parametrize("name,path", list(modules()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_every_name_a_function_reads_from_module_scope_exists(name, path):
    module = importlib.import_module(name)
    table = symtable.symtable(path.read_text(), str(path), "exec")
    # Anything the module *could* bind, not only what it did. A name bound
    # under `if __name__ == "__main__"` is real but absent after an import,
    # and that is the only false positive this turned up.
    known = (set(vars(module)) | set(dir(builtins)) | ALLOWED.get(name, set())
             | {s.get_name() for s in table.get_symbols() if s.is_local()})
    missing = sorted({n for n in free_names(table) if n not in known})
    assert not missing, f"{name} reads undefined name(s): {missing}"


def test_no_test_builds_a_widget_without_asking_for_the_application():
    """Constructing a QWidget with no QApplication does not raise, it
    segfaults.

    A test that skips the `qapp` fixture passes anyway as long as some
    earlier test in the same process happened to make one -- an ordering
    accident, not a guarantee. Running the suite across processes turns it
    into a crash, which is how this was found, and it would equally be a
    crash for anyone running a single test file on its own.
    """
    import ast
    from pathlib import Path

    #: Suffixes that name a Qt object in this codebase.
    WIDGETS = ("Dialog", "View", "Panel", "Editor", "Bar", "Window", "Group",
               "Meter", "Swatch", "Picker", "Assembly", "Widget", "Button")

    offenders = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for fn in tree.body:
            if not (isinstance(fn, ast.FunctionDef)
                    and fn.name.startswith("test_")):
                continue
            if {"qapp", "window"} & {a.arg for a in fn.args.args}:
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and any(node.func.id.endswith(w) for w in WIDGETS)):
                    offenders.append(f"{path.name}:{fn.lineno} {fn.name} "
                                     f"builds {node.func.id}()")
                    break
    assert not offenders, (
        "these build a Qt object without requesting `qapp`, and pass only "
        "because something else made the application first: "
        + "; ".join(offenders))
