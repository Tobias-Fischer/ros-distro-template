"""Adopt the template in an existing distribution checkout, keeping it close to its main.

    python tools/adopt.py ../ros-humble [--vcs-ref v0.1.8] [--template gh:Tobias-Fischer/ros-distro-template]

- renders the template over the checkout with the distribution's answers from
  distros.yaml, plus its current conda-forge pinning version and migrations
  (from its vinca_pinning.yaml) and the pinning overrides that differ from the
  shared ones, so conda_build_config.yaml stays (nearly) as it is;
- moves the temporary rebuild controls of its testpr.yml (the full-rebuild flag and
  the "Delete specific outdated cache entries" lines) into ci.yaml;
- removes the old tests/ros-<distro>-* files (replaced by the shared tests/ros2-*);
- runs `pixi lock`.

rosdistro_snapshot.yaml, vinca.yaml, patch/ & co. are left untouched. Review and
commit the result (`git add -A && git add -f .scripts`).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from copier import run_copy

ROOT = Path(__file__).resolve().parent.parent
CI_YAML_HEADER = (ROOT / "template" / "ci.yaml").read_text()


def current_pinning(repo: Path) -> dict:
    path = repo / "vinca_pinning.yaml"
    if not path.is_file():
        return {}
    spec = yaml.safe_load(path.read_text()) or {}
    version = spec.get("conda_forge_pinning_version")
    if not version:
        return {}
    answers = {
        "conda_forge_pinning_version": str(version),
        "conda_forge_migrations": list(spec.get("migrations") or []),
    }
    # overrides whose value differs from the shared ones (or that only this
    # distribution has) become its pinning_overrides answer
    shared = (yaml.safe_load((ROOT / "pinning" / "overrides.yaml").read_text()) or {})["pinning_overrides"]
    own = spec.get("pinning_overrides") or {}
    distinct = {k: v for k, v in own.items() if k not in shared or shared[k] != v}
    if distinct:
        answers["pinning_overrides"] = distinct
    return answers


def rebuild_controls(repo: Path, distro: str) -> tuple[bool, list[str]]:
    """The full-rebuild flag and cache evictions of the old testpr.yml."""
    path = repo / ".github" / "workflows" / "testpr.yml"
    if not path.is_file():
        return False, []
    text = path.read_text()
    full = bool(re.search(r"IGNORE_CACHE_AND_DO_FULL_REBUILD:\s*'?true'?", text))
    evict = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("rm -rf ${{ matrix.folder_cache }}/") or stripped.endswith("/*"):
            continue
        for target in re.findall(r"\$\{\{ matrix\.folder_cache \}\}/(\S+)", stripped):
            name = re.sub(rf"^(ros2-|ros-{distro}-)", "", target)
            if name and name != "<package>*" and name not in evict:
                evict.append(name)
    return full, evict


def write_ci_yaml(repo: Path, full: bool, evict: list[str]) -> None:
    text = CI_YAML_HEADER
    if full:
        text = text.replace(
            "full_rebuild: false",
            "# (carried over from testpr.yml: IGNORE_CACHE_AND_DO_FULL_REBUILD 'true')\nfull_rebuild: true",
        )
    if evict:
        entries = "\n".join(f'  - "{e}"' if "*" in e else f"  - {e}" for e in evict)
        text = text.replace(
            "evict_cache: []",
            '# (carried over from the "Delete specific outdated cache entries" step of testpr.yml)\n'
            f"evict_cache:\n{entries}",
        )
    (repo / "ci.yaml").write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", type=Path)
    parser.add_argument("--distro", help="default: ros_distro from vinca.yaml")
    parser.add_argument("--template", default="gh:Tobias-Fischer/ros-distro-template")
    parser.add_argument("--vcs-ref", default=None, help="template version (default: latest tag)")
    args = parser.parse_args()

    repo = args.repo.resolve()
    distro = args.distro or yaml.safe_load((repo / "vinca.yaml").read_text())["ros_distro"]
    registry = yaml.safe_load((ROOT / "distros.yaml").read_text())
    answers = dict(registry.get(distro, {}).get("answers") or {"distro": distro})
    answers.update(current_pinning(repo))
    full, evict = rebuild_controls(repo, distro)

    print(f"Adopting ros-{distro} with answers {answers}", flush=True)
    run_copy(args.template, repo, data=answers, defaults=True, overwrite=True, unsafe=True,
             vcs_ref=args.vcs_ref, quiet=True)
    write_ci_yaml(repo, full, evict)
    old_tests = sorted((repo / "tests").glob(f"ros-{distro}-*"))
    for path in old_tests:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    print(f"ci.yaml: full_rebuild={full}, evict_cache={evict}; removed {len(old_tests)} old tests")
    subprocess.run(["pixi", "lock"], cwd=repo, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
