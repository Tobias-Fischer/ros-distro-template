"""robostack-bot command line.

    robostack-bot rerender         [--repo .] [--vcs-ref TAG]
    robostack-bot drift            [--repo .] [--template SRC]
    robostack-bot upstream         --template-dir DIR [--repo .]
    robostack-bot update-snapshot  [--repo .]
    robostack-bot update-pinning   --template-dir DIR DISTRO_DIR...   (template repository)
    robostack-bot check-stale      [--repo .]
    robostack-bot new-distro NAME  --from DIR --dest DIR [--template SRC] [--set key=value ...]
    robostack-bot parse-comment    --body TEXT --association ROLE

Results are printed and, with --summary FILE, written as markdown for the PR
body. In GitHub Actions the outputs `title`, `changed`, `ok` and `labels` are set.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import commands

DEFAULT_TEMPLATE = "https://github.com/RoboStack/ros-distro-template.git"


def _set_outputs(result: commands.Result) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a") as fh:
        fh.write(f"title={result.title}\n")
        fh.write(f"changed={'true' if result.changed else 'false'}\n")
        fh.write(f"ok={'true' if result.ok else 'false'}\n")
        fh.write(f"labels={','.join(result.labels)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robostack-bot", description=__doc__.splitlines()[0])
    parser.add_argument("--summary", type=Path, help="write the markdown summary to this file")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, **kw) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kw)
        if name not in ("new-distro", "parse-comment"):
            p.add_argument("--repo", type=Path, default=Path("."))
        return p

    add("rerender", help="update from the template (copier update)").add_argument("--vcs-ref")
    add("drift", help="report hand edits of template-owned files").add_argument("--template")
    add("upstream", help="apply hand edits of template-owned files to a template checkout").add_argument(
        "--template-dir", type=Path, required=True
    )
    add("update-snapshot", help="refresh rosdistro_snapshot.yaml")
    up = sub.add_parser("update-pinning", help="move the shared pinning to the latest conda-forge pinning")
    up.add_argument("--template-dir", type=Path, default=Path("."))
    up.add_argument("distros", nargs="+", type=Path, help="checkouts of all distributions")
    add("check-stale", help="list published packages built against outdated pins")
    nd = add("new-distro", help="instantiate a new distribution")
    nd.add_argument("name")
    nd.add_argument("--from", dest="source", type=Path, required=True, help="checkout of an existing distribution")
    nd.add_argument("--dest", type=Path, required=True)
    nd.add_argument("--template", default=DEFAULT_TEMPLATE)
    nd.add_argument("--vcs-ref")
    nd.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="extra copier answers")
    pc = add("parse-comment", help="print the command requested by an @robostack-bot comment")
    pc.add_argument("--body", required=True)
    pc.add_argument("--association", required=True)

    args = parser.parse_args(argv)

    if args.command == "parse-comment":
        command = commands.parse_comment(args.body, args.association)
        print(command or "")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as fh:
                fh.write(f"command={command or ''}\n")
        return 0

    if args.command == "rerender":
        result = commands.rerender(args.repo.resolve(), args.vcs_ref)
    elif args.command == "drift":
        result = commands.drift(args.repo.resolve(), args.template)
    elif args.command == "upstream":
        result = commands.upstream(args.repo.resolve(), args.template_dir.resolve())
    elif args.command == "update-snapshot":
        result = commands.update_snapshot(args.repo.resolve())
    elif args.command == "update-pinning":
        result = commands.update_pinning(args.template_dir.resolve(), [d.resolve() for d in args.distros])
    elif args.command == "check-stale":
        result = commands.check_stale(args.repo.resolve())
    else:  # new-distro
        extra = dict(kv.split("=", 1) for kv in args.set)
        result = commands.new_distro(
            args.name, args.source.resolve(), args.dest.resolve(), args.template, extra, args.vcs_ref
        )

    print(f"# {result.title}\n\n{result.summary}")
    if args.summary:
        args.summary.write_text(result.summary + "\n\n🤖 robostack-bot\n")
    _set_outputs(result)
    # drift, check-stale and upstream report findings via `ok`; only real
    # failures (rerender, snapshot, pinning) fail the process.
    return 0 if result.ok or args.command in ("drift", "check-stale", "upstream") else 1


if __name__ == "__main__":
    sys.exit(main())
