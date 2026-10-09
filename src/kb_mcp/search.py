from collections.abc import Iterable
from dataclasses import dataclass

MAX_SNIPPETS_PER_FILE = 3
SNIPPET_CHARS = 200
PATH_HIT_WEIGHT = 10


@dataclass(frozen=True)
class Hit:
    path: str
    score: int
    snippets: tuple[tuple[int, str], ...]


def _snippets(text: str, terms: list[str]) -> tuple[tuple[int, str], ...]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        lower_line = line.lower()
        if any(term in lower_line for term in terms):
            found.append((number, line.strip()[:SNIPPET_CHARS]))
            if len(found) == MAX_SNIPPETS_PER_FILE:
                break
    return tuple(found)


def search_files(files: Iterable[tuple[str, str]], query: str, scope: str = "", limit: int = 10) -> list[Hit]:
    terms = query.lower().split()
    if not terms:
        return []
    prefix = f"{scope}/" if scope else ""
    hits = []
    for path, text in files:
        if not path.startswith(prefix):
            continue
        lower_path, lower_text = path.lower(), text.lower()
        if not all(term in lower_text or term in lower_path for term in terms):
            continue
        score = sum(lower_text.count(term) for term in terms)
        score += PATH_HIT_WEIGHT * sum(term in lower_path for term in terms)
        hits.append(Hit(path, score, _snippets(text, terms)))
    hits.sort(key=lambda h: (-h.score, h.path))
    return hits[:limit]
