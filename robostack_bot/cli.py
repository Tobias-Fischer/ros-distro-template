"""robostack-bot command line.

In a distribution checkout:

    robostack-bot update-from-template       [--repo .] [--vcs-ref TAG]
    robostack-bot update-rosdistro-snapshot  [--repo .]
    robostack-bot find-stale-packages        [--repo .]
    robostack-bot check-template-drift       [--repo .] [--template SRC]
    robostack-bot upstream-to-template       --template-dir DIR [--repo .]

In the template repository:

    robostack-bot update-conda-forge-pinning --template-dir DIR DISTRO_DIR...
    robostack-bot new-distribution NAME      --from DIR --dest DIR [--template SRC] [--set key=value ...]

For the workflows:

    robostack-bot parse-command              --body TEXT --association ROLE

Results are printed and, with --summary FILE, written as markdown for the PR
body. In GitHub Actions the outputs `title`, `changed`, `ok` and `labels` are set.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import commands

DEFAULT_TEMPLATE = "https://github.com/Tobias-Fischer/ros-distro-template.git"

# Commands that only report findings: `ok == False` means "found something", not
# "failed", so they don't fail the workflow.
REPORTING = {"check-template-drift", "find-stale-packages", "upstream-to-template"}


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

    def in_distro(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument("--repo", type=Path, default=Path("."), help="distribution checkout")
        return p

    in_distro("update-from-template", commands.COMMAND_HELP["update-from-template"]).add_argument("--vcs-ref")
    in_distro("update-rosdistro-snapshot", commands.COMMAND_HELP["update-rosdistro-snapshot"])
    in_distro("find-stale-packages", commands.COMMAND_HELP["find-stale-packages"])
    in_distro("check-template-drift", commands.COMMAND_HELP["check-template-drift"]).add_argument("--template")
    in_distro("upstream-to-template", commands.COMMAND_HELP["upstream-to-template"]).add_argument(
        "--template-dir", type=Path, required=True
    )
    up = sub.add_parser(
        "update-conda-forge-pinning",
        help="move the shared template/vinca_pinning.yaml to the latest conda-forge pinning",
    )
    up.add_argument("--template-dir", type=Path, default=Path("."))
    up.add_argument("distros", nargs="+", type=Path, help="checkouts of all distributions")
    nd = sub.add_parser("new-distribution", help="instantiate a new distribution from the template")
    nd.add_argument("name")
    nd.add_argument("--from", dest="source", type=Path, required=True, help="checkout of an existing distribution")
    nd.add_argument("--dest", type=Path, required=True)
    nd.add_argument("--template", default=DEFAULT_TEMPLATE)
    nd.add_argument("--vcs-ref")
    nd.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="extra copier answers")
    pc = sub.add_parser("parse-command", help="print the command requested by a comment or command issue")
    pc.add_argument("--body", required=True)
    pc.add_argument("--association", required=True)

    args = parser.parse_args(argv)

    if args.command == "parse-command":
        command = commands.parse_comment(args.body, args.association)
        print(command or "")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as fh:
                fh.write(f"command={command or ''}\n")
        return 0

    if args.command == "update-from-template":
        result = commands.rerender(args.repo.resolve(), args.vcs_ref)
    elif args.command == "update-rosdistro-snapshot":
        result = commands.update_snapshot(args.repo.resolve())
    elif args.command == "find-stale-packages":
        result = commands.check_stale(args.repo.resolve())
    elif args.command == "check-template-drift":
        result = commands.drift(args.repo.resolve(), args.template)
    elif args.command == "upstream-to-template":
        result = commands.upstream(args.repo.resolve(), args.template_dir.resolve())
    elif args.command == "update-conda-forge-pinning":
        result = commands.update_pinning(args.template_dir.resolve(), [d.resolve() for d in args.distros])
    else:  # new-distribution
        extra = dict(kv.split("=", 1) for kv in args.set)
        result = commands.new_distro(
            args.name, args.source.resolve(), args.dest.resolve(), args.template, extra, args.vcs_ref
        )

    print(f"# {result.title}\n\n{result.summary}")
    if args.summary:
        args.summary.write_text(result.summary + "\n\n🤖 robostack-bot\n")
    _set_outputs(result)
    return 0 if result.ok or args.command in REPORTING else 1


if __name__ == "__main__":
    sys.exit(main())
