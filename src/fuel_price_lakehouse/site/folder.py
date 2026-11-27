"""The site folder: put together next to its destination, then swapped in whole.

A visitor, or the step that deploys the site, must find the previous site or
the new one, never a mixture of the two and never nothing. So the site is built
in a folder beside the destination and only renamed into place once every file
is written; whatever fails before or during that rename leaves the destination
exactly as it was.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fuel_price_lakehouse.site.contract import SiteBuildError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
"""The dashboard itself (HTML, CSS, JavaScript), copied as it is."""

DATA = "data"
DOWNLOADS = "downloads"
GENERATED_FOLDERS = (DATA, DOWNLOADS)
"""Folders of the site that only the builder fills."""

SITE_MARKER = Path(DATA) / "meta.json"
"""A file every site built here has. A folder without it is not replaced."""


@contextmanager
def replacing(out: Path) -> Iterator[Path]:
    """A new, empty folder next to ``out``, which replaces ``out`` when the block succeeds.

    If the block raises, ``out`` is untouched and the new folder is removed.
    """
    _refuse_to_replace_a_foreign_folder(out)
    workspace = out.with_name(out.name + ".building")
    shutil.rmtree(workspace, ignore_errors=True)  # left behind by a build that was killed
    workspace.mkdir(parents=True)
    try:
        yield workspace
        _swap(workspace, out)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def copy_static(site: Path) -> int:
    """Copy the dashboard into ``site`` and say how many files that was.

    ``data`` and ``downloads`` at its top are left behind: the front-end keeps
    sample files there during development, and only real numbers are published.
    """
    if not STATIC_DIR.is_dir():
        logger.warning("site: no front-end at %s, building the data files alone", STATIC_DIR)
        return 0

    def generated_here(folder: str, names: list[str]) -> Collection[str]:
        return GENERATED_FOLDERS if Path(folder) == STATIC_DIR else ()

    shutil.copytree(STATIC_DIR, site, ignore=generated_here, dirs_exist_ok=True)
    return sum(1 for path in site.rglob("*") if path.is_file())


def write_json(path: Path, payload: Any) -> int:
    """Write ``payload`` as compact JSON and return its size in bytes.

    No indentation: every visitor downloads these files. NaN is refused here
    once more, because Python would otherwise write it and no browser reads it.
    """
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.write_bytes((encoded + "\n").encode("utf-8"))


def _refuse_to_replace_a_foreign_folder(out: Path) -> None:
    """The build replaces ``out`` as a whole, so ``out`` has to be a site, or nothing yet."""
    if out.is_dir() and not any(out.iterdir()):
        return
    if out.exists() and not (out / SITE_MARKER).is_file():
        raise SiteBuildError(
            f"{out} exists and is not a site built here (no {SITE_MARKER}): not replacing it"
        )


def _swap(built: Path, out: Path) -> None:
    """Put the finished site in place of the previous one.

    A folder cannot be renamed over another one, so the previous site steps
    aside first, and steps back if the new one cannot be moved in. Both folders
    are next to ``out``, on the same filesystem, so each step is one rename.
    """
    replaced = out.with_name(out.name + ".replaced")
    shutil.rmtree(replaced, ignore_errors=True)
    if out.exists():
        out.rename(replaced)
    try:
        built.rename(out)
    except OSError:
        if replaced.exists():
            replaced.rename(out)
        raise
    shutil.rmtree(replaced, ignore_errors=True)
