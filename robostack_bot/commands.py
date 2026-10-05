"""robostack-bot commands.

Every command works on a distribution checkout (`repo`), changes files in place
and returns a `Result`. Opening the pull request (or posting a comment) is left
to the calling workflow, so the commands can be run and tested locally.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import template as tpl

# Commands a distribution can run (Actions > Run workflow, command issues and
# `@robostack-bot <command>` comments), with what they do.
COMMAND_HELP = {
    "update-from-template": "update this repository to the latest template version (copier update + pixi lock)",
    "update-rosdistro-snapshot": "refresh rosdistro_snapshot.yaml to the latest rosdistro release",
    "find-stale-packages": "list published packages built against pins that no longer match",
    "check-template-drift": "list template-owned files that were edited by hand in this repository",
    "upstream-to-template": "move hand edits of template-owned files into the template (PR there)",
}
COMMANDS = tuple(COMMAND_HELP)


@dataclass
class Result:
    title: str
    summary: str
    changed: bool = False  # working tree changed, a PR should be opened
    ok: bool = True  # False marks a failure that needs a human (labels the PR / fails the job)
    labels: list[str] = field(default_factory=list)


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def changed_files(repo: Path) -> list[str]:
    out = run(["git", "status", "--porcelain", "--untracked-files=all"], repo).stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


def tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def channel_url(answers: dict, for_repodata: bool = False) -> str:
    """Same rule as copier.yml's computed `channel_url`."""
    distro = answers["distro"]
    name = answers.get("channel_name") or f"robostack-{distro}"
    if answers.get("upload_target", "prefix") == "prefix":
        # repo.prefix.dev serves repodata directly (prefix.dev redirects).
        return f"https://{'repo.' if for_repodata else ''}prefix.dev/{name}"
    return f"https://conda.anaconda.org/{name}"


# --------------------------------------------------------------------------- #
# update-from-template: template -> distribution
# --------------------------------------------------------------------------- #
def rerender(repo: Path, vcs_ref: str | None = None) -> Result:
    before = tpl.read_answers(repo).get("_commit")
    cmd = ["copier", "update", "--trust", "--defaults", "--conflict", "rej"]
    if vcs_ref:
        cmd += ["--vcs-ref", vcs_ref]
    proc = run(cmd, repo, check=False)
    if proc.returncode != 0:
        return Result("Template update failed", f"```\n{tail(proc.stdout + proc.stderr)}\n```", ok=False)
    files = changed_files(repo)
    if "pixi.toml" in files:
        run(["pixi", "lock"], repo)
    deps_ok, deps_report = True, []
    if "vinca_pinning.yaml" in files:
        # The pinning is shared; each distribution renders its own
        # conda_build_config.yaml and checks it against its own recipes.
        run(["pixi", "run", "vinca-pinning-render"], repo)
        deps_ok, deps_report = check_deps(repo)
    files = changed_files(repo)
    after = tpl.read_answers(repo).get("_commit")
    rejects = [f for f in files if f.endswith(".rej")]
    lines = [f"Re-rendered from the template: `{before}` → `{after}`.", ""]
    lines += [f"- `{f}`" for f in files] or ["No changes."]
    lines += deps_report
    if rejects:
        lines += [
            "",
            "**Conflicts:** these template-owned files were edited in this repository, "
            "so the update could not be merged automatically. Resolve the `.rej` files "
            "(and consider `@robostack-bot upstream-to-template` to move the edit into the template):",
        ] + [f"- `{f}`" for f in rejects]
    labels = (["template-conflict"] if rejects else []) + ([] if deps_ok else ["pinning-conflict"])
    return Result(
        f"Update to template {after}",
        "\n".join(lines),
        changed=bool(files),
        ok=not rejects and deps_ok,
        labels=labels,
    )


