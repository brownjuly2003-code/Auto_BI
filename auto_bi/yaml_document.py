"""One YAML document from text this program did not write.

PyYAML answers a text it cannot scan, parse or compose with a ``yaml.YAMLError``. Building
the values of a composed document is plain Python, and what that raises leaves
``yaml.safe_load`` as it is: a date that does not exist or an integer longer than the
interpreter converts is a ``ValueError``, an explicit ``!!bool`` the tag does not describe a
``KeyError``, an explicit ``!!timestamp`` that is not one an ``AttributeError``, a document
nested deeper than the interpreter's stack a ``RecursionError``. A reader that grades an
unreadable file by catching ``yaml.YAMLError`` stops on every one of those, and the author
of the file is shown a traceback of this program instead of a finding about the file.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError

#: How many refusals of a schema a usage error spells out before it counts the rest.
_MAX_SCHEMA_ERRORS = 3


def load_yaml_document(text: str) -> Any:
    """What ``yaml.safe_load`` builds from *text*; a document it cannot build is a ``YAMLError``.

    A failure PyYAML reports itself is raised unchanged. Anything else raised while the
    document is loaded becomes a ``ConstructorError`` — the class PyYAML gives a value it
    cannot build where it checks, such as ``!!binary`` that is not base64. The error names
    the class that was raised and nothing of its message, which may quote the document.
    Running out of memory says nothing about the document and is not converted.
    """
    try:
        return yaml.safe_load(text)
    except (yaml.YAMLError, MemoryError):
        raise
    except Exception as exc:
        problem = f"{type(exc).__name__} while the document was built"
        raise ConstructorError(None, None, problem, None) from exc


def unread_reason(exc: BaseException) -> str:
    """Why a file could not be read, in the one line a usage error gives whoever ran the command.

    The class of what was raised, and whatever else tells where to look: the position, for a
    text PyYAML refuses itself; what the interpreter said, for a document that could not be
    built — that error has no position, and the message is the only pointer to the value.

    This is for a line that is printed and never kept. A finding that is sealed in a report
    or a bundle names the class alone: the message of what was raised may quote the file.
    """
    name = type(exc).__name__
    if not isinstance(exc, yaml.YAMLError):
        return name
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        return f"{name} at line {mark.line + 1}, column {mark.column + 1}"
    cause = exc.__cause__
    if cause is None:
        return name
    said = " ".join(str(cause).split())
    raised = f"{type(cause).__name__}: {said}" if said else type(cause).__name__
    return f"{name} ({raised})"


def schema_reason(exc: ValidationError) -> str:
    """What a schema refuses of a document that was read, in the one line of a usage error.

    Where and why for the first refusals, in the manner ``decision validate`` reports a
    contract that fails its schema, and the number of the rest. The value a refusal is about
    is left out. A place may still be a key of the document, and a validator of the schema
    may quote a value in its reason; either may hold a line break, so each refusal is given
    in one line. A key that is empty text is given as ``""``: a place with no step is the
    root of the document.

    Like ``unread_reason``, this is for a line that is printed and never kept.
    """
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    said = []
    for error in errors[:_MAX_SCHEMA_ERRORS]:
        place = ".".join(str(step) or '""' for step in error["loc"]) or "<root>"
        said.append(" ".join(f"{place}: {error['msg']}".split()))
    if len(errors) > len(said):
        said.append(f"(+{len(errors) - len(said)} more)")
    return "; ".join(said)
