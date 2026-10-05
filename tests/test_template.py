"""Tests for the template and robostack-bot (run with `pixi run test`)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from robostack_bot import commands  # noqa: E402
from robostack_bot import template as tpl  # noqa: E402

REGISTRY = yaml.safe_load((ROOT / "distros.yaml").read_text())
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@localhost",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@localhost",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "add", "-f", ".")  # the rendered .gitignore ignores *.sh/*.bat
    git(repo, "commit", "-qm", message)


class TemplateCopy:
    """A throw-away clone of this template repo (including uncommitted changes)."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "template"
        subprocess.run(["git", "clone", "-q", str(ROOT), str(path)], check=True)
        # bring over the working tree state so tests cover uncommitted edits
        changed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "-m", "-o", "--exclude-standard"],
            capture_output=True, text=True, check=True,
        ).stdout.split("\0")
        for rel in (r for r in changed if r and (ROOT / r).is_file()):
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_bytes((ROOT / rel).read_bytes())
        deleted = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "-d"], capture_output=True, text=True, check=True
        ).stdout.split("\0")
        for rel in filter(None, deleted):
            (path / rel).unlink(missing_ok=True)
        git(path, "add", "-A")
        git(path, "commit", "-qm", "working tree", "--allow-empty")
        git(path, "tag", "v-test")
        return path

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


def instantiate(template: Path, dest: Path, answers: dict) -> None:
    tpl.render(template, answers, dest, ref="HEAD")
    git(dest, "init", "-q", "-b", "main")
    commit_all(dest, "instantiate")


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._copy = TemplateCopy()
        cls.template = cls._copy.__enter__()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.rendered = {}
        for distro, entry in REGISTRY.items():
            dest = Path(cls._tmp.name) / f"ros-{distro}"
            tpl.render(cls.template, entry["answers"], dest)
            cls.rendered[distro] = dest

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        cls._copy.__exit__(None, None, None)

    def test_no_unrendered_markers(self):
        for distro, dest in self.rendered.items():
            for path in dest.rglob("*"):
                if path.is_file():
                    text = path.read_text(errors="replace")
                    self.assertNotRegex(text, r"\[=|=\]|\[%|%\]", f"{distro}: {path}")
                    self.assertNotIn("[=", path.name)

    def test_github_and_pixi_expressions_untouched(self):
        dest = self.rendered["jazzy"]
        testpr = (dest / ".github/workflows/testpr.yml").read_text()
        self.assertIn("${{ matrix.platform }}", testpr)
        self.assertIn('if [[ "${attempt}" == "3" ]]; then', testpr)
        self.assertIn("{{ PACKAGE }}", (dest / "pixi.toml").read_text())

    def test_distribution_values(self):
        rolling = (self.rendered["rolling"] / "pixi.toml").read_text()
        self.assertIn('default = "ros2-ros-workspace"', rolling)
        self.assertIn('rev = "5767846ab2557b4e358e11a5f07c7743df3ede4e"', rolling)
        self.assertIn("rattler-build upload prefix -c robostack-rolling", rolling)
        humble = (self.rendered["humble"] / "pixi.toml").read_text()
        self.assertIn("-c https://conda.anaconda.org/robostack-staging", humble)
        self.assertIn("rattler-build upload anaconda -o robostack-staging", humble)
        self.assertIn('default = "ros2-ros-workspace"', humble)
        self.assertIn("vinca-snapshot -d humble", humble)
        lyrical = (self.rendered["lyrical"] / ".github/workflows/testpr.yml").read_text()
        self.assertIn("-c https://prefix.dev/robostack-lyrical", lyrical)
        self.assertTrue((self.rendered["kilted"] / "tests/ros2-rclpy.yaml").is_file())
        # one shared vinca for everyone
        revs = {re.search(r'^vinca = .*$', (d / "pixi.toml").read_text(), re.M).group(0) for d in self.rendered.values()}
        self.assertEqual(len(revs), 1)

    def test_pixi_toml_parses(self):
        import tomllib

        for distro, dest in self.rendered.items():
            data = tomllib.loads((dest / "pixi.toml").read_text())
            self.assertEqual(data["workspace"]["name"], f"ros-{distro}")
            self.assertIn("build-ci", data["tasks"])

    def test_workflows_parse(self):
        for dest in self.rendered.values():
            for wf in (dest / ".github/workflows").glob("*.yml"):
                yaml.safe_load(wf.read_text())

    def test_build_win_keeps_crlf(self):
        data = (self.rendered["rolling"] / ".scripts/build_win.bat").read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_answers_file(self):
        answers = yaml.safe_load((self.rendered["rolling"] / ".copier-answers.yml").read_text())
        self.assertEqual(answers["distro"], "rolling")
        self.assertNotIn("channel_url", answers)  # computed, not stored