def check_deps(repo: Path) -> tuple[bool, list[str]]:
    """`pixi run check-deps` (pin conflicts against this distribution's recipes)."""
    deps = run(["pixi", "run", "check-deps"], repo, check=False)
    ok = deps.returncode == 0
    return ok, [
        "",
        f"### `pixi run check-deps`: {'no conflicts' if ok else 'conflicts found'}",
        "",
        "```",
        tail(deps.stdout + deps.stderr, 120),
        "```",
    ]


# --------------------------------------------------------------------------- #
# check-template-drift: hand edits of template-owned files
# --------------------------------------------------------------------------- #
def drift(repo: Path, template_src: str | None = None) -> Result:
    found = tpl.drift(repo, template_src)
    if not found:
        return Result("No template drift", "All template-owned files match the template.")
    lines = [
        "These files are owned by the template but differ from it. Changes to them are "
        "overwritten by the next template update; move them into the template instead "
        "(comment `@robostack-bot upstream-to-template`).",
        "",
    ]
    for d in found:
        lines.append(f"<details><summary><code>{d.path}</code> ({d.status})</summary>\n")
        lines.append(f"```diff\n{d.diff}\n```\n</details>" if d.diff else "</details>")
    return Result("Template drift", "\n".join(lines), ok=False, labels=["upstream-to-template"])


# --------------------------------------------------------------------------- #
# upstream-to-template: distribution -> template (best effort)
# --------------------------------------------------------------------------- #
def _template_source(template_dir: Path, rel: str, answers: dict) -> Path | None:
    """The file in template/ that renders to `rel`."""
    root = template_dir / "template"
    for src in root.rglob("*"):
        if not src.is_file():
            continue
        name = src.relative_to(root).as_posix()
        if name.endswith(".jinja"):
            name = name[: -len(".jinja")]
        name = re.sub(r"\[=\s*(\w+)\s*=\]", lambda m: str(answers.get(m.group(1), m.group(0))), name)
        if name == rel:
            return src
    return None


def upstream(repo: Path, template_dir: Path) -> Result:
    """Apply a distribution's edits of template-owned files to a template checkout.

    Plain files are copied over. For .jinja files the rendered->edited diff is
    applied with `patch`, which works whenever the edit doesn't touch templated
    lines; otherwise the file is reported for a manual port.
    """
    answers = tpl.read_answers(repo)
    found = tpl.drift(repo, str(template_dir))
    applied, manual = [], []
    for d in found:
        if d.status != "modified":
            continue
        src = _template_source(template_dir, d.path, answers)
        if src is None:
            manual.append((d.path, "no template source found"))
        elif src.suffix != ".jinja":
            shutil.copyfile(repo / d.path, src)
            applied.append(d.path)
        else:
            proc = subprocess.run(
                ["patch", "--forward", "--silent", "-p1", str(src)],
                input=d.diff + "\n",
                text=True,
                capture_output=True,
            )
            if proc.returncode == 0:
                applied.append(d.path)
            else:
                for leftover in (src.with_name(src.name + ".rej"), src.with_name(src.name + ".orig")):
                    leftover.unlink(missing_ok=True)
                manual.append((d.path, "edit touches templated lines"))
    distro = answers["distro"]
    lines = [f"Template changes upstreamed from ros-{distro}.", ""]
    lines += [f"- `{p}`" for p in applied] or ["Nothing could be applied automatically."]
    if manual:
        lines += ["", "**Port by hand:**"] + [f"- `{p}`: {why}" for p, why in manual]
    return Result(
        f"Upstream template changes from ros-{distro}",
        "\n".join(lines),
        changed=bool(applied),
        ok=not manual,
    )


