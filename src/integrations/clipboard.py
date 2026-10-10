"""Clipboard writes that clipboard managers are asked not to keep.

`pbcopy` writes plain text only, so every dictation landed in clipboard
history (Maccy, Raycast, Alfred, Paste...) and stayed there. CLAUDE.md §3.1
names clipboard history as a leakage path.

Two measures, neither of which needs a dependency:

  1. Mark the item with the nspasteboard.org conventions
     `org.nspasteboard.ConcealedType` and `org.nspasteboard.TransientType`.
     Managers that honour them (most popular ones do) skip or hide the item.
     This is a request, not an enforcement: a manager that ignores the
     convention still records it.
  2. `clear_if_unchanged`: the caller clears the clipboard afterwards, but only
     if nothing else has been copied since. That is detected with the
     pasteboard's change count, so a later copy by the user is never wiped.

AppKit is reached through JavaScript for Automation (`osascript -l
JavaScript`). The text goes in on stdin, never argv or the environment, both
of which other processes of the same user can read through `ps`.
"""

from __future__ import annotations

import subprocess

CONCEALED = "org.nspasteboard.ConcealedType"
TRANSIENT = "org.nspasteboard.TransientType"

# `name` selects a named pasteboard instead of the general one. Tests use it,
# so they never touch the user's real clipboard.
_PB = """
ObjC.import('AppKit');
function pb(name) {
  return name ? $.NSPasteboard.pasteboardWithName(name)
              : $.NSPasteboard.generalPasteboard;
}
"""

_COPY = _PB + """
function run(argv) {
  var data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  var text = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
  var p = pb(argv[0]);
  p.clearContents;
  p.setStringForType(text, $.NSPasteboardTypeString);
  p.setStringForType($(''), '%s');
  p.setStringForType($(''), '%s');
  return String(p.changeCount);
}
""" % (CONCEALED, TRANSIENT)

_CLEAR = _PB + """
function run(argv) {
  var p = pb(argv[1]);
  if (String(p.changeCount) !== argv[0]) return 'changed';
  p.clearContents;
  return 'cleared';
}
"""

_READ = _PB + """
function run(argv) {
  var p = pb(argv[0]);
  var s = p.stringForType($.NSPasteboardTypeString);
  var types = ObjC.deepUnwrap(p.types) || [];
  return JSON.stringify({text: s.isNil() ? null : s.js, types: types,
                         count: String(p.changeCount)});
}
"""


def _jxa(script: str, args: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-l", "JavaScript", "-e", script, *args],
                          input=stdin, capture_output=True, timeout=10)


def copy(text: str, pasteboard: str = "") -> str | None:
    """Put `text` on the clipboard, marked concealed and transient.

    Returns the pasteboard change count after the write, for
    `clear_if_unchanged`, or None if the write failed.
    """
    r = _jxa(_COPY, [pasteboard], text.encode("utf-8"))
    if r.returncode != 0:
        return None
    return r.stdout.decode(errors="replace").strip() or None


def clear_if_unchanged(change_count: str | None, pasteboard: str = "") -> bool:
    """Empty the clipboard if it still holds what `copy` put there."""
    if not change_count:
        return False
    r = _jxa(_CLEAR, [change_count, pasteboard])
    return r.returncode == 0 and r.stdout.decode(errors="replace").strip() == "cleared"


def read(pasteboard: str = "") -> dict:
    """Return {"text", "types", "count"}. For tests and diagnostics only."""
    import json
    r = _jxa(_READ, [pasteboard])
    r.check_returncode()
    return json.loads(r.stdout.decode())
