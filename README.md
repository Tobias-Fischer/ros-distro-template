# ros-distro-template

The shared infrastructure of the RoboStack ROS 2 distribution repositories
(ros-rolling, ros-humble, ros-jazzy, ros-kilted, ros-lyrical, …) as a
[copier](https://copier.readthedocs.io) template, plus **robostack-bot**, which
keeps the distributions up to date.

## How it works

Each distribution is an instance of `template/`, rendered with a handful of answers
(`.copier-answers.yml` in the distribution):

| answer | meaning | example |
|---|---|---|
| `distro` | rosdistro name | `jazzy` |
| `channel_name` | channel the packages are published to | `robostack-jazzy` (humble: `robostack-staging`) |
| `upload_target` | `prefix` (prefix.dev) or `anaconda` (anaconda.org) | `anaconda` |

Everything else is shared: CI workflows, build scripts, `pixi.toml` (including the
vinca version), README, AGENTS.md, the Python tools, `robostack.yaml`,
`packages-ignore.yaml`, `vinca_pinning.yaml` and the smoke tests in `tests/`
(named `ros2-<pkg>.yaml`, which vinca matches for any distribution). The few real
differences are `[% if distro ... %]` blocks, e.g. in `robostack.yaml`
(`tools/merge_conda_index.py` built it from the five distributions).

Files fall in two groups:

- **template-owned**: everything rendered from `template/`. Never edit these in a
  distribution: the next update overwrites them, and the bot flags such edits on
  PRs (`upstream-to-template` label). Change them here instead.
- **distribution-owned**: `vinca.yaml`, `pkg_additional_info.yaml`,
  `rosdistro_additional_recipes.yaml`, `ci.yaml` (seeded once, see
  `_skip_if_exists` in `copier.yml`), `patch/`, and the generated
  `rosdistro_snapshot.yaml`, `conda_build_config.yaml`, `pixi.lock`.

Temporary PR-build controls that used to be edited into `testpr.yml` (full rebuild,
cache evictions) now live in the distribution-owned `ci.yaml`.

### Template syntax

Only files ending in `.jinja` are rendered. Jinja uses `[= var =]` for variables and
`[% if distro == "rolling" %]…[% endif %]` for blocks, so GitHub Actions
(`${{ … }}`), pixi task arguments (`{{ PACKAGE }}`) and bash (`[[ … ]]`) are written
verbatim. File names can be templated too (`tests/ros-[= distro =]-rclpy.yaml.jinja`).
Prefer one unified file over a conditional; add a conditional only for a real
difference between distributions.

> `template/.gitignore` ignores `*.sh`/`*.bat`, so new scripts under
> `template/.scripts/` have to be added with `git add -f`, and renders of a dirty
> working tree (copier copies uncommitted changes with `git add -A`) leave them
> out: commit before `pixi run render-all`.

## Changing the template

1. Edit `template/`, then `pixi run test` and `pixi run render-all` (renders every
   distribution in `distros.yaml` and diffs it against `../ros-<distro>`; CI does the
   same against each repository's `main`).
2. Merge, then publish a release. `propagate.yml` runs `copier update` in every
   distribution and opens (or refreshes) a `bot/template-update` PR there. Run it
   manually with `dry_run` to only get the diffs as artifacts.

Edits made in a distribution can flow back: comment `@robostack-bot upstream` on a
PR/issue there, and the bot opens a PR here with the edit applied to `template/`
(automatically when the edit doesn't touch templated lines).

## robostack-bot

`robostack_bot/` is a small CLI (`pixi run robostack-bot --help`) that the
workflows call; every command can also be run locally in a distribution checkout:

| command | what it does |
|---|---|
| `rerender [--vcs-ref TAG]` | `copier update` + `pixi lock` |
| `drift` | report hand edits of template-owned files |
| `upstream --template-dir DIR` | apply those edits to a template checkout |
| `update-snapshot` | `pixi run create_snapshot`, summarise the version bumps |
| `update-pinning DISTRO_DIR...` | (template repository) move the shared `vinca_pinning.yaml` to the latest conda-forge pinning, with migrations selected for all distributions |
| `check-stale` | `check_dependency_compat.py --stale` against the published channel |
| `new-distro NAME --from DIR --dest DIR` | instantiate a new distribution |

In each distribution, `.github/workflows/bot.yml` (template-owned) runs
`update-snapshot` weekly, any command from *Actions › Run workflow*, from an issue
opened with the *robostack-bot command* issue template, or from a comment
`@robostack-bot <command>` by an owner, member or collaborator, and the drift check
on every PR. `update-pinning.yml` in this repository updates the shared pinning
weekly; when a template update changes `vinca_pinning.yaml`, `rerender` also runs
`vinca-pinning-render` and `check-deps` in the distribution and reports the result
in its PR. PRs are opened with the
robostack-bot GitHub App (`vars.ROBOSTACK_BOT_APP_ID`,
`secrets.ROBOSTACK_BOT_PRIVATE_KEY`; `secrets.GHA_PAT` as a fallback) so that CI
runs on them.

## New distribution

*Actions › New distribution*: name, the distribution to seed from, upload target.
The bot renders the template, seeds `vinca.yaml` (new `ros_distro`,
`build_number: 0`) and `pkg_additional_info.yaml` (without build-number overrides)
from the source,
generates the snapshot and `conda_build_config.yaml`, and either uploads the result or creates
`RoboStack/ros-<name>` with a checklist issue (channel, secrets, mutex, patches to
port). Locally:

```bash
pixi run robostack-bot new-distro macaroni --from ../ros-rolling --dest ../ros-macaroni --template .
```

## Adopting the template in an existing distribution

```bash
cd ros-<distro>
pixi exec copier copy --trust --overwrite --data-file <answers.yml> gh:RoboStack/ros-distro-template .
# move the temporary rebuild controls from the old testpr.yml into ci.yaml,
# and delete the old tests/ros-<distro>-*.yaml (replaced by tests/ros2-*.yaml)
pixi lock
git add -A && git add -f .scripts
```

The answers for the existing distributions are in `distros.yaml`.
