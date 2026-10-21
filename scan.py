"""The leak scan for the rendered Finks reports site (Finks-ai/finks-reports).

`scan` re-reads every written file: HTML may use only TAGS and ATTRS, hrefs stay on the site, text is ASCII
(plus `·`), and no text, attribute value or CSS may match LEAKS, per node and joined across nodes after NFKC.
A finding names the file and rule, never the matched text.

Only generic shapes are checked here. The private deny-lists (internal db/table names, provider and vendor
names, public source names, plan names, legal-status words, and so on) stay in finks-lakehouse, which runs
them over the model before it is published. This repo must contain no literal private values.
"""

import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

FILES = (
    "index.html",
    "private/index.html",
    "roadmap/index.html",
    "lake/index.html",
    "evals/index.html",
    "costs/index.html",
)

TAGS = frozenset(
    "html head meta title style body main header nav h1 h2 h3 p div span b a ul li table tr th td "
    "svg line polyline rect circle text".split()
)
INLINE = frozenset({"a", "b", "span"})
ATTRS = frozenset(
    "lang charset name content class href viewbox width height x y x1 x2 y1 y2 cx cy r points fill stroke "
    "stroke-width stroke-dasharray role aria-label preserveaspectratio text-anchor".split()
)
HREF = re.compile(r"(\.\./)?([a-z]+/)?")
COLOR = re.compile(r"#[0-9a-f]{3}|#[0-9a-f]{6}|none")
LEAKS = {
    "AWS account id": r"(?<!\d)\d{12}(?!\d)",
    "AWS access key": r"\b(AKIA|ASIA)[0-9A-Z]{16}\b",
    "s3:// path": r"(?i)s3://",
    "URL": r"(?i)\b[a-z][a-z0-9+.-]*://|\bwww\.",
    "dollar amount": r"\$",
    "email or handle": r"@",
    "PR or issue ref": r"#\d|/pull/|/issues/",
    "snake_case identifier": r"[A-Za-z0-9]_[A-Za-z0-9]",
    "dotted name or hostname": r"(?i)\b[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*",
    "ARN": r"(?i)\barn:",
    "UUID": r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-",
    "hex id": r"(?i)\b[0-9a-f]{12,}\b",
    "IP address": r"\b\d{1,3}(\.\d{1,3}){3}\b",
    "credential word": r"(?i)\b(secrets?|passwords?|api ?keys?|credentials?|bearer)\b|\b(access|auth|api|bearer)[ _-]?tokens?\b",
    "token word": r"(?i)\btokens?\b",
    "SQL": r"(?i)\bselect\b[\s\S]*\bfrom\b",
    "billing word": r"(?i)\b(usd|dollars?|credits?|invoices?|billing|spend)\b",
    "storage size": r"(?i)\d[\d.,]*\s*(kb|mb|gb|tb|pb|kib|mib|gib|tib|bytes?)\b|\b(kilo|mega|giga|tera|peta)bytes?\b",
}
EXEMPT = {
    "storage size": {
        "lake/index.html"
    },  # absolute sizes and LLM token counts: the lake page only
    "token word": {"lake/index.html"},
}


class Leak(Exception):
    pass


def leaks(text: str, where: str) -> list[str]:
    """LEAKS over the NFKC-normalised text without format characters (zero-width, bidi), plus non-ASCII as written.
    `where` starts with the file's site path, which picks its EXEMPT rules."""
    norm = "".join(
        c
        for c in unicodedata.normalize("NFKC", text)
        if unicodedata.category(c) != "Cf"
    )
    rel = where.split(" ")[0].rstrip(":")
    found = [
        f"{where}: {name}"
        for name, p in LEAKS.items()
        if rel not in EXEMPT.get(name, ()) and re.search(p, norm)
    ]
    if re.search(r"[^\x00-\x7f\u00b7]", text):
        found.append(f"{where}: non-ASCII text")
    return found


class _Scan(HTMLParser):
    def __init__(self, where: str):
        super().__init__(convert_charrefs=True)
        self.where, self.found, self.text, self.in_style = where, [], [], False

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.found.append(f"{self.where}: tag <{tag}>")
        self.in_style = tag == "style"
        if tag not in INLINE:  # joined text keeps inline runs together, as rendered
            self.text.append("\n")
        for k, v in attrs:
            if k not in ATTRS:
                self.found.append(f"{self.where}: attribute {k}")
            elif k == "href" and not HREF.fullmatch(v or ""):
                self.found.append(f"{self.where}: off-site href")
            if k in ("fill", "stroke"):  # colors only, so `#1d4ed8` is no PR ref
                if not COLOR.fullmatch(v or ""):
                    self.found.append(f"{self.where}: {k} not a color")
            else:
                self.found += leaks(v or "", f"{self.where} {k}=")

    def handle_endtag(self, tag):
        self.in_style = False
        if tag not in INLINE:
            self.text.append("\n")

    def handle_data(self, data):
        if self.in_style:  # no comments, generated content, escapes or external references; colors aside, LEAKS
            if re.search(
                r"(?i)/\*|(?<![\w-])content\s*:|\\|url\(|@import|expression\(|//", data
            ):
                self.found.append(
                    f"{self.where}: CSS comment, content, escape or external reference"
                )
            self.found += leaks(
                re.sub(r"#[0-9a-f]{3}(?:[0-9a-f]{3})?\b", "", data), f"{self.where} CSS"
            )
        else:
            self.text.append(data)
            self.found += leaks(data, self.where)

    def handle_comment(self, data):
        self.found.append(f"{self.where}: comment")

    def handle_decl(self, decl):
        if decl.lower() != "doctype html":
            self.found.append(f"{self.where}: declaration")

    def unknown_decl(self, data):
        self.found.append(f"{self.where}: CDATA or unknown declaration")

    def handle_pi(self, data):
        self.found.append(f"{self.where}: processing instruction")


def scan(root: Path, ignore: tuple[str, ...] = ()) -> None:
    """Raise Leak naming file and rule (never the matched text) for anything outside the allowlisted shapes: only
    FILES, no symlinks; top-level `ignore` entries are skipped."""
    found = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.relative_to(root).parts[0] in ignore:
            continue
        if p.is_symlink():
            found.append(f"{rel}: symlink")
        elif p.is_dir():
            continue
        elif rel not in FILES:
            found.append(f"{rel}: unexpected file")
        elif p.suffix == ".html":
            s = _Scan(rel)
            s.feed(p.read_text())
            s.close()
            found += s.found + leaks("".join(s.text), f"{rel} (joined text)")
        else:
            found += leaks(p.read_text(), rel)
    if found:
        raise Leak("public site scan failed:\n  " + "\n  ".join(sorted(set(found))))
