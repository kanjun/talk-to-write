"""Markdown to the HTML the document view shows.

Plain markdown plus a few conventions a writer needs:

- an image or video on its own line, followed straight away by a line in
  italics, is a figure with that line as its caption::

      ![Morning walk route](/media/route.png)
      *The loop I walk before anyone is awake.*

- a "picture" whose address is a video file (.mp4, .mov, .webm, .m4v) is a
  playable video; a YouTube or Vimeo link is embedded;
- footnotes: ``text[^1]`` and ``[^1]: the note`` at the end;
- garden notes also get Obsidian's ``[[links]]``, ``![[embeds]]``, ``> [!claude]``
  boxes, and words in single [brackets] (words added to a quote of theirs) marked.
"""

import html
import re
from urllib.parse import quote

import markdown

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
EMBED_LINE = re.compile(r"^!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]\s*$")
CAPTION_LINE = re.compile(r"^(?:\*([^*].*?)\*|_([^_].*?)_)\s*$")
VIDEO_EXT = re.compile(r"\.(mp4|mov|webm|m4v)(\?.*)?$", re.I)
YOUTUBE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{6,})")
VIMEO = re.compile(r"vimeo\.com/(\d+)")
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
# Words in single [brackets] that aren't a link or a footnote: words added to a quote of their writing.
INSERTED = re.compile(r"(?<![\[\\!])\[([^\[\]\n^][^\[\]\n]*)\](?![(\[\]:])")
CALLOUT = re.compile(
    r"<blockquote>\s*<p>\[!(\w+)\][ \t]*([^\n<]*)(?:\n|<br />\n?)?", re.S
)
FENCE = re.compile(r"^(```|~~~)")


def strip_frontmatter(text: str) -> str:
    match = FRONTMATTER.match(text)
    return text[match.end() :] if match else text


def inline(text: str) -> str:
    out = markdown.markdown(text, extensions=[])
    return re.sub(r"^<p>(.*)</p>$", r"\1", out.strip(), flags=re.S)


def media_html(src: str, alt: str) -> str:
    src_attr = html.escape(src, quote=True)
    if found := YOUTUBE.search(src):
        return (
            f'<div class="embed"><iframe src="https://www.youtube-nocookie.com/embed/{found.group(1)}" '
            'allow="fullscreen; picture-in-picture" allowfullscreen loading="lazy"></iframe></div>'
        )
    if found := VIMEO.search(src):
        return (
            f'<div class="embed"><iframe src="https://player.vimeo.com/video/{found.group(1)}" '
            'allow="fullscreen; picture-in-picture" allowfullscreen loading="lazy"></iframe></div>'
        )
    if VIDEO_EXT.search(src):
        return (
            f'<video src="{src_attr}" controls playsinline preload="metadata"></video>'
        )
    return f'<img src="{src_attr}" alt="{html.escape(alt, quote=True)}" loading="lazy">'


def figures(md: str, garden: bool) -> str:
    """Turn a picture/video line (and the italic line under it) into a <figure>."""
    lines = md.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE.match(line.strip()):
            in_fence = not in_fence
        media = None
        if not in_fence:
            if found := IMAGE_LINE.match(line.strip()):
                media = (found.group(2), found.group(1))
            elif garden and (found := EMBED_LINE.match(line.strip())):
                name = found.group(1).strip()
                if not name.lower().endswith(".md") and "." in name:
                    media = ("/garden-file/" + quote(name), found.group(2) or "")
        if media is None:
            out.append(line)
            i += 1
            continue
        caption = ""
        if i + 1 < len(lines) and (cap := CAPTION_LINE.match(lines[i + 1].strip())):
            caption = cap.group(1) or cap.group(2) or ""
            i += 1
        block = f'<figure class="fig" data-src="{html.escape(media[0], quote=True)}">{media_html(*media)}'
        if caption:
            block += f"<figcaption>{inline(caption)}</figcaption>"
        out.extend(["", block + "</figure>", ""])
        i += 1
    return "\n".join(out)


def garden_marks(md: str) -> str:
    def link(found: re.Match) -> str:
        target, alias = found.group(1).strip(), found.group(2)
        label = html.escape(alias or target)
        return f'<a class="wikilink" data-note="{html.escape(target, quote=True)}" href="#">{label}</a>'

    md = WIKILINK.sub(link, md)
    return INSERTED.sub(
        lambda f: f'<span class="added">[{html.escape(f.group(1))}]</span>', md
    )


def render(text: str, garden: bool = False) -> str:
    md = strip_frontmatter(text)
    md = figures(md, garden)
    if garden:
        md = garden_marks(md)
    out = markdown.markdown(
        md,
        extensions=["footnotes", "tables", "fenced_code", "sane_lists"],
        extension_configs={"footnotes": {"BACKLINK_TEXT": "&#8593;"}},
    )

    def callout(found: re.Match) -> str:
        kind, label = found.group(1).lower(), found.group(2).strip()
        heading = html.escape(label) or {"claude": "Claude's words"}.get(
            kind, kind.capitalize()
        )
        return f'<blockquote class="callout callout-{kind}"><div class="callout-title">{heading}</div><p>'

    return CALLOUT.sub(callout, out)