class SharedFilesTest(RenderTest):
    """robostack.yaml / packages-ignore.yaml / tests are shared; check what each distro gets."""

    ORIGINAL = Path(__file__).resolve().parent.parent.parent  # ../ros-<distro> checkouts

    def test_robostack_yaml_keeps_every_distro_mapping(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from merge_conda_index import DROP, UNIFY

        for distro, dest in self.rendered.items():
            new = yaml.safe_load((dest / "robostack.yaml").read_text())
            out = subprocess.run(
                ["git", "-C", str(self.ORIGINAL / f"ros-{distro}"), "show", "origin/main:robostack.yaml"],
                capture_output=True, text=True,
            )
            if out.returncode != 0:
                self.skipTest("distribution checkouts not available")
            old = yaml.safe_load(out.stdout)
            for key, value in old.items():
                if key in UNIFY or key in DROP:
                    continue
                self.assertEqual(new[key], value, f"{distro}: {key}")

    def test_robostack_yaml_sorted(self):
        for distro, dest in self.rendered.items():
            keys = list(yaml.safe_load((dest / "robostack.yaml").read_text()))
            self.assertEqual(keys, sorted(keys, key=str.casefold), distro)

    def test_tests_are_distro_agnostic(self):
        for distro, dest in self.rendered.items():
            for path in (dest / "tests").rglob("*"):
                self.assertFalse(path.name.startswith("ros-"), path)
                if path.is_file():
                    self.assertNotRegex(path.read_text(), rf"(?<![/\w])ros-{distro}-", path)


class CiConfigTest(unittest.TestCase):
    def run_config(self, config: dict, files: list[str]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cache = tmp / "output" / "linux-64"
            cache.mkdir(parents=True)
            for name in files:
                (cache / name).write_text("")
            (tmp / "ci.yaml").write_text(yaml.safe_dump(config))
            (tmp / "vinca.yaml").write_text("ros_distro: jazzy\n")
            subprocess.run(
                [sys.executable, str(ROOT / "template/.github/scripts/ci_config.py"), "--cache-dir", str(cache)],
                cwd=tmp, check=True, capture_output=True,
            )
            return sorted(p.name for p in cache.iterdir())

    FILES = [
        "ros2-std-msgs-5.3.0-np2py314h1_29.conda",
        "ros-jazzy-std-msgs-5.3.0-np2py314h1_29.conda",
        "ros-jazzy-std-msgs-extra-1.0.0-h1_29.conda",
        "ros-jazzy-roboplan-core-0.7.0-h1_29.conda",
        "ros-jazzy-rclcpp-28.0.0-h1_29.conda",
    ]

    def test_evict_exact_and_glob(self):
        left = self.run_config({"evict_cache": ["std_msgs", "roboplan*"]}, self.FILES)
        self.assertEqual(left, ["ros-jazzy-rclcpp-28.0.0-h1_29.conda", "ros-jazzy-std-msgs-extra-1.0.0-h1_29.conda"])

    def test_full_rebuild(self):
        self.assertEqual(self.run_config({"full_rebuild": True}, self.FILES), [])

    def test_default_keeps_everything(self):
        self.assertEqual(self.run_config({"full_rebuild": False, "evict_cache": []}, self.FILES), sorted(self.FILES))


class BotTest(unittest.TestCase):
    def setUp(self):
        self._copy = TemplateCopy()
        self.template = self._copy.__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "ros-jazzy"
        instantiate(self.template, self.repo, REGISTRY["jazzy"]["answers"])

    def tearDown(self):
        self._tmp.cleanup()
        self._copy.__exit__(None, None, None)

    def test_drift_clean_then_edited(self):
        self.assertEqual(tpl.drift(self.repo, str(self.template)), [])
        wf = self.repo / ".github/workflows/testpr.yml"
        wf.write_text(wf.read_text() + "# hand edit\n")
        found = tpl.drift(self.repo, str(self.template))
        self.assertEqual([d.path for d in found], [".github/workflows/testpr.yml"])
        self.assertIn("+# hand edit", found[0].diff)
        # distribution-owned files are never drift
        (self.repo / "ci.yaml").write_text("full_rebuild: true\n")
        self.assertEqual(len(tpl.drift(self.repo, str(self.template))), 1)

    def test_rerender_picks_up_template_change(self):
        target = self.template / "template/build_gap_report.py"
        target.write_text(target.read_text() + "# template change\n")
        git(self.template, "commit", "-qam", "change")
        git(self.template, "tag", "v-test-2")
        result = commands.rerender(self.repo, vcs_ref="v-test-2")
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(result.changed)
        self.assertTrue((self.repo / "build_gap_report.py").read_text().endswith("# template change\n"))
        self.assertEqual(tpl.read_answers(self.repo)["_commit"], "v-test-2")

    def test_upstream_plain_and_templated(self):
        plain = self.repo / "build_gap_report.py"
        plain.write_text(plain.read_text() + "# fixed in jazzy\n")
        pixi = self.repo / "pixi.toml"
        pixi.write_text(pixi.read_text().replace('cmake = "<4.0"', 'cmake = "<4.1"'))
        result = commands.upstream(self.repo, self.template)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue((self.template / "template/build_gap_report.py").read_text().endswith("# fixed in jazzy\n"))
        src = (self.template / "template/pixi.toml.jinja").read_text()
        self.assertIn('cmake = "<4.1"', src)
        self.assertIn("[= distro =]", src)  # still a template

    def test_upstream_templated_line_needs_manual_port(self):
        pixi = self.repo / "pixi.toml"
        pixi.write_text(pixi.read_text().replace("vinca-snapshot -d jazzy", "vinca-snapshot -d jazzy --verbose"))
        result = commands.upstream(self.repo, self.template)
        self.assertFalse(result.ok)
        self.assertIn("pixi.toml", result.summary)

    def test_new_distro(self):
        (self.repo / "vinca.yaml").write_text("ros_distro: jazzy\nbuild_number: 25\nchannels:\n  - robostack-jazzy\n")
        (self.repo / "pkg_additional_info.yaml").write_text("foo:\n  build_number: 3\nbar:\n  build_number: 2\n  additional_cmake_args: x\n")
        (self.repo / "patch").mkdir()
        (self.repo / "patch/ros-jazzy-foo.patch").write_text("")
        dest = self.tmp / "ros-macaroni"
        result = commands.new_distro("macaroni", self.repo, dest, str(self.template), {"upload_target": "prefix"})
        self.assertTrue(result.ok)
        vinca = (dest / "vinca.yaml").read_text()
        self.assertIn("ros_distro: macaroni", vinca)
        self.assertIn("build_number: 0", vinca)
        self.assertIn("robostack-macaroni", vinca)
        info = yaml.safe_load((dest / "pkg_additional_info.yaml").read_text())
        self.assertEqual(info, {"bar": {"additional_cmake_args": "x"}})
        self.assertIn('name = "ros-macaroni"', (dest / "pixi.toml").read_text())
        self.assertTrue((dest / "tests/ros2-rclpy.yaml").is_file())
        self.assertTrue((dest / "robostack.yaml").is_file())
        self.assertEqual(list((dest / "patch").iterdir()), [])
        self.assertIn("1 candidates", result.summary)


class SmallTest(unittest.TestCase):
    def test_parse_comment(self):
        self.assertEqual(commands.parse_comment("@robostack-bot rerender", "MEMBER"), "rerender")
        self.assertEqual(commands.parse_comment("Hi\n@robostack-bot, please update-snapshot", "OWNER"), "update-snapshot")
        self.assertIsNone(commands.parse_comment("@robostack-bot rerender", "CONTRIBUTOR"))
        self.assertIsNone(commands.parse_comment("@robostack-bot rm-rf", "MEMBER"))
        self.assertIsNone(commands.parse_comment("thanks @robostack-bot", "MEMBER"))
        # issue form ("robostack-bot command" issue template)
        form = "### Command\n\nupdate-snapshot\n\n### Notes\n\n_No response_"
        self.assertEqual(commands.parse_comment(form, "OWNER"), "update-snapshot")
        self.assertIsNone(commands.parse_comment(form, "NONE"))
        # the pinning is shared and updated in the template repository
        self.assertIsNone(commands.parse_comment("@robostack-bot update-pinning", "OWNER"))

    def test_snapshot_changes(self):
        text = commands.snapshot_changes({"a": "1.0", "b": "2.0", "c": "1"}, {"a": "1.1", "b": "2.0", "d": "3"})
        self.assertTrue(text.startswith("1 updated, 1 added, 1 removed"))
        self.assertIn("| a | 1.0 | 1.1 |", text)

    def test_channel_url(self):
        self.assertEqual(commands.channel_url({"distro": "lyrical"}), "https://prefix.dev/robostack-lyrical")
        self.assertEqual(
            commands.channel_url({"distro": "humble", "channel_name": "robostack-staging", "upload_target": "anaconda"}),
            "https://conda.anaconda.org/robostack-staging",
        )
        self.assertTrue(re.match(r"https://repo\.prefix\.dev/", commands.channel_url({"distro": "x"}, True)))


if __name__ == "__main__":
    unittest.main()
