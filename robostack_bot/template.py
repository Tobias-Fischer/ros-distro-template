"""Rendering the template and comparing it with a distribution checkout."""

from __future__ import annotations

import contextlib
import difflib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml
from copier import run_copy

ANSWERS_FILE = ".copier-answers.yml"


def read_answers(repo: Path) -> dict:
    path = repo / ANSWERS_FILE
    if not path.is_file():
        raise SystemExit(f"{path} not found: {repo} is not instantiated from the template")
    return yaml.safe_load(path.read_text()) or {}


def user_answers(answers: dict) -> dict:
    """Answers without copier's private keys (_commit, _src_path, ...)."""
    return {k: v for k, v in answers.items() if not k.startswith("_")}


@contextlib.contextmanager
def template_checkout(src: str, ref: str | None) -> Iterator[Path]:
    """A local clone of the template at `ref` (a local path is used as-is if ref is None)."""
    local = Path(src).expanduser()
    if ref is None and local.is_dir():
        yield local
        return
    with tempfile.TemporaryDirectory(prefix="robostack-template-") as tmp:
        dest = Path(tmp) / "template"
        url = str(local) if local.is_dir() else src
        if url.startswith("gh:"):
            url = "https://github.com/" + url[3:]
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
        if ref:
            subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", ref], check=True)
        yield dest


def seeded_files(template_dir: Path) -> set[str]:
    """Files seeded once and then owned by the distribution (copier `_skip_if_exists`)."""
    config = yaml.safe_load((template_dir / "copier.yml").read_text()) or {}
    return set(config.get("_skip_if_exists") or [])


def render(template_dir: Path, answers: dict, dest: Path, ref: str | None = "HEAD") -> None:
    run_copy(
        str(template_dir),
        dest,
        data=user_answers(answers),
        vcs_ref=ref,
        defaults=True,
        unsafe=True,
        quiet=True,
    )


def owned_files(rendered: Path, seeded: set[str]) -> list[str]:
    """Template-owned files of a rendered distribution (relative posix paths)."""
    return sorted(
        p.relative_to(rendered).as_posix()
        for p in rendered.rglob("*")
        if p.is_file()
        and p.relative_to(rendered).as_posix() not in seeded
        and p.name != ANSWERS_FILE
    )


@dataclass
class FileDrift:
    path: str
    status: str  # "modified" | "missing"
    diff: str


def compare(rendered: Path, repo: Path, files: list[str]) -> list[FileDrift]:
    """Template-owned files whose content in `repo` differs from the rendering."""
    drift = []
    for rel in files:
        new = (rendered / rel).read_bytes()
        target = repo / rel
        if not target.is_file():
            drift.append(FileDrift(rel, "missing", ""))
            continue
        old = target.read_bytes()
        if old == new:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                new.decode(errors="replace").splitlines(),
                old.decode(errors="replace").splitlines(),
                f"a/{rel}",
                f"b/{rel}",
                lineterm="",
            )
        )
        drift.append(FileDrift(rel, "modified", diff))
    return drift


def drift(repo: Path, template_src: str | None = None) -> list[FileDrift]:
    """Hand edits of template-owned files relative to the recorded template version."""
    answers = read_answers(repo)
    src = template_src or answers["_src_path"]
    with template_checkout(src, answers.get("_commit")) as tdir, tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "rendered"
        render(tdir, answers, dest, ref=answers.get("_commit") or "HEAD")
        return compare(dest, repo, owned_files(dest, seeded_files(tdir)))
