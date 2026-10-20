"""What exists on Windows alone is reached behind a platform check mypy reads.

CI type-checks the package on Linux: the `quality` jobs run `mypy auto_bi` and `mypy
--strict auto_bi` on `ubuntu-latest`. mypy checks one platform, by default the one it runs on,
and skips a branch it can tell is not taken there — a branch on `sys.platform`. A branch on
`os.name` it cannot tell, and checks on every platform. So code that reaches a Windows-only
API under `os.name == "nt"` type-checks on Windows and fails CI on Linux. T-63's staging of an
artifact under the owner and DACL of the file it replaces did so: `mypy --strict` found 9
errors in it on Linux and macOS, none on Windows. Behind a check of `sys.platform` Linux skips
the branch, and the `quality` job checks it once more, for Windows (`--platform win32`).

This module reads the sources of the package, so it needs no mypy and fails on the platform
its author runs it on. Windows-only is what typeshed declares under `sys.platform == "win32"`
alone: the modules and names below, and what the package itself defines behind such a check.
The reverse — a POSIX-only API reached on Windows — is not read here: the Windows run of mypy
finds it.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "auto_bi"

#: Modules typeshed has for Windows alone.
WINDOWS_MODULES = frozenset({"msvcrt", "winreg", "winsound", "_winapi", "nt"})

#: Names typeshed declares in a module for Windows alone, by the module.
WINDOWS_NAMES = {
    "ctypes": frozenset(
        {
            "WinDLL",
            "OleDLL",
            "windll",
            "oledll",
            "WinError",
            "FormatError",
            "GetLastError",
            "get_last_error",
            "set_last_error",
            "HRESULT",
        }
    ),
    "os": frozenset(
        {
            "O_BINARY",
            "O_NOINHERIT",
            "O_SHORT_LIVED",
            "O_TEMPORARY",
            "O_RANDOM",
            "O_SEQUENTIAL",
            "O_TEXT",
            "startfile",
            "add_dll_directory",
        }
    ),
    "subprocess": frozenset(
        {
            "STARTUPINFO",
            "CREATE_NEW_CONSOLE",
            "CREATE_NEW_PROCESS_GROUP",
            "CREATE_NO_WINDOW",
            "DETACHED_PROCESS",
            "STARTF_USESHOWWINDOW",
            "STARTF_USESTDHANDLES",
        }
    ),
    "signal": frozenset({"CTRL_C_EVENT", "CTRL_BREAK_EVENT"}),
}


def _on_windows(test: ast.expr) -> bool | None:
    """Whether *test* says "on Windows" (True), "not on Windows" (False), or neither (None).

    These are the forms mypy reads: `sys.platform == "win32"`, `sys.platform.startswith("win")`,
    their negations by `!=` and `not`.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _on_windows(test.operand)
        return None if inner is None else not inner
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and _is_sys_platform(test.left)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "win32"
    ):
        if isinstance(test.ops[0], ast.Eq):
            return True
        if isinstance(test.ops[0], ast.NotEq):
            return False
        return None
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Attribute)
        and test.func.attr == "startswith"
        and _is_sys_platform(test.func.value)
        and len(test.args) == 1
        and isinstance(test.args[0], ast.Constant)
        and test.args[0].value == "win"
    ):
        return True
    return None


def _is_sys_platform(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "platform"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _branches(test: ast.expr) -> tuple[bool, bool]:
    """Whether the body and the `else` of an `if` on *test* are taken on Windows alone.

    Of an `and` or an `or`, mypy reads the leftmost operand only: `flag and sys.platform ==
    "win32"` is no platform check, and its body is checked on every platform.
    """
    if isinstance(test, ast.BoolOp):
        first = _on_windows(test.values[0])
        if isinstance(test.op, ast.And):
            return first is True, False
        return False, first is False
    said = _on_windows(test)
    return said is True, said is False


def _bound(statements: list[ast.stmt]) -> set[str]:
    """The names a run of module-level statements binds."""
    names: set[str] = set()
    for statement in statements:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(statement.name)
        elif isinstance(statement, ast.Import | ast.ImportFrom):
            names.update((alias.asname or alias.name).split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                names.update(node.id for node in ast.walk(target) if isinstance(node, ast.Name))
        elif isinstance(statement, ast.If):
            names |= _bound(statement.body) | _bound(statement.orelse)
    return names


def _windows_defined(module: ast.Module) -> frozenset[str]:
    """What the module binds behind a Windows check at its top level, and nowhere else."""
    windows: set[str] = set()
    elsewhere: set[str] = set()
    for statement in module.body:
        if isinstance(statement, ast.If):
            body_windows, else_windows = _branches(statement.test)
            (windows if body_windows else elsewhere).update(_bound(statement.body))
            (windows if else_windows else elsewhere).update(_bound(statement.orelse))
        else:
            elsewhere |= _bound([statement])
    return frozenset(windows - elsewhere)


def findings(source: str) -> list[tuple[int, str]]:
    """Each Windows-only module, name or local definition reached outside a Windows check, and
    each look at `os.name`, which mypy does not read: `(line, what)`."""
    module = ast.parse(source)
    local = _windows_defined(module)
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, windows: bool) -> None:
        if isinstance(node, ast.If):
            body_windows, else_windows = _branches(node.test)
            visit(node.test, windows)
            for child in node.body:
                visit(child, windows or body_windows)
            for child in node.orelse:
                visit(child, windows or else_windows)
            return
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr == "name":
                found.append((node.lineno, "os.name"))
            elif not windows and node.attr in WINDOWS_NAMES.get(node.value.id, ()):
                found.append((node.lineno, f"{node.value.id}.{node.attr}"))
        elif isinstance(node, ast.Import) and not windows:
            for alias in node.names:
                if alias.name.split(".")[0] in WINDOWS_MODULES:
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            top = node.module.split(".")[0]
            for alias in node.names:
                if node.module == "os" and alias.name == "name":
                    found.append((node.lineno, "os.name"))
                elif not windows and (
                    top in WINDOWS_MODULES or alias.name in WINDOWS_NAMES.get(node.module, ())
                ):
                    found.append((node.lineno, f"from {node.module} import {alias.name}"))
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and not windows
            and node.id in local
        ):
            found.append((node.lineno, node.id))
        for child in ast.iter_child_nodes(node):
            visit(child, windows)

    visit(module, False)
    return sorted(found)