# --------------------------------------------------------------------------- #
# update-rosdistro-snapshot
# --------------------------------------------------------------------------- #
def _versions(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {k: str(v.get("version")) for k, v in data.items() if isinstance(v, dict)}


def snapshot_changes(old: dict[str, str], new: dict[str, str]) -> str:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    bumped = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    lines = [f"{len(bumped)} updated, {len(added)} added, {len(removed)} removed packages."]
    if bumped:
        lines += ["", "| package | old | new |", "|---|---|---|"]
        lines += [f"| {k} | {old[k]} | {new[k]} |" for k in bumped]
    if added:
        lines += ["", "Added: " + ", ".join(f"`{k}`" for k in added)]
    if removed:
        lines += ["", "Removed: " + ", ".join(f"`{k}`" for k in removed)]
    return "\n".join(lines)


def update_snapshot(repo: Path) -> Result:
    snapshot = repo / "rosdistro_snapshot.yaml"
    old = _versions(snapshot)
    proc = run(["pixi", "run", "create_snapshot"], repo, check=False)
    if proc.returncode != 0:
        return Result("Snapshot update failed", f"```\n{tail(proc.stdout + proc.stderr)}\n```", ok=False)
    new = _versions(snapshot)
    changed = old != new
    return Result(
        "Update rosdistro snapshot",
        snapshot_changes(old, new) if changed else "The snapshot is up to date.",
        changed=changed,
    )


# --------------------------------------------------------------------------- #
# update-conda-forge-pinning (template repository)
# --------------------------------------------------------------------------- #
def update_pinning(template_dir: Path, distro_dirs: list[Path]) -> Result:
    """Move the shared template/vinca_pinning.yaml to the latest conda-forge pinning.

    Runs in the template repository. Migrations are selected for the union of the
    dependencies of all distributions (their recipes are generated with vinca); the
    distributions then pick the change up through the template update PR, which
    renders their conda_build_config.yaml and runs check-deps.
    """
    from vinca import pinning  # only needed here, keeps the other commands vinca-free

    config = template_dir / "template" / "vinca_pinning.yaml"
    before = config.read_text()
    dependencies: set[str] = set()
    for distro_dir in distro_dirs:
        print(f"Collecting dependencies of {distro_dir.name}", flush=True)
        dependencies |= pinning.dependencies_from_vinca(distro_dir, pinning.DEFAULT_PLATFORMS)
    version, migrations, reports = pinning.update_pinning(config, dependencies=dependencies)
    if config.read_text() == before:
        return Result("Pinning is up to date", f"Already on conda-forge-pinning {version}.")
    lines = [
        f"Moved `template/vinca_pinning.yaml` to conda-forge-pinning `{version}`, selecting "
        f"migrations for the dependencies of {', '.join(d.name for d in distro_dirs)}.",
        "",
        "Applied migrations: " + (", ".join(f"`{m}`" for m in migrations) or "none"),
        "",
    ]
    lines += [f"- `{name}`: {report}" for name, report in reports]
    lines += [
        "",
        "After merging and releasing, every distribution gets a template update PR that "
        "re-renders its `conda_build_config.yaml` and runs `check-deps`.",
    ]
    return Result("Update conda-forge pinning", "\n".join(lines), changed=True)


# --------------------------------------------------------------------------- #
# find-stale-packages
# --------------------------------------------------------------------------- #
def check_stale(repo: Path) -> Result:
    answers = tpl.read_answers(repo)
    url = channel_url(answers, for_repodata=True)
    proc = run(
        ["pixi", "run", "python", "check_dependency_compat.py", "--stale", "--repodata", url],
        repo,
        check=False,
    )
    output = tail(proc.stdout + proc.stderr, 200)
    ok = proc.returncode == 0
    return Result(
        "Stale packages" if not ok else "No stale packages",
        f"`check_dependency_compat.py --stale --repodata {url}`\n\n```\n{output}\n```",
        ok=ok,
    )


# --------------------------------------------------------------------------- #
# new-distribution (template repository)
# --------------------------------------------------------------------------- #
# Distribution-owned files seeded from the source distribution; everything else
# (including robostack.yaml, packages-ignore.yaml and vinca_pinning.yaml) is shared.
SEEDED_FROM_SOURCE = ("vinca.yaml", "pkg_additional_info.yaml")


def _strip_build_numbers(text: str) -> str:
    """Drop per-package build_number overrides from pkg_additional_info.yaml."""
    data = yaml.safe_load(text) or {}
    for key in list(data):
        entry = data[key]
        if isinstance(entry, dict):
            entry.pop("build_number", None)
            if not entry:
                del data[key]
    return yaml.safe_dump(data, sort_keys=True)


def _seed_vinca(text: str, source: str, distro: str) -> str:
    text = re.sub(r"(?m)^ros_distro:.*$", f"ros_distro: {distro}", text)
    text = re.sub(r"(?m)^build_number:.*$", "build_number: 0", text)
    # Packages are named ros2-<pkg> (+ ros-<distro>-<pkg> compatibility packages).
    if re.search(r"(?m)^package_name_mode:", text):
        text = re.sub(r"(?m)^package_name_mode:.*$", "package_name_mode: both", text)
    else:
        text = re.sub(r"(?m)^(ros_distro:.*)$", r"\1\n\npackage_name_mode: both", text, count=1)
    return text.replace(f"robostack-{source}", f"robostack-{distro}")


def new_distro(
    distro: str,
    source_repo: Path,
    dest: Path,
    template_src: str,
    answers: dict | None = None,
    vcs_ref: str | None = None,
) -> Result:
    """Instantiate a new distribution next to an existing one.

    Infrastructure comes from the template; the package selection and metadata
    are seeded from `source_repo`. Patches are only listed, never copied: a patch
    is valid for one source version and must be ported deliberately.
    """
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty")
    source_answers = tpl.user_answers(tpl.read_answers(source_repo))
    source = source_answers["distro"]
    data = {"distro": distro, **(answers or {})}

    for name in SEEDED_FROM_SOURCE:
        src = source_repo / name
        if not src.is_file():
            continue
        text = src.read_text()
        if name == "vinca.yaml":
            text = _seed_vinca(text, source, distro)
        elif name == "pkg_additional_info.yaml":
            text = _strip_build_numbers(text)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_text(text)
    with tpl.template_checkout(template_src, vcs_ref) as tdir:
        tpl.render(tdir, data, dest, ref=vcs_ref or "HEAD")
    (dest / "rosdistro_additional_recipes.yaml").touch()
    (dest / "patch").mkdir(exist_ok=True)

    patches = sorted(p.name for p in (source_repo / "patch").glob("*.patch"))
    lines = [
        f"Created `{dest}` for `{distro}` from the template, seeded from ros-{source}.",
        "",
        "Next steps:",
        "- [ ] `pixi run create_snapshot`",
        "- [ ] `pixi run vinca-pinning-render` and `pixi run check-deps`",
        "- [ ] review `vinca.yaml` (mutex name/version, `build_number: 0`, package selection)",
        "- [ ] create the channel and the `ANACONDA_API_TOKEN`/prefix.dev trusted publisher and `GHA_PAT`/bot app secrets",
        f"- [ ] port patches that still apply ({len(patches)} candidates in ros-{source}/patch, "
        "check with `pixi run check-patches`)",
    ]
    return Result(f"New distribution ros-{distro}", "\n".join(lines), changed=True)


# --------------------------------------------------------------------------- #
# @robostack-bot comments
# --------------------------------------------------------------------------- #
ALLOWED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
_MENTION = re.compile(r"^\s*@robostack-bot,?\s+(?:please\s+)?([a-z-]+)\b", re.IGNORECASE | re.MULTILINE)


_ISSUE_FORM = re.compile(r"^###\s*Command\s*\n+\s*([a-z-]+)", re.IGNORECASE | re.MULTILINE)


def parse_comment(body: str, association: str) -> str | None:
    """The command requested by a comment (`@robostack-bot <command>`) or by the
    "robostack-bot command" issue form, if the author may run it."""
    if association.upper() not in ALLOWED_ASSOCIATIONS:
        return None
    match = _MENTION.search(body or "") or _ISSUE_FORM.search(body or "")
    if not match:
        return None
    command = match.group(1).lower()
    return command if command in COMMANDS else None
