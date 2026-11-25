"""Build a git-filter-repo blob callback that redacts identifiers.

Blanking notebook outputs does not touch identifiers *typed into* source — for
example ``df[df['eid'] == 7000001]``, a spot-check of one participant's record
left behind in a cell. Those must be substituted rather than deleted, so the
surrounding code survives and the history stays readable.

Caveats worth knowing before using this:

* A blob callback cannot see the filename and cannot apply the context
  heuristics the scanners use, so redaction inside a blob is unconditional.
  Always diff the result rather than assuming only the intended lines changed.
* Redaction rewrites file *content*, so every commit hash changes.
"""
from __future__ import annotations

DEFAULT_PLACEHOLDER = "REDACTED_ID"

# Contexts where a digit run is definitively not a participant identifier.
# A Stack Overflow answer link (stackoverflow.com/a/1094933/1870254) matches the
# identifier pattern twice; redacting it mangles a source attribution and
# achieves nothing. This is the one exception applied inside the callback —
# narrow, and justified because the surrounding text names a URL scheme or host.
URL_CONTEXT = rb"https?://|www\.|doi\.org|stackoverflow\.com|github\.com|gitlab\."

# Executed by git-filter-repo with `blob` in scope. Kept deliberately small:
# it runs once per blob over the whole history.
_TEMPLATE = "\n".join([
    "import re as _re",
    "_PAT = _re.compile({pattern!r})",
    "_URL = _re.compile({url_context!r})",
    "_REPL = {placeholder!r}",
    "_HEX = b'0123456789abcdefABCDEF'",
    "def _in_hex_token(_s, _a, _b):",
    "    # Widen over [0-9a-fA-F-]. If the run extends into a hex letter or a",
    "    # hyphen it is a UUID or hash fragment, not an identifier. Jupyter cell",
    "    # ids are exactly this shape and redacting them corrupts the notebook.",
    "    while _a > 0 and (_s[_a-1] in _HEX or _s[_a-1:_a] == b'-'):",
    "        _a -= 1",
    "    while _b < len(_s) and (_s[_b] in _HEX or _s[_b:_b+1] == b'-'):",
    "        _b += 1",
    "    _tok = _s[_a:_b]",
    "    return len(_tok) > 7 and any(_c not in b'0123456789' for _c in _tok)",
    "def _sub(_m):",
    "    _s = _m.string",
    "    _lo = max(0, _m.start() - 80)",
    "    if _URL.search(_s[_lo:_m.end()]):",
    "        return _m.group(0)",
    "    if _in_hex_token(_s, _m.start(), _m.end()):",
    "        return _m.group(0)",
    "    return _REPL",
    "_new, _n = _PAT.subn(_sub, blob.data)",
    "if _n:",
    "    blob.data = _new",
])


# nbformat >=4.5 gives every cell an ``id`` — a random token such as
# ``aa11bb22-cc33-dd44-ee55-ff6677889900`` or ``ab12cd34``. Any 7-digit run
# inside one matches the identifier pattern, so a raw-text scanner reports it as
# a participant id. In one real repository these were 137 of 183 apparent hits.
#
# They are arbitrary metadata, so the fix is to rewrite the offending ids into a
# form that cannot match: map each digit to a letter. The mapping is injective,
# so distinct ids stay distinct, and the result contains no digits at all.
#
# Only ids that actually match are touched, and replacement is done on the exact
# quoted token rather than by re-serialising, so file formatting is preserved and
# the diff stays reviewable.
# NOTE: this template contains literal braces (the JSON sniff, the regex
# quantifiers), so it is filled by token replacement rather than str.format.
_CELLID_TEMPLATE = "\n".join([
    "import json as _json, re as _re",
    "_PAT = _re.compile(__PATTERN__)",
    "_MAP = bytes.maketrans(b'0123456789', b'ghijklmnop')",
    "_d = blob.data",
    "if _d[:1] == b'{' and b'\"cells\"' in _d[:8192]:",
    "    try:",
    "        _nb = _json.loads(_d.decode('utf-8'))",
    "    except Exception:",
    "        _nb = None",
    "    if isinstance(_nb, dict) and 'cells' in _nb:",
    "        for _c in _nb['cells']:",
    "            _cid = _c.get('id')",
    "            if not isinstance(_cid, str):",
    "                continue",
    "            _b = _cid.encode('utf-8')",
    "            if not _PAT.search(_b):",
    "                continue",
    "            _new = b'c' + _b.translate(_MAP)",
    "            _d = _d.replace(b'\"' + _b + b'\"', b'\"' + _new + b'\"')",
    "        if _d != blob.data:",
    "            blob.data = _d",
])


def build_cell_id_callback(profile) -> str:
    """Return callback source that rewrites matching Jupyter cell ids."""
    return _CELLID_TEMPLATE.replace(
        "__PATTERN__", repr(profile.id_regex.pattern.encode("utf-8")))


def build_callback(profile, placeholder: str = DEFAULT_PLACEHOLDER) -> str:
    """Return callback source that replaces every identifier match.

    The pattern is applied to raw bytes so that undecodable blobs are handled
    without a decode step; the identifier patterns are pure ASCII, so a bytes
    regex matches exactly the same text a str regex would.
    """
    pattern = profile.id_regex.pattern.encode("utf-8")
    return _TEMPLATE.format(pattern=pattern, url_context=URL_CONTEXT,
                            placeholder=placeholder.encode("utf-8"))