def _modules() -> Iterator[Path]:
    yield from sorted(PACKAGE.rglob("*.py"))


def test_the_package_reaches_windows_only_api_behind_a_check_mypy_reads() -> None:
    found = [
        f"{path.relative_to(PACKAGE.parent).as_posix()}:{line}: {what}"
        for path in _modules()
        for line, what in findings(path.read_text(encoding="utf-8"))
    ]
    assert found == [], (
        "branch on `sys.platform` (mypy reads it; `os.name` it does not), with the check "
        "leftmost of an `and`: " + "; ".join(found)
    )


def test_the_package_is_read() -> None:
    modules = list(_modules())
    assert len(modules) > 100
    assert PACKAGE / "evidence" / "store.py" in modules


REACHED = [
    pytest.param("import msvcrt", ["import msvcrt"], id="a Windows module at the top"),
    pytest.param(
        """
        def f():
            import msvcrt
        """,
        ["import msvcrt"],
        id="a Windows module in a function",
    ),
    pytest.param(
        """
        import os
        if os.name == "nt":
            import msvcrt
        """,
        ["import msvcrt", "os.name"],
        id="behind os.name",
    ),
    pytest.param(
        """
        import sys
        if flag and sys.platform == "win32":
            import msvcrt
        """,
        ["import msvcrt"],
        id="behind a check right of an and",
    ),
    pytest.param(
        """
        import sys
        if sys.platform != "win32":
            import msvcrt
        """,
        ["import msvcrt"],
        id="in the branch that is not Windows",
    ),
    pytest.param(
        """
        import sys
        if sys.platform == "linux":
            import msvcrt
        """,
        ["import msvcrt"],
        id="behind a check of another platform",
    ),
    pytest.param(
        """
        import sys
        if sys.platform == "win32" or flag:
            import msvcrt
        """,
        ["import msvcrt"],
        id="behind a check left of an or",
    ),
    pytest.param(
        """
        import os
        flags = os.O_BINARY
        """,
        ["os.O_BINARY"],
        id="a Windows flag of os",
    ),
    pytest.param(
        """
        import ctypes
        lib = ctypes.WinDLL("kernel32")
        """,
        ["ctypes.WinDLL"],
        id="a loader of ctypes",
    ),
    pytest.param(
        "from ctypes import get_last_error",
        ["from ctypes import get_last_error"],
        id="a Windows name imported from ctypes",
    ),
    pytest.param("from os import name", ["os.name"], id="os.name imported"),
    pytest.param(
        """
        import sys
        if sys.platform == "win32":
            def staged():
                return 1
        def put():
            return staged()
        """,
        ["staged"],
        id="a definition of the package's own behind the check, reached outside it",
    ),
    pytest.param(
        """
        import sys
        if sys.platform == "win32":
            def staged():
                return 1
        def put(flag):
            if flag and sys.platform == "win32":
                return staged()
        """,
        ["staged"],
        id="a definition of the package's own, reached right of an and",
    ),
]


@pytest.mark.parametrize("source,expected", REACHED)
def test_what_is_reached_outside_a_windows_check_is_found(source: str, expected: list[str]) -> None:
    assert sorted(what for _, what in findings(textwrap.dedent(source))) == sorted(expected)


GUARDED = [
    pytest.param(
        """
        import sys
        if sys.platform == "win32":
            import msvcrt
        """,
        id="behind the check",
    ),
    pytest.param(
        """
        import sys
        if sys.platform.startswith("win"):
            import msvcrt
        """,
        id="behind startswith",
    ),
    pytest.param(
        """
        import sys
        if not sys.platform != "win32":
            import msvcrt
        """,
        id="behind a negated negation",
    ),
    pytest.param(
        """
        import sys
        if sys.platform != "win32":
            pass
        else:
            import msvcrt
        """,
        id="in the else of the check that is not Windows",
    ),
    pytest.param(
        """
        import os
        import sys
        if sys.platform == "win32" and flag:
            flags = os.O_BINARY
        """,
        id="behind the check left of an and",
    ),
    pytest.param(
        """
        import sys
        if sys.platform != "win32" or flag:
            pass
        else:
            import msvcrt
        """,
        id="in the else of an or whose left is not Windows",
    ),
    pytest.param(
        """
        import ctypes
        import sys
        if sys.platform == "win32":
            def staged():
                lib = ctypes.WinDLL("kernel32")
                return lib
        def put(flag):
            if sys.platform == "win32" and flag:
                return staged()
        """,
        id="a definition of the package's own, reached behind the check",
    ),
    pytest.param(
        """
        import sys
        if sys.platform == "win32":
            def staged():
                return 1
        else:
            def staged():
                return 0
        def put():
            return staged()
        """,
        id="a definition on every platform",
    ),
    pytest.param(
        """
        import os
        flags = os.O_WRONLY | getattr(os, "O_BINARY", 0)
        """,
        id="a Windows flag looked up by name",
    ),
]


@pytest.mark.parametrize("source", GUARDED)
def test_what_is_reached_behind_a_windows_check_is_not_found(source: str) -> None:
    assert findings(textwrap.dedent(source)) == []
