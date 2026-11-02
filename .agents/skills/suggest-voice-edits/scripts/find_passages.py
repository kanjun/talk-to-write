"""Search the user's own passages (the voice bank) for words or phrases.

    uv run python .agents/skills/suggest-voice-edits/scripts/find_passages.py "deeply" "understand" --limit 8
    uv run python .agents/skills/suggest-voice-edits/scripts/find_passages.py --kind essay --kind garden "you don't"

Every term is a word or a quoted phrase; a passage scores by how many terms it
contains (finished prose breaks ties). Prints each hit with where it came from.
"""

import argparse
import json
import re
from pathlib import Path

BANK = Path("data/.apps/talk-to-write/voice/passages.jsonl")
PROSE = {"essay": 3, "page": 2, "garden": 2}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("terms", nargs="+")
    parser.add_argument(
        "--kind",
        action="append",
        help="only these kinds (essay, page, garden, obsidian, logseq)",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--bank", type=Path, default=BANK)
    args = parser.parse_args()

    if not args.bank.exists():
        raise SystemExit(
            f"No voice bank at {args.bank}; run build_voice_bank.py first."
        )
    patterns = [re.compile(r"\b" + re.escape(t.lower()) + r"\b") for t in args.terms]
    hits = []
    with args.bank.open() as handle:
        for line in handle:
            p = json.loads(line)
            if args.kind and p.get("kind") not in args.kind:
                continue
            text = p["text"].lower()
            score = sum(1 for pat in patterns if pat.search(text))
            if score:
                hits.append((score, PROSE.get(p.get("kind", ""), 0), p))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    for score, _, p in hits[: args.limit]:
        when = f", {p['date']}" if p.get("date") else ""
        print(
            f"[{p.get('kind')}] {p.get('title')}{when}  ({score}/{len(patterns)})\n  {p['text']}\n  source: {p.get('source')}\n"
        )
    if not hits:
        print("No passages match.")


if __name__ == "__main__":
    main()
