"""The workflow files themselves, checked before GitHub gets to reject them.

An invalid workflow does not fail at the step that is wrong. It fails to parse,
which means it never appears in the Actions list at all — so the symptom is a
workflow that cannot be run and a red cross with no job and no log in it. That
is a hard thing to read, and the only feedback is a push.

This asks the questions GitHub would, on the way in.
"""

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


def test_there_are_workflows_to_check():
    # A glob that matches nothing makes every test above pass silently.
    assert len(WORKFLOWS) >= 8, f"only found {len(WORKFLOWS)}"
