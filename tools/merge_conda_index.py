"""Merge the per-distribution robostack.yaml files into one shared template.

One-off migration helper (kept for re-running against newer distribution
checkouts): every rosdep key of every distribution is kept (an unused key is
harmless), identical entries are emitted once, and entries whose value differs
are emitted as a [% if distro ... %] block per group of distributions, unless
the key is listed in UNIFY, in which case the given distribution's entry wins.

A key a distribution doesn't have is only added for it if the key isn't also a ROS
package of that distribution (rosdistro_snapshot.yaml): e.g. `tl_expected` is a
rosdep key in newer distributions but a ROS package in humble, and mapping it to
conda-forge there would replace the ROS package.

    python tools/merge_conda_index.py --repos-dir .. > template/robostack.yaml

(write to robostack.yaml.jinja instead if the output contains [% %] blocks)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

DISTROS = ["rolling", "humble", "jazzy", "kilted", "lyrical"]

# Differences that are drift: take this distribution's entry for everyone.
UNIFY = {
    "google-mock": "rolling",  # adds the explicit emscripten: [] mapping
    "liblttng-ust-dev": "rolling",  # same mapping, dict form instead of a selector string
    "zbar": "rolling",  # zbar is available on all platforms nowadays
    "libdc1394-dev": "rolling",  # not on win64
    "ignition-gazebo6": "humble",  # superset (adds libgl-devel on linux), only humble uses it
    "python-pygraphviz": "jazzy",  # superset (adds graphviz)
    "python3-pygraphviz": "jazzy",
    "eigen": "humble",  # always with eigen-abi-devel
    "libpcl-all-dev": "rolling",  # with eigen-abi-devel
    "libgdal-dev": "rolling",  # libgdal-core
    "python3-vcstool": "rolling",  # vcs2l
    "qml-module-qtquick-extras": "rolling",  # qt6-main
    "xtensor": "jazzy",  # no version constraint
}

# Keys mapped to conda-forge in every distribution, even where the name is also a
# ROS package (the conda-forge package replaces the ROS/vendor package).
MAP_EVERYWHERE = {"tl_expected", "sophus"}

# Keys left out of robostack.yaml because the shared packages-ignore.yaml maps them
# to nothing (robostack.yaml is searched first, so a mapping there would win).
DROP = {
    "chrony": "system service, ignored since ros-jazzy 2026-05-16 (packages-ignore.yaml)",
}

TOP_KEY = re.compile(r"^([^\s#][^:]*):")


def blocks(text: str) -> tuple[list[str], dict[str, str]]:
    """Header lines, and the text of each top-level entry (with the comments above it)."""
    header, entries, pending, key = [], {}, [], None
    for line in text.splitlines(keepends=True):
        m = TOP_KEY.match(line)
        if m:
            key = m.group(1).strip().strip("'\"")
            entries[key] = "".join(pending) + line
            pending = []
        elif key is None:
            header.append(line)
        elif line.strip() == "" or line.startswith("#"):
            pending.append(line)
        else:
            entries[key] += "".join(pending) + line
            pending = []
    return header, entries


def normalize(block: str) -> str:
    """Blocks are equal if they parse to the same value (ignoring comments/blank lines)."""
    return yaml.safe_dump(yaml.safe_load(block), sort_keys=True)


def read(repos: Path, distro: str, ref: str | None, name: str = "robostack.yaml") -> str:
    if ref:
        return subprocess.run(
            ["git", "-C", str(repos / f"ros-{distro}"), "show", f"{ref}:{name}"],
            capture_output=True, text=True, check=True,
        ).stdout
    return (repos / f"ros-{distro}" / name).read_text()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos-dir", type=Path, default=Path(".."))
    parser.add_argument("--ref", default=None)
    args = parser.parse_args()

    parsed = {d: blocks(read(args.repos_dir, d, args.ref)) for d in DISTROS}
    ros_packages = {
        d: set(yaml.safe_load(read(args.repos_dir, d, args.ref, "rosdistro_snapshot.yaml")) or {})
        for d in DISTROS
    }
    header = parsed["rolling"][0]
    keys = sorted({k for _, e in parsed.values() for k in e}, key=str.casefold)
    out = list(header)
    for key in keys:
        if key in DROP:
            continue
        present = {d: parsed[d][1][key] for d in DISTROS if key in parsed[d][1]}
        # distributions where the key is a ROS package and must stay unmapped
        excluded = [
            d for d in DISTROS
            if d not in present and key in ros_packages[d] and key not in MAP_EVERYWHERE
        ]
        if excluded:
            out.append(f"[% if distro not in {excluded!r} %]\n")
        if key in UNIFY:
            out.append(present[UNIFY[key]].rstrip("\n") + "\n")
            if excluded:
                out.append("[% endif %]\n")
            continue
        groups: dict[str, list[str]] = {}
        for d, block in present.items():
            groups.setdefault(normalize(block), []).append(d)
        if len(groups) == 1:
            first = next(iter(present.values()))
            out.append(present.get("rolling", first).rstrip("\n") + "\n")
            if excluded:
                out.append("[% endif %]\n")
            continue
        # The group containing rolling (or the largest group) becomes the default.
        ordered = sorted(groups.values(), key=lambda ds: ("rolling" not in ds, -len(ds)))
        default, *others = ordered
        for i, ds in enumerate(others):
            kw = "if" if i == 0 else "elif"
            out.append(f"[% {kw} distro in {ds!r} %]\n" + present[ds[0]].rstrip("\n") + "\n")
        out.append("[% else %]\n" + present[default[0]].rstrip("\n") + "\n[% endif %]\n")
        if excluded:
            out.append("[% endif %]\n")
    sys.stdout.write("".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
