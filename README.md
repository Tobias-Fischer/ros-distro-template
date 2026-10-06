# ros-distro-template

The shared infrastructure of the RoboStack ROS 2 distribution repositories
(ros-rolling, ros-humble, ros-jazzy, ros-kilted, ros-lyrical, …) as a
[copier](https://copier.readthedocs.io) template, plus **robostack-bot**, which
keeps the distributions up to date.

> **Status:** under review at
> [Tobias-Fischer/ros-distro-template](https://github.com/Tobias-Fischer/ros-distro-template),
> to be moved to RoboStack. Moving it means changing `template_repo` in
> `copier.yml` (and `DEFAULT_TEMPLATE` in `robostack_bot/cli.py`) and releasing.

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
differences are `[% if distro ... %]` blocks, e.g. the humble-only win-64
workaround in `tests/ros2-robot-state-publisher.yaml.jinja`. `robostack.yaml` was
merged from the five distributions with `tools/merge_conda_index.py`.

Files fall in two groups:

- **template-owned**: everything rendered from `template/`. Never edit these in a
  distribution: the next update overwrites them, and the bot flags such edits on
  PRs (`upstream-to-template` label). Change them here instead.
- **distribution-owned**: `vinca.yaml`, `pkg_additional_info.yaml`,
  `rosdistro_additional_recipes.yaml`, `ci.yaml` (seeded once, see
  `_skip_if_exists` in `copier.yml`), `patch/`, and the generated
  `rosdistro_snapshot.yaml`, `conda_build_config.yaml`, `pixi.lock`.

Temporary PR-build controls that used to be edited into `testpr.yml` (full rebuild,
cache evictions) now live in the distribution-owned `ci.yaml`
([template/ci.yaml](template/ci.yaml) has the defaults and examples).

All distributions use `package_name_mode: both` in `vinca.yaml`, so packages are
named `ros2-<pkg>` (plus `ros-<distro>-<pkg>` compatibility packages), and the
shared tests (`tests/ros2-<pkg>.yaml`) and examples use the `ros2-` names.

### Template syntax

Only files ending in `.jinja` are rendered. Jinja uses `[= var =]` for variables and
`[% if distro == "rolling" %]…[% endif %]` for blocks, so GitHub Actions
(`${{ … }}`), pixi task arguments (`{{ PACKAGE }}`) and bash (`[[ … ]]`) are written
verbatim. File names can be templated too (`[= _copier_conf.answers_file =].jinja`).
Prefer one unified file over a conditional; add a conditional only for a real
difference between distributions.

> `template/.gitignore` ignores `*.sh`/`*.bat`, so new scripts under
> `template/.scripts/` have to be added with `git add -f`, and renders of a dirty
> working tree (copier copies uncommitted changes with `git add -A`) leave them
> out: commit before `pixi run render-all`.

## How changes flow between the template and the distributions

Every distribution records in `.copier-answers.yml` which template version it was
rendered from (`_commit`) and with which answers. Both directions work by
re-rendering that recorded version and comparing.

### Template → distributions

1. Edit `template/`, then `pixi run test` and `pixi run render-all` (renders every
   distribution in `distros.yaml` and diffs it against `../ros-<distro>`; CI does the
   same against each repository's `main`, so a template PR shows exactly what each
   distribution will receive).
2. Merge and publish a release, e.g. `v1.3`. `propagate.yml` then runs
   `robostack-bot update-from-template --vcs-ref v1.3` in every distribution and opens
   (or refreshes) a `bot/template-update` PR there. Run it manually with `dry_run` to
   only get the diffs as artifacts.

Example: you change `rattler-build = ">=0.57.0,<0.58"` to `"<0.59"` in
`template/pixi.toml.jinja` and release `v1.3`. In ros-jazzy (at `_commit: v1.2`),
copier renders `v1.2` and `v1.3` with jazzy's answers. The two renders differ only in
that line, so only that line changes in ros-jazzy; distribution-owned files
(`vinca.yaml`, `patch/`, `ci.yaml`, …) are never touched. `_commit` becomes `v1.3`,
the bot runs `pixi lock`, and ros-jazzy gets a PR "Update to template v1.3". Each
distribution merges its PR when it is ready (e.g. a pinning change together with a
rebuild).

If a distribution had edited the changed lines by hand, copier can't merge them:
the PR then contains `*.rej` files and the `template-conflict` label.

### Distribution → template

Template-owned files should not be edited in a distribution, but it happens (a
quick CI fix in a PR, say). Such edits are moved into the template instead of being
lost:

1. `check-template-drift` runs on every PR in a distribution: it renders the
   template at the recorded `_commit` with the distribution's answers and compares
   it with the template-owned files. Any difference is a hand edit; the PR gets the
   `upstream-to-template` label and the diff in the job summary.
2. A maintainer comments `@robostack-bot upstream-to-template`. The bot finds the
   template source of each edited file and applies the edit there:
   - plain files (e.g. `build_gap_report.py`) are copied over 1:1;
   - for `.jinja` files the rendered→edited diff is applied to the `.jinja` source
     with `patch`, which works whenever the edit doesn't touch a templated line;
     otherwise the file is listed under "port by hand".
3. The bot opens a PR in this repository. After the next release every distribution
   gets the change, and the originating distribution's update PR is empty for it.

Example: a ros-jazzy PR adds a retry loop to `.github/workflows/testpr.yml`. The
drift check flags it, `@robostack-bot upstream-to-template` applies the same lines to
`template/.github/workflows/testpr.yml.jinja` (they contain no `[= … =]`), and the
template PR shows the retry loop. Had the edit changed the line
`-c https://conda.anaconda.org/robostack-jazzy` (rendered from `-c [= channel_url =]`),
the bot would ask for a manual port instead of baking jazzy's channel into the
template.

Both directions are covered by `tests/test_template.py` (`BotTest`).

## robostack-bot

`robostack_bot/` is a small CLI (`pixi run robostack-bot --help`) that the
workflows call; every command can also be run locally.

In a distribution (Actions › *robostack-bot* › Run workflow, an issue from the
*robostack-bot command* issue template, or a comment `@robostack-bot <command>` by an
owner, member or collaborator):

| command | what it does |
|---|---|
| `update-from-template [--vcs-ref TAG]` | `copier update` to the latest (or given) template version, then `pixi lock`; if the shared pinning changed, also `vinca-pinning-render` and `check-deps` (result in the PR, `pinning-conflict` label on conflicts). Opens a PR. |
| `update-rosdistro-snapshot` | `pixi run create_snapshot`; the PR lists the version bumps. Runs weekly. |
| `find-stale-packages` | `check_dependency_compat.py --stale` against the published channel: packages built against outdated pins, with the build-number snippet to rebuild them. |
| `check-template-drift` | Hand edits of template-owned files (runs on every PR). |
| `upstream-to-template` | Apply those hand edits to the template and open a PR here. |

In this repository:

| command | what it does |
|---|---|
| `update-conda-forge-pinning DISTRO_DIR...` | Move the shared `template/vinca_pinning.yaml` to the latest conda-forge pinning, with migrations selected for the dependencies of all distributions. Runs weekly (`update-conda-forge-pinning.yml`) and opens a PR here. |
| `new-distribution NAME --from DIR --dest DIR` | Instantiate a new distribution (see below). |

PRs are opened with the robostack-bot GitHub App (`vars.ROBOSTACK_BOT_APP_ID`,
`secrets.ROBOSTACK_BOT_PRIVATE_KEY`; `secrets.GHA_PAT` as a fallback) so that CI
runs on them. Without either, the workflows fall back to `GITHUB_TOKEN`, which
needs "Allow GitHub Actions to create and approve pull requests", doesn't trigger
CI on the PRs and can't push to the template repository.

### Setting up the bot app

1. Create a GitHub App (org or user settings › Developer settings › GitHub Apps),
   webhook inactive, with repository permissions **Contents**, **Pull requests**,
   **Issues** and **Workflows**: read and write (Workflows is needed because template
   updates change `.github/workflows/`). For `new-distribution` with `create_repo`
   it also needs **Administration: read and write**, and must live in an
   organisation: GitHub Apps can't create repositories in personal accounts.
2. Install it on the template repository and every distribution repository.
3. Set `ROBOSTACK_BOT_APP_ID` (variable) and `ROBOSTACK_BOT_PRIVATE_KEY` (secret, the
   `.pem`) in each of those repositories.

Note that GitHub runs `bot.yml` from the default branch for comments, issues and
*Run workflow*: changes to the bot workflow itself take effect once the template
update PR is merged.

## New distribution

*Actions › New distribution* (`new-distribution.yml`): name, the distribution to seed from, upload target.
The bot renders the template, seeds `vinca.yaml` (new `ros_distro`,
`build_number: 0`) and `pkg_additional_info.yaml` (without build-number overrides)
from the source,
generates the snapshot and `conda_build_config.yaml`, and either uploads the result or creates
`RoboStack/ros-<name>` with a checklist issue (channel, secrets, mutex, patches to
port). Locally:

```bash
pixi run robostack-bot new-distribution macaroni --from ../ros-rolling --dest ../ros-macaroni --template .
```

## Adopting the template in an existing distribution

```bash
cd ros-<distro>
pixi exec copier copy --trust --overwrite --data-file <answers.yml> gh:Tobias-Fischer/ros-distro-template .
# move the temporary rebuild controls from the old testpr.yml into ci.yaml,
# and delete the old tests/ros-<distro>-*.yaml (replaced by tests/ros2-*.yaml)
pixi lock
git add -A && git add -f .scripts
```

The answers for the existing distributions are in `distros.yaml`.
