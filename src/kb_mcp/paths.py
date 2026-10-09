from .errors import InvalidPathError


def normalize_path(path: str) -> str:
    parts: list[str] = []
    for part in path.strip().replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part != "..":
            parts.append(part)
        elif parts:
            parts.pop()
        else:
            raise InvalidPathError(f"Paths must stay inside the knowledge base: {path!r}")
    return "/".join(parts)
