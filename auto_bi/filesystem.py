"""What the filesystem says of a path — and what it refuses to say.

Two answers of ``pathlib`` drop a refusal. ``Path.rglob`` reads a directory it cannot list as
an empty one: the ``OSError`` of the scan is dropped where it is raised, what the directory
holds is not in the result, and nothing in the result says so. And from Python 3.14 on
``Path.exists``, ``Path.is_file`` and ``Path.is_dir`` answer ``False`` to every ``OSError``,
so a path under a directory that may not be searched reads as a path that is not there; up to
3.13 the same look raised. A reader that answers for every file under a path cannot tell
either answer from the one for a path that holds nothing. The functions here ask the
operating system the same questions and keep its refusal.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path

#: What the operating system answers for a path that is not there, as ``pathlib`` read it
#: before Python 3.14: no such entry, a component that is no directory, a descriptor that is
#: none, a link that never ends. Any other error is a refusal to say.
_NOT_THERE = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})
#: The same on Windows, by ``winerror``: the device is not ready, the name has no valid form,
#: the name of a link cannot be resolved.
_NOT_THERE_ON_WINDOWS = frozenset({21, 123, 1921})


def _not_there(error: OSError) -> bool:
    return error.errno in _NOT_THERE or getattr(error, "winerror", None) in _NOT_THERE_ON_WINDOWS


def _status(path: str | os.PathLike[str]) -> os.stat_result | None:
    """What *path* leads to, links followed — ``None`` where nothing is there."""
    try:
        return os.stat(path)
    except OSError as error:
        if _not_there(error):
            return None
        raise
    except ValueError:  # a name the operating system has no form for: one that holds a NUL
        return None


def exists(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is there. A look the filesystem refuses raises its ``OSError``."""
    return _status(path) is not None


def is_file(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is a regular file. A look the filesystem refuses raises its ``OSError``."""
    status = _status(path)
    return status is not None and stat.S_ISREG(status.st_mode)


def is_dir(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is a directory. A look the filesystem refuses raises its ``OSError``."""
    status = _status(path)
    return status is not None and stat.S_ISDIR(status.st_mode)


def paths_under(
    root: str | os.PathLike[str], refused: list[OSError] | None = None
) -> Iterator[Path]:
    """Every path below *root*, as ``Path(root).rglob("*")`` gives them, in no promised order.

    A directory that is not there holds nothing, and neither does a path that is no
    directory: that is what empty means, and nothing is said of either. A directory the
    filesystem refuses to list is another matter. Its ``OSError``, which names the directory
    as its ``filename``, is raised — or, when *refused* is given, is kept there while the
    directories that can be listed are still walked. A directory that is listed while the
    filesystem will not say which of its entries are directories is refused the same way,
    once: the entries are given, and what is under them was not walked.

    A symbolic link to a directory is given and is not followed, as ``rglob`` does not follow
    one.
    """

    def refuse(error: OSError) -> None:
        if _not_there(error):
            return
        if refused is None:
            raise error
        refused.append(error)

    pending = [os.fspath(root)]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scan:
                entries = list(scan)
        except OSError as error:
            refuse(error)
            continue
        untold: OSError | None = None
        for entry in entries:
            yield Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
            except OSError as error:
                if untold is None and not _not_there(error):
                    untold = error
        if untold is not None:
            refuse(OSError(untold.errno, untold.strerror, directory))
