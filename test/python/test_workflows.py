"""The workflow files themselves, checked before GitHub gets to reject them.

An invalid workflow does not fail at the step that is wrong. It fails to parse,
which means it never appears in the Actions list at all — so the symptom is a
workflow that cannot be run and a red cross with no job and no log in it. That
is a hard thing to read, and the only feedback is a push.

This asks the questions GitHub would, on the way in.
"""

import re
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="pip install pyyaml")

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))

# What GitHub actually accepts. `secrets` is not on it and never was: no
# permission lets a workflow's own token write a repository secret, and asking
# for one stops the file parsing rather than failing later.
ALLOWED = {
    "actions", "attestations", "checks", "contents", "deployments", "discussions",
    "id-token", "issues", "models", "packages", "pages", "pull-requests",
    "repository-projects", "security-events", "statuses",
}
LEVELS = {"read", "write", "none"}


def _ids(paths):
    return [p.name for p in paths]


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_it_parses(path):
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} is not a mapping"
    assert loaded.get("jobs"), f"{path.name} has no jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_every_permission_is_one_github_has(path):
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    places = [loaded.get("permissions")]
    places += [job.get("permissions") for job in loaded.get("jobs", {}).values()]

    for block in places:
        if block in (None, "read-all", "write-all", {}):
            continue
        for name, level in block.items():
            assert name in ALLOWED, (
                f"{path.name} asks for `{name}`, which GitHub does not have. "
                f"The file will not parse and the workflow will not appear at all.")
            assert level in LEVELS, f"{path.name}: `{name}: {level}` is not read, write or none"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_the_name_is_a_name(path):
    # A workflow with no name shows as its own file path in the Actions list,
    # which is also what a broken one looks like - worth not confusing the two.
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded.get("name"), f"{path.name} has no name:"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_actions_are_pinned(path):
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job in loaded.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses and not uses.startswith("./"):
                assert "@" in uses, f"{path.name} uses `{uses}` without a version"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_ids(WORKFLOWS))
def test_an_if_reads_env_from_the_job_not_the_step(path):
    """`if: env.X` sees the job's env, never the step's own.

    A step that sets X in its own `env:` and asks about it in its `if:` gets an
    empty string every time, so the step never runs - and nothing is logged,
    because as far as Actions is concerned the condition was simply false. It is
    the same silent shape as the invalid permission above: no error, no step, no
    clue. The fix is always to declare it at job level, which is why this asks.
    """
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job_name, job in loaded.get("jobs", {}).items():
        at_job = set((job.get("env") or {}).keys()) | set((loaded.get("env") or {}).keys())
        for step in job.get("steps", []):
            asked = re.findall(r"\benv\.([A-Za-z_][A-Za-z0-9_]*)", str(step.get("if", "")))
            for name in asked:
                assert name in at_job, (
                    f"{path.name}: the step '{step.get('name', '?')}' asks about "
                    f"env.{name} in its `if`, but {name} is not set on the job. "
                    f"A step's own env: is not visible there, so this reads as empty "
                    f"and the step never runs.")


# Every file a browser suite actually opens. A suite that drives a page the
# workflow's path filter does not cover is a suite that never runs on the
# change that breaks it.
DRIVEN = ["docs/index.html", "docs/app/app.js", "docs/app/sw.js", "test/run.py"]


def _covered(patterns, path):
    """Does one of GitHub's path filters match this file?"""
    from fnmatch import fnmatch

    for pattern in patterns:
        # `**` matches across directory separators; fnmatch's `*` already does,
        # which is close enough for the shapes used here.
        if fnmatch(path, pattern.replace("**", "*")):
            return True
    return False


def test_the_app_test_runs_on_everything_it_tests():
    loaded = yaml.safe_load((Path(__file__).resolve().parents[2]
                             / ".github" / "workflows" / "app-test.yml").read_text(encoding="utf-8"))
    # PyYAML reads a bare `on:` as the boolean True, which is a YAML 1.1 rule
    # and a well-known trap - the key is there, it is simply not a string.
    triggers = loaded.get("on", loaded.get(True, {}))
    for event in ("pull_request", "push"):
        patterns = triggers.get(event, {}).get("paths", [])
        assert patterns, f"app-test.yml has no paths for {event}"
        for path in DRIVEN:
            assert _covered(patterns, path), (
                f"app-test.yml does not run on {path} for {event}, but a suite drives it. "
                f"A change there would go out with no test having run.")


def test_there_are_workflows_to_check():
    # A glob that matches nothing makes every test above pass silently.
    assert len(WORKFLOWS) >= 8, f"only found {len(WORKFLOWS)}"
