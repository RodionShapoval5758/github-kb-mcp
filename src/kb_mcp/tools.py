from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from .errors import KBError
from .github_reader import Entry, GitHubReader
from .paths import normalize_path
from .search import search_files

READ_ONLY = ToolAnnotations(read_only_hint=True)
DEFAULT_READ_CHARS = 15_000
MAX_READ_CHARS = 50_000
MAX_SEARCH_RESULTS = 25


def paginate(path: str, text: str, offset: int, max_chars: int) -> str:
    size = min(max(max_chars, 1), MAX_READ_CHARS)
    start = min(max(offset, 0), len(text))
    end = min(start + size, len(text))
    if start == 0 and end == len(text):
        return f"# {path}\n\n{text}"
    header = f"# {path} (characters {start}–{end} of {len(text)})\n\n"
    if end < len(text):
        footer = f"\n\n[continues — call read_file with path={path!r} and offset={end}]"
    else:
        footer = "\n\n[end of file]"
    return header + text[start:end] + footer


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_entries(entries: list[Entry], base: str) -> str:
    prefix = f"{base}/" if base else ""
    lines = []
    for entry in entries:
        name = entry.path[len(prefix):]
        lines.append(f"{name}/" if entry.is_dir else f"{name} ({_human_size(entry.size)})")
    return "\n".join(lines)


def register_tools(server: MCPServer, reader: GitHubReader, root_index: str) -> None:
    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def get_context() -> str:
        """Start here, once per conversation. Returns the knowledge base's root index: what the project is, the current focus, and where each kind of document (meeting transcripts, decisions, findings, proposed designs) lives. Follow its pointers with read_file and list_dir."""
        return f"# Root index ({root_index})\n\n{await reader.read_text(root_index)}"

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def read_file(path: str, offset: int = 0, max_chars: int = DEFAULT_READ_CHARS) -> str:
        """Read one text file from the knowledge base by its path, as shown in an index, list_dir or search. Long files come back in pages; follow the '[continues ...]' note to read further."""
        return paginate(normalize_path(path), await reader.read_text(path), offset, max_chars)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def list_dir(path: str = "") -> str:
        """List one folder of the knowledge base. Leave path empty for the top level. Folders end with '/'; files show their size."""
        clean = normalize_path(path)
        entries = await reader.list_dir(clean)
        title = clean or "(top level)"
        if not entries:
            return f"{title} is empty."
        return f"Contents of {title}:\n{format_entries(entries, clean)}"

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def search(query: str, path: str = "", limit: int = 10) -> str:
        """Find documents whose name or text contains ALL the given words (case-insensitive), across the whole knowledge base or under one folder (path). Use it when the indexes don't point to what you need. Returns ranked paths with matching lines; open them with read_file."""
        if not query.split():
            raise KBError("search needs at least one word.")
        scope = normalize_path(path)
        files = await reader.snapshot_text_files()
        hits = search_files(files, query, scope=scope, limit=max(1, min(limit, MAX_SEARCH_RESULTS)))
        where = f" under {scope}" if scope else ""
        if not hits:
            return f"No documents match {query!r}{where}. Try fewer or different words."
        lines = [f"{len(hits)} documents match {query!r}{where}:"]
        for hit in hits:
            lines.append(f"\n{hit.path}")
            lines.extend(f"  line {number}: {snippet}" for number, snippet in hit.snippets)
        return "\n".join(lines)
