#!/usr/bin/env python3
"""Build the user's voice bank: a JSONL of short passages they wrote themselves.

Each output line is {"text", "source", "title", "date", "kind"} where kind is
one of essay | page | garden | obsidian | logseq.

Where to read from is a setting with no default: ``voice_sources`` in the Talk to
Write app's settings file (``data/.apps/talk-to-write/settings.json``), a list of

    {"path": "<folder>", "kind": "essay" | "page" | "garden" | "obsidian" | "logseq",
     "exclude": ["<regex on the path inside the folder>", ...]}   # exclude is optional

Finished pieces (essay, page) are the strongest evidence of voice; garden notes
(Obsidian-style notes in the user's own words) next; rough notes (obsidian, logseq)
last. With no sources set, the bank is written empty and voice edits run without
voice examples.

Only the user's own words are kept. Things that are deliberately dropped:
  - frontmatter, headings, code, tables, link lists, images, short fragments
  - garden notes: `> [!claude]` callouts, single-[bracket] insertions, "Related:" lines
  - Obsidian / Logseq: files matching a source's `exclude` patterns, clippings /
    book notes, blockquotes, and paragraphs with tell-tale AI or quotation markers.

Stdlib only. Re-runnable: the output file is rewritten each run.

Usage:
  python build_voice_bank.py [--settings FILE] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

WS = Path("/home/user/workspace")
SETTINGS = WS / "data/.apps/talk-to-write/settings.json"
OUT = WS / "data/.apps/talk-to-write/voice/passages.jsonl"
KINDS = ("essay", "page", "garden", "obsidian", "logseq")

MIN_CHARS, MAX_CHARS, MIN_WORDS = 40, 600, 8
TARGET_CHARS = 420  # soft target when splitting long paragraphs into sentence groups

# Site pages that are usually other people's words (quotes) or bare lists.
SKIP_PAGES = {"quotes.md", "bookshelf.md"}

# A Logseq block whose text matches this starts a subtree of someone else's
# words (therapy/coach notes, highlights, talk/lecture notes, transcripts).
LOGSEQ_SKIP_SUBTREE = re.compile(
    r"(\[\[Conversation\]\]|^Highlights|Transcript|Readwise|\[\[Book Notes\]\]|Notes::|"
    r"^Speaker \d|\bnotes?:?$|\bsaid:?$|\bquotes?:?$|Lecture|Podcast|"
    r"Retreat|^Tags::|Author::|URL::)",
    re.I,
)
LOGSEQ_PAGE_CLIPPING = re.compile(
    r"(#Readwise|Author::|URL::|^- Highlights|\[\[Book Notes\]\])", re.M
)

# Paragraph-level signs that a passage is AI-written or addressed to the user by an AI.
AI_TELLS = re.compile(
    r"(\byour (intuition|draft|outline|essay|notes|framing|instinct|own example|"
    r"argument|thesis|sentences?|words|screenshot)\b|^(Here's|Here is|Let me|Great question|"
    r"Certainly[,!]|Absolutely[,!]|I'd suggest|I'd push|Sure[,!])|\bmy read\b|\bas I read it\b|"
    r"\(suggested by ChatGPT\)|\bClaude's words\b|That's a (profound|powerful|beautiful|real)\b|"
    r"It's the difference between|\bWorth (checking|noting)\b|I can't see the|\bI'd say plainly\b|"
    r"\*\*Response:\*\*|Counterargument:|\bthe essay should\b|\s->\s|[\"“][^\"”]{90,}[\"”])",
    re.I,
)
# Signs a passage is mainly someone else's words.
QUOTE_TELLS = re.compile(
    r"(^[\"“'‘].{20,}[\"”'’]\.?$|\s[-–—]\s?[A-Z][a-z]+( [A-Z][a-z]+)*$|\b(said|says|wrote|writes),? [\"“]|"
    r"^[A-Z][a-zA-Z]+( [A-Z][a-zA-Z]+)?: [\"“]|"  # "Sam: \"...\"" dialogue lines
    r"^([A-Z][\w.'’-]+ ){0,3}[A-Z][\w.'’-]+ [—–-] |"  # "Ada Moreno — <their idea>"
    r"^([A-Z][\w.'’-]+ ){1,3}(wants|said|says|thinks|believes|argues|writes|notes)\b)"
)
# Notes and chat carry other people's words (meeting/talk notes, pasted text);
# for those kinds keep only passages in the user's own first person, or their questions.
FIRST_PERSON = re.compile(
    r"(\bI\b|\bI'(m|ve|d|ll)\b|\b(my|me|myself|we|we're|we've|our|us)\b|\?)", re.I
)
FIRST_PERSON_KINDS = {"obsidian", "logseq"}
SENT_SPLIT = re.compile(r"(?<=[.!?…])[\"”’)]*\s+(?=[\"“(]?[A-Z0-9])")


# ---------------------------------------------------------------- cleaning


def strip_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
                if m:
                    meta[m.group(1)] = m.group(2).strip().strip('"')
            text = text[end + 4 :]
    return meta, text


def clean_inline(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)  # html tags
    s = re.sub(r"!\[\[[^\]]*\]\]", " ", s)  # obsidian embeds
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)  # images
    s = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", s)  # [[target|alias]]
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)  # [[target]]
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # [text](url)
    s = re.sub(r"\[\^[^\]]*\]", "", s)  # footnote refs
    s = re.sub(r"\(\([0-9a-f-]{36}\)\)", " ", s)  # logseq block refs
    s = re.sub(r"#\[\[[^\]]*\]\]", " ", s)  # logseq #[[tag]]
    s = re.sub(r"(?<!\w)#[A-Za-z][\w/-]*", " ", s)  # #tags
    s = re.sub(r"\{\{[^}]*\}\}", " ", s)  # logseq macros
    s = re.sub(r"\b[a-z-]+::\s*\S*", " ", s)  # logseq properties
    s = re.sub(
        r"^(TODO|DONE|LATER|NOW|DOING|WAITING|CANCELED|CANCELLED)\s+", "", s.strip()
    )
    s = s.replace("\\_", "_").replace("\\*", "*")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def strip_garden(body: str) -> str:
    out, in_callout = [], False
    for line in body.splitlines():
        if re.match(r"^>\s*\[!claude\]", line, re.I):
            in_callout = True
            continue
        if in_callout:
            if line.startswith(">"):
                continue
            in_callout = False
        if re.match(r"^\s*Related:", line):
            continue
        # single [brackets] that are not links/wikilinks/footnotes are Claude's insertions
        line = re.sub(r"(?<!\[)\[(?!\[|\^|!)[^\[\]]*\](?![\(\]])", "", line)
        out.append(line)
    return "\n".join(out)


def is_bad(text: str, kind: str) -> bool:
    if len(text) < MIN_CHARS or len(text.split()) < MIN_WORDS:
        return True
    if re.search(r"https?://|www\.|\S+\.(com|org|io|ai)/", text):
        return True
    if "|" in text and text.count("|") >= 2:
        return True
    if re.search(
        r"[{};]\s*$|^\s*(def |class |import |from \S+ import|\$ |>>>)|=>|\(\)", text
    ):
        return True
    letters = sum(c.isalpha() for c in text)
    if letters < 0.6 * len(text):
        return True
    # essays, pages and garden notes are the user's curated words: only structural filters apply
    if kind not in ("essay", "page", "garden") and (
        AI_TELLS.search(text) or QUOTE_TELLS.search(text)
    ):
        return True
    if text.rstrip().endswith(":") or text.startswith((">", "Q:", "A:")):
        return True
    if kind in FIRST_PERSON_KINDS and not FIRST_PERSON.search(text.replace("I.e.", "")):
        return True
    return False


def chunk(paragraph: str) -> list[str]:
    """Split a paragraph into groups of 1-4 sentences of at most MAX_CHARS."""
    p = paragraph.strip()
    if len(p) <= MAX_CHARS:
        return [p]
    sents = [s.strip() for s in SENT_SPLIT.split(p) if s.strip()]
    groups, cur = [], []
    for s in sents:
        if (
            len(s) > MAX_CHARS
        ):  # run-on (often transcripts): hard-wrap on clause boundaries
            if cur:
                groups.append(" ".join(cur))
                cur = []
            parts = re.split(r"(?<=[,;—])\s+", s)
            buf = ""
            for part in parts:
                if buf and len(buf) + len(part) + 1 > TARGET_CHARS:
                    groups.append(buf)
                    buf = part
                else:
                    buf = f"{buf} {part}".strip()
            if buf:
                groups.append(buf)
            continue
        if cur and (len(" ".join(cur)) + len(s) + 1 > TARGET_CHARS or len(cur) >= 4):
            groups.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        groups.append(" ".join(cur))
    return [g for g in groups if len(g) <= MAX_CHARS]


def prose_paragraphs(body: str) -> list[str]:
    """Paragraphs of a markdown document, skipping headings/code/tables/quotes/lists of links."""
    paras, cur, in_code = [], [], False

    def flush():
        if cur:
            paras.append("\n".join(cur))
            cur.clear()

    for raw in body.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```") or line.strip().startswith("~~~"):
            in_code = not in_code
            flush()
            continue
        if in_code:
            continue
        s = line.strip()
        if not s or s in ("---", "***", "--") or s.startswith("#") or s.startswith(">"):
            flush()
            continue
        fn = re.match(r"^\[\^\w+\]:\s*(.*)$", s)
        if fn:  # footnote body: their prose, minus the marker
            flush()
            paras.append(fn.group(1))
            continue
        if s.startswith("|") or s.startswith("%%") or s.startswith("<"):
            flush()
            continue
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if m:  # list item: its own paragraph
            flush()
            paras.append(m.group(3))
            continue
        if raw.startswith(("\t", "    ")) and cur:
            cur.append(s)
            continue
        cur.append(s)
    flush()
    return paras


# ---------------------------------------------------------------- dedupe / emit


class Bank:
    def __init__(self):
        self.rows: list[dict] = []
        self.exact: set[str] = set()
        self.prefix: dict[str, int] = {}
        self.dropped = Counter()

    @staticmethod
    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", t.lower())

    def add(
        self, text: str, source: Path, title: str | None, date: str | None, kind: str
    ):
        for piece in re.split(r"\s…\s|^…\s*|\s*…$", text):
            for g in chunk(clean_inline(piece)):
                g = g.strip(" -–—")
                if g.count("**") % 2:
                    g = g.replace("**", "")
                if g.count("*") % 2 and not re.search(r"\w\*\w", g):
                    g = g.strip("*").strip()
                if is_bad(g, kind):
                    self.dropped[kind] += 1
                    continue
                n = self.norm(g)
                key = n[:60]
                if n in self.exact or (
                    key in self.prefix
                    and abs(self.prefix[key] - len(n)) < 0.25 * len(n)
                ):
                    self.dropped["dup"] += 1
                    continue
                self.exact.add(n)
                self.prefix[key] = len(n)
                self.rows.append(
                    {
                        "text": g,
                        "source": str(source),
                        "title": title,
                        "date": date,
                        "kind": kind,
                    }
                )


def is_part_file(p: Path) -> bool:
    m = re.search(r"^(.*)\.p\d\d\.md$", p.name)
    return bool(m) and (p.parent / f"{m.group(1)}.md").exists()


def norm_date(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


# ---------------------------------------------------------------- sources


def do_site(bank: Bank, d: Path, kind: str):
    for f in sorted(d.glob("*.md")):
        if kind == "page" and f.name in SKIP_PAGES:
            continue
        meta, body = strip_frontmatter(f.read_text(encoding="utf-8"))
        if meta.get("coauthor"):
            continue  # co-written; body is only an excerpt in frontmatter anyway
        title, date = meta.get("title") or f.stem, norm_date(meta.get("date"))
        for p in prose_paragraphs(body):
            bank.add(p, f, title, date, kind)


def do_garden(bank: Bank, d: Path):
    for f in sorted(d.glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        meta, body = strip_frontmatter(raw)
        dates = re.findall(
            r"journals/(\d{4}_\d{2}_\d{2})",
            raw.split("\n---", 2)[0] if raw.startswith("---") else "",
        )
        date = norm_date(dates[0]) if len(set(dates)) == 1 else None
        for p in prose_paragraphs(strip_garden(body)):
            bank.add(p, f, f.stem, date, "garden")


def do_obsidian(bank: Bank, d: Path, exclude: list[str]):
    deny = [re.compile(x) for x in exclude]
    for f in sorted(d.rglob("*.md")):
        rel = str(f.relative_to(d))
        if is_part_file(f) or any(x.search(rel) for x in deny):
            continue
        meta, body = strip_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        date = (
            norm_date(rel)
            if rel.startswith("Daily notes/") or re.match(r"Essays/\d{4}", rel)
            else None
        )
        for p in prose_paragraphs(body):
            bank.add(p, f, f.stem, date, "obsidian")


def parse_logseq_blocks(body: str) -> list[tuple[int, str]]:
    blocks: list[list] = []
    for raw in body.splitlines():
        m = re.match(r"^([\t ]*)- ?(.*)$", raw)
        if m:
            indent = len(m.group(1).replace("    ", "\t").replace("\t", "  "))
            blocks.append([indent, m.group(2)])
        elif blocks and raw.strip():
            blocks[-1][1] += " " + raw.strip()
        elif raw.strip():
            blocks.append([0, raw.strip()])
    return [(i, t) for i, t in blocks]


def do_logseq(bank: Bank, d: Path, exclude: list[str]):
    deny = [re.compile(x) for x in exclude]
    for f in sorted(d.rglob("*.md")):
        if is_part_file(f) or any(x.search(str(f.relative_to(d))) for x in deny):
            continue
        is_journal = f.parent.name == "journals"
        title = urllib.parse.unquote(f.stem)
        raw = f.read_text(encoding="utf-8", errors="replace")
        meta, body = strip_frontmatter(raw)
        title = meta.get("title") or title
        notes_only = False
        if not is_journal:
            if LOGSEQ_PAGE_CLIPPING.search(body):
                notes_only = (
                    True  # readwise clipping: keep only the user's **Note**: reactions
                )
        date = norm_date(f.stem) if is_journal else None
        skip_below = None
        for indent, text in parse_logseq_blocks(body):
            if skip_below is not None:
                if indent > skip_below:
                    continue
                skip_below = None
            t = text.strip()
            if notes_only:
                m = re.match(r"^\*\*Note\*\*:\s*(.*)$", t)
                if m:
                    bank.add(m.group(1), f, title, date, "logseq")
                continue
            if LOGSEQ_SKIP_SUBTREE.search(
                clean_inline(t)
            ) or LOGSEQ_SKIP_SUBTREE.search(t):
                skip_below = indent
                continue
            if re.match(r"^(collapsed|id|title|tags|alias)::", t) or t.startswith(
                ("#", ">", "```", "|")
            ):
                continue
            bank.add(t, f, title, date, "logseq")


# ---------------------------------------------------------------- main


def read_sources(settings: Path) -> list[dict]:
    """The configured voice sources, or [] when none are set."""
    try:
        data = json.loads(settings.read_text())
    except (OSError, ValueError):
        return []
    sources = data.get("voice_sources") if isinstance(data, dict) else None
    out = []
    for src in sources or []:
        if not isinstance(src, dict) or not src.get("path"):
            continue
        if src.get("kind") not in KINDS:
            print(f"warning: skipping source with unknown kind: {src}", file=sys.stderr)
            continue
        out.append(src)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--settings", type=Path, default=SETTINGS)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    bank = Bank()
    sources = read_sources(a.settings)
    if not sources:
        print(
            f"note: no voice_sources set in {a.settings}; writing an empty bank "
            "(voice edits will run without voice examples)",
            file=sys.stderr,
        )
    # order = priority for dedupe: finished prose wins over notes
    for src in sorted(sources, key=lambda x: KINDS.index(x["kind"])):
        path = Path(src["path"]).expanduser()
        kind, exclude = src["kind"], list(src.get("exclude") or [])
        if not path.exists():
            print(f"warning: {kind} source missing: {path}", file=sys.stderr)
            continue
        if kind in ("essay", "page"):
            do_site(bank, path, kind)
        elif kind == "garden":
            do_garden(bank, path)
        elif kind == "obsidian":
            do_obsidian(bank, path, exclude)
        else:
            do_logseq(bank, path, exclude)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in bank.rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(a.out)
    counts = Counter(r["kind"] for r in bank.rows)
    print(
        json.dumps(
            {
                "total": len(bank.rows),
                "by_kind": dict(counts),
                "dropped": dict(bank.dropped),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
