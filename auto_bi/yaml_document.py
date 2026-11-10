"""One YAML document from text this program did not write.

PyYAML answers a text it cannot scan, parse or compose with a ``yaml.YAMLError``. Building
the values of a composed document is plain Python, and what that raises leaves
``yaml.safe_load`` as it is: a date that does not exist or an integer longer than the
interpreter converts is a ``ValueError``, an explicit ``!!bool`` the tag does not describe a
``KeyError``, an explicit ``!!timestamp`` that is not one an ``AttributeError``, a document
nested deeper than the interpreter's stack a ``RecursionError``. A reader that grades an
unreadable file by catching ``yaml.YAMLError`` stops on every one of those, and the author
of the file is shown a traceback of this program instead of a finding about the file.

One text PyYAML builds and this module refuses: an escape of a double-quoted scalar that
names a surrogate. A surrogate is half of the UTF-16 form of a character and no character
by itself: libyaml — the scanner PyYAML is also shipped with — refuses the escape, and the
scanner ``yaml.safe_load`` runs builds a string of it that no UTF-8 file and no canonical
JSON can hold. What became of such a string was up to whoever read it next: a schema that
took it, a digest that had no form for it, a report that could not be printed.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError

#: How many refusals of a schema a usage error spells out before it counts the rest.
_MAX_SCHEMA_ERRORS = 3


class SurrogateEscapeError(yaml.MarkedYAMLError):
    """A double-quoted scalar with an escape that names a surrogate.

    The mark is where the scalar begins: the scanner has replaced the escape by then, and
    the place of the escape inside the scalar is not kept. Two escapes that JSON reads as one
    character — a high surrogate and a low one after it — are refused like any other: PyYAML
    builds two surrogates of them, not the character.
    """


def load_yaml_document(text: str) -> Any:
    """What ``yaml.safe_load`` builds from *text*; a document it cannot build is a ``YAMLError``.

    A failure PyYAML reports itself is raised unchanged. A document it composes with a
    scalar that holds a surrogate is a ``SurrogateEscapeError`` before anything of it is
    built. Anything else raised while the document is loaded becomes a ``ConstructorError``
    — the class PyYAML gives a value it cannot build where it checks, such as ``!!binary``
    that is not base64. The error names the class that was raised and nothing of its
    message, which may quote the document. Running out of memory says nothing about the
    document and is not converted.
    """
    try:
        loader = yaml.SafeLoader(text)
        try:
            node = loader.get_single_node()
            if node is None:
                return None
            _refuse_surrogates(node)
            return loader.construct_document(node)
        finally:
            loader.dispose()
    except (yaml.YAMLError, MemoryError):
        raise
    except Exception as exc:
        problem = f"{type(exc).__name__} while the document was built"
        raise ConstructorError(None, None, problem, None) from exc


def _refuse_surrogates(document: yaml.Node) -> None:
    """Raise for the first scalar of a composed document that holds a surrogate.

    First as the document is written: a key before its value, an entry before the next.
    A node an alias repeats is looked at where it is anchored, once — which also ends the
    walk of a node that holds itself — and nothing here calls itself, so a document that
    was composed is walked whatever its depth.
    """
    seen: set[int] = set()
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, yaml.ScalarNode):
            if _holds_surrogate(node.value):
                problem = "found an escape that names a surrogate"
                raise SurrogateEscapeError(None, None, problem, node.start_mark)
            continue
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            for key, value in reversed(node.value):
                pending.append(value)
                pending.append(key)
        elif isinstance(node, yaml.SequenceNode):
            pending.extend(reversed(node.value))


def _holds_surrogate(text: str) -> bool:
    # UTF-8 has a form for every code point a string can hold but the surrogates.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


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
