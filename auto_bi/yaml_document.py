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
from yaml.constructor import ConstructorError


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
