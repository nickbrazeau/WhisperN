"""Clipboard writes are concealed and cleared only if unchanged.

Uses a private named pasteboard, never the user's general clipboard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from integrations import clipboard  # noqa: E402

PB = "local.whispern.test"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS pasteboard")


def test_copy_marks_concealed_and_transient():
    text = "Synthetic: move the meeting to Wednesday — “quoted” ünïcode"
    count = clipboard.copy(text, PB)
    assert count
    got = clipboard.read(PB)
    assert got["text"] == text
    assert clipboard.CONCEALED in got["types"]
    assert clipboard.TRANSIENT in got["types"]


def test_clear_when_unchanged():
    count = clipboard.copy("synthetic text", PB)
    assert clipboard.clear_if_unchanged(count, PB)
    assert clipboard.read(PB)["text"] is None


def test_never_clears_a_later_copy():
    first = clipboard.copy("synthetic one", PB)
    clipboard.copy("synthetic two, copied by the user", PB)
    assert not clipboard.clear_if_unchanged(first, PB)
    assert clipboard.read(PB)["text"] == "synthetic two, copied by the user"


def test_text_is_not_in_argv():
    # The script receives text on stdin only; argv carries the pasteboard name.
    assert "readDataToEndOfFile" in clipboard._COPY
