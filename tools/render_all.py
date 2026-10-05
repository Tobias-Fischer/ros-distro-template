"""Render the template for every distribution and diff it against the live repos.

For each entry in distros.yaml the template (including uncommitted changes) is
rendered with that distribution's answers, and every template-owned file is
compared with the distribution repository. Seeded, distribution-owned files
(copier.yml `_skip_if_exists`) are not compared.

    python tools/render_all.py                       # all distros, summary
    python tools/render_all.py rolling --diff        # one distro, full diff
    python tools/render_all.py --repos-dir ~/robot --ref origin/main
    python tools/render_all.py --check               # exit 1 on any difference

A distribution checkout is looked up as <repos-dir>/ros-<distro>; the files are
read from --ref (default: the working tree) so local branches don't matter.
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from copier import run_copy

ROOT = Path(__file__).resolve().parent.parent


def load_answers(distro: str, entry: dict, repo: Path, ref: str | None) -> dict:
    """The distro's own .copier-answers.yml wins over the bootstrap answers."""
    text = read_file(repo, ".copier-answers.yml", ref)
    if text is not None:
        answers = yaml.safe_load(text) or {}
        return {k: v for k, v in answers.items() if not k.startswith("_")}
    return dict(entry.get("answers") or {"distro": distro})


def read_file(repo: Path, path: str, ref: str | None) -> str | None:
    if ref is None:
        p = repo / path
        return p.read_text(errors="replace") if p.is_file() else None
    out = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
    )
    return out.stdout.decode(errors="replace") if out.returncode == 0 else None


def render(answers: dict, dest: Path) -> None:
    run_copy(
        str(ROOT),
        dest,
        data=answers,
        defaults=True,
        unsafe=True,
        vcs_ref="HEAD",
        quiet=True,
    )


def owned_files(rendered: Path) -> list[str]:
    config = yaml.safe_load((ROOT / "copier.yml").read_text())
    seeded = set(config.get("_skip_if_exists", []))
    files = []
    for p in sorted(rendered.rglob("*")):
        rel = p.relative_to(rendered).as_posix()
        if p.is_file() and rel not in seeded and rel != ".copier-answers.yml":
            files.append(rel)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("distros", nargs="*", help="default: all in distros.yaml")
    parser.add_argument("--repos-dir", type=Path, default=ROOT.parent)
    parser.add_argument("--ref", default=None, help="git ref to compare against (default: working tree)")
    parser.add_argument("--diff", action="store_true", help="print full diffs")
    parser.add_argument("--check", action="store_true", help="exit 1 if anything differs")
    args = parser.parse_args()

    registry = yaml.safe_load((ROOT / "distros.yaml").read_text())
    distros = args.distros or list(registry)
    differs = False
    for distro in distros:
        repo = args.repos_dir / f"ros-{distro}"
        answers = load_answers(distro, registry[distro], repo, args.ref)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / f"ros-{distro}"
            render(answers, dest)
            print(f"== {distro} ({repo}, {args.ref or 'working tree'})")
            for rel in owned_files(dest):
                new = (dest / rel).read_text(errors="replace")
                old = read_file(repo, rel, args.ref)
                if old is None:
                    print(f"   NEW      {rel}")
                    differs = True
                    continue
                if old == new:
                    continue
                diff = list(
                    difflib.unified_diff(
                        old.splitlines(), new.splitlines(),
                        f"ros-{distro}/{rel}", f"template/{rel}", lineterm="",
                    )
                )
                changed = sum(1 for l in diff if l[:1] in "+-" and not l.startswith(("+++", "---")))
                if not changed:  # only line endings / trailing newline differ
                    print(f"   EOL      {rel}")
                else:
                    print(f"   {changed:4d} ±  {rel}")
                differs = True
                if args.diff:
                    print("\n".join(diff))
    return 1 if (args.check and differs) else 0


if __name__ == "__main__":
    sys.exit(main())
