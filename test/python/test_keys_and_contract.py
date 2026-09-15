"""The things that made "it doesn't work" take hours to explain.

Every message in `key_shape` exists because somebody — me — spent real time on
a 401 that said nothing useful. A masked key pasted out of the console looks
exactly like a real one to anybody not counting characters.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "anki"))

import proofread                                                 # noqa: E402
import explain                                                   # noqa: E402


@pytest.fixture
def key(monkeypatch):
    def use(value):
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)
        return proofread.key_shape()
    return use


def test_an_empty_key_says_so(key):
    assert key("") == "it is empty"
    assert key("   ") == "it is empty"


def test_the_wrong_kind_of_token_is_named(key):
    said = key("ghp_0123456789abcdefghijklmnopqrstuvwxyz")
    assert "sk-ant-" in said
    assert "different kind of token" in said


def test_a_masked_key_is_the_one_people_actually_paste(key):
    # This is what the Anthropic console shows once the key is created. It is
    # the right length and the right prefix and it is not a key.
    said = key("sk-ant-api03-" + "•" * 80)
    assert "masked display" in said


def test_a_truncated_key_is_named_as_short(key):
    said = key("sk-ant-api03-tooshort")
    assert "short" in said.lower() or "characters" in said


def test_surrounding_whitespace_is_an_aside_not_a_cause(key):
    # It is stripped before the key is ever sent, so it is never the reason one
    # is rejected — saying otherwise sends somebody off cleaning a secret that
    # is not at fault.
    said = key("  sk-ant-api03-" + "a" * 90 + "\n")
    assert "ignored" in said


def test_a_key_that_looks_right_is_not_blamed(key):
    said = key("sk-ant-api03-" + "a" * 90)
    assert "empty" not in said
    assert "masked" not in said


def test_the_workspace_header_is_only_sent_when_there_is_one(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    assert proofread.workspace_id() == ""
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", " wrkspc_123 ")
    assert proofread.workspace_id() == "wrkspc_123"


def test_slug_is_a_stable_filename():
    assert explain.slug("Put Off") == "put-off"
    assert explain.slug("  beat around the bush  ") == "beat-around-the-bush"
    assert explain.slug("don't") == "don-t"
    assert explain.slug("café") == "caf"
    assert explain.slug("!!!") == ""


def test_the_contract_is_what_the_browser_needs():
    # The app fetches this and builds its own request from it, so the two
    # cannot drift into asking for different things.
    contract = explain.contract()
    assert contract["schema"]["additionalProperties"] is False
    assert contract["max_tokens"] > 0
    assert isinstance(contract["system"], str) and contract["system"].strip()
    required = set(contract["schema"]["required"])
    for field in ("recognised", "word", "meaning", "nuance", "collocations",
                  "confusables", "memory_hook"):
        assert field in required, field
    # No nullable unions anywhere: the shape known to work with this API has
    # none, and the browser's speaking prompt follows the same rule.
    def types(node):
        if isinstance(node, dict):
            if isinstance(node.get("type"), list):
                yield node["type"]
            for value in node.values():
                yield from types(value)
        elif isinstance(node, list):
            for value in node:
                yield from types(value)
    assert list(types(contract["schema"])) == []
