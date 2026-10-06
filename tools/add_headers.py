"""Add (or refresh) the "generated from the template" header of every template-owned file.

    python tools/add_headers.py           # update template/ in place
    python tools/add_headers.py --check   # exit 1 if a header is missing or outdated

The header names the template source, so someone about to edit the file in a
distribution knows to change the template instead. Seeded, distribution-owned
files (copier.yml `_skip_if_exists`), LICENSE and copier's answers file get none.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"
MARKER = "generated from ros-distro-template"
EXEMPT = {"LICENSE", "[= _copier_conf.answers_file =].jinja"}


def header_lines(source: str) -> list[str]:
    return [
        f"This file is {MARKER} (template/{source}).",
        "If you change it here, upstream the change: comment `@robostack-bot upstream-to-template` on your PR.",
    ]


def comment(path: Path, lines: list[str]) -> list[str]:
    name = path.name.removesuffix(".jinja")
    suffix = Path(name).suffix
    if suffix == ".md":
        return ["<!--", *lines, "-->"]
    if suffix == ".bat":
        return [f":: {line}" for line in lines]
    if suffix in (".cpp", ".hpp", ".c", ".h"):
        return [f"// {line}" for line in lines]
    return [f"# {line}" for line in lines]


def with_header(path: Path, text: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    # drop an existing header (and the blank line after it)
    for i, line in enumerate(lines[:6]):
        if MARKER in line:
            markdown = i > 0 and lines[i - 1].strip() == "<!--"
            start = i - 1 if markdown else i
            end = i + 2 + (1 if markdown else 0)  # marker line, upstream line, "-->"
            if end < len(lines) and not lines[end].strip():
                end += 1
            lines = lines[:start] + lines[end:]
            break
    source = path.relative_to(TEMPLATE).as_posix()
    block = comment(path, header_lines(source)) + [""]
    at = 1 if lines and lines[0].startswith("#!") else 0
    return newline.join(lines[:at] + block + lines[at:])


def template_owned(path: Path, seeded: set[str]) -> bool:
    rel = path.relative_to(TEMPLATE).as_posix()
    return path.is_file() and rel not in EXEMPT and rel.removesuffix(".jinja") not in seeded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    seeded = set(yaml.safe_load((ROOT / "copier.yml").read_text()).get("_skip_if_exists") or [])
    stale = []
    for path in sorted(TEMPLATE.rglob("*")):
        if not template_owned(path, seeded):
            continue
        text = path.read_bytes().decode()
        new = with_header(path, text)
        if new != text:
            stale.append(path.relative_to(ROOT).as_posix())
            if not args.check:
                path.write_bytes(new.encode())
    for p in stale:
        print(("missing/outdated header: " if args.check else "updated: ") + p)
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
