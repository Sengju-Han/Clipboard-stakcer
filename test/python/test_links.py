"""Links to the published pages, which are the ones nobody can work around.

Everything here is used from a phone. When a workflow's failure message says
"open the connect page" and the link 404s, there is no fallback: no console, no
file manager, no way to guess what the address should have been. And the repo
is called `Clipboard-stakcer`, which is one transposition away from the word
anybody would type from memory.

So: every published link must agree on the base, and must point at a file that
is actually in `docs/`.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
SITE = re.compile(r"https://sengju-han\.github\.io/([A-Za-z0-9._%-]+)/([A-Za-z0-9._%/-]*)")
FLOW = re.compile(r"github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/actions/workflows/([A-Za-z0-9._-]+)")
LOOK_IN = ("*.md", "*.py", "*.yml", "*.yaml", "*.html")
SKIP = {".git", "node_modules", "vendor", "__pycache__", "deck"}


def _files():
    for pattern in LOOK_IN:
        for path in ROOT.rglob(pattern):
            if any(part in SKIP for part in path.relative_to(ROOT).parts):
                continue
            yield path


def _links():
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for base, rest in SITE.findall(text):
            yield path.relative_to(ROOT), base, rest


FOUND = sorted(set(_links()))


def test_there_are_links_to_check():
    # A regex that matches nothing makes everything below pass silently.
    assert len(FOUND) >= 4, f"only found {FOUND}"


def test_they_all_agree_on_the_repository_name():
    bases = {base for _, base, _ in FOUND}
    assert len(bases) == 1, (
        f"the published links disagree about the repository name: {sorted(bases)}. "
        "One of them 404s, and it is the one somebody reaches from a phone.")


@pytest.mark.parametrize("where,rest", [(w, r) for w, _, r in FOUND],
                         ids=[f"{w}:{r or '/'}" for w, _, r in FOUND])
def test_every_published_link_points_at_a_real_page(where, rest):
    target = DOCS / rest if rest else DOCS / "index.html"
    if target.is_dir():
        target = target / "index.html"
    assert target.exists(), (
        f"{where} links to /{rest}, which is not in docs/ - so it is a 404 for "
        "anybody who follows it.")


def _workflow_links():
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in FLOW.findall(text):
            yield path.relative_to(ROOT), name


FLOWS = sorted(set(_workflow_links()))


@pytest.mark.parametrize("where,name", FLOWS, ids=[f"{w}:{n}" for w, n in FLOWS] or None)
def test_every_link_to_a_workflow_points_at_one(where, name):
    """A workflow link is one rename away from a 404, and it is the kind of
    link that only ever gets followed by somebody who is already stuck."""
    assert (ROOT / ".github" / "workflows" / name).exists(), (
        f"{where} links to the {name} workflow, which is not in .github/workflows/.")
