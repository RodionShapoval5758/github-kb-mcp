import asyncio
import base64
import io
import logging
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .errors import InvalidPathError, KBNotFoundError, KBUnavailableError
from .paths import normalize_path

GITHUB_API = "https://api.github.com"
MAX_TEXT_FILE_BYTES = 2_000_000
REPO_UNREACHABLE = (
    "The knowledge base repository or branch is not reachable. "
    "Check KB_REPO, KB_BRANCH and the GitHub token's repository access."
)

LOW_RATE_LIMIT = 100
STATUS_HINTS = {
    401: "GITHUB_TOKEN is invalid or expired",
    403: "GITHUB_TOKEN lacks Contents read access to KB_REPO, or GitHub's rate limit is exhausted",
    429: "GitHub is rate limiting this server",
}

log = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _warn_if_rate_limit_low(response: httpx.Response) -> None:
    try:
        remaining = int(response.headers["x-ratelimit-remaining"])
    except (KeyError, ValueError):
        return
    if remaining < LOW_RATE_LIMIT:
        reset = response.headers.get("x-ratelimit-reset", "")
        resets_at = datetime.fromtimestamp(int(reset), UTC).strftime("%H:%M:%SZ") if reset.isdigit() else "unknown"
        log.warning("GitHub rate limit low: %d requests left, resets at %s", remaining, resets_at)


@dataclass(frozen=True)
class Entry:
    path: str
    is_dir: bool
    size: int


def is_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:8192]


def _extract_text_files(archive_bytes: bytes) -> list[tuple[str, str]]:
    files = []
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.size > MAX_TEXT_FILE_BYTES:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            raw = handle.read()
            if is_binary(raw):
                continue
            # GitHub tarballs wrap everything in one "<owner>-<repo>-<sha>/" folder.
            _, _, path = member.name.partition("/")
            files.append((path, raw.decode("utf-8", errors="replace")))
    return files


class GitHubReader:
    def __init__(self, repo: str, branch: str, token: str, client: httpx.AsyncClient | None = None):
        self._repo = repo
        self._branch = branch
        self._client = client or httpx.AsyncClient(
            base_url=GITHUB_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
            follow_redirects=True,
        )

    async def _get(self, url: str, *, what: str | None, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = await self._client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("GitHub GET %s failed after %d ms: %r", url, _elapsed_ms(started), exc)
            raise KBUnavailableError("Could not reach GitHub. Try again in a moment.") from exc
        elapsed = _elapsed_ms(started)
        _warn_if_rate_limit_low(response)
        if response.status_code == 404:
            log.warning("GitHub GET %s -> 404 in %d ms: %s", url, elapsed, response.text[:300])
            # what=None marks a request whose target always exists (the branch itself); GitHub also answers 404
            # when the token can't see a private repo, so a 404 there is a config problem, not a missing file.
            if what is None:
                raise KBUnavailableError(REPO_UNREACHABLE)
            raise KBNotFoundError(f"Not found in the knowledge base: {what}. Use list_dir or search to find the right path.")
        if response.is_error:
            log.error(
                "GitHub GET %s -> %d in %d ms: %s (request_id=%s, rate limit remaining %s) %s",
                url,
                response.status_code,
                elapsed,
                STATUS_HINTS.get(response.status_code, "unexpected response from GitHub"),
                response.headers.get("x-github-request-id", "none"),
                response.headers.get("x-ratelimit-remaining", "unknown"),
                response.text[:300],
            )
            raise KBUnavailableError(
                "The knowledge base is temporarily unavailable (GitHub refused or rate-limited the request)."
            )
        remaining = response.headers.get("x-ratelimit-remaining")
        log.info(
            "GitHub GET %s -> %d in %d ms%s",
            url,
            response.status_code,
            elapsed,
            f" (rate limit remaining {remaining})" if remaining else "",
        )
        return response

    async def read_text(self, path: str) -> str:
        clean = normalize_path(path)
        if not clean:
            raise InvalidPathError("Give a file path, not the repository root. Use list_dir to see the top level.")
        url = f"/repos/{self._repo}/contents/{quote(clean)}"
        data = (await self._get(url, what=clean, params={"ref": self._branch})).json()
        if isinstance(data, list):
            raise InvalidPathError(f"{clean} is a folder. Use list_dir to see what it contains.")
        if data.get("type") != "file":
            raise InvalidPathError(f"{clean} is not a regular file.")
        if data.get("encoding") == "base64":
            raw = base64.b64decode(data["content"])
        else:
            # Files over 1 MB come back without inline content; fetch them raw.
            raw_response = await self._get(
                url, what=clean, params={"ref": self._branch}, headers={"Accept": "application/vnd.github.raw+json"}
            )
            raw = raw_response.content
        if is_binary(raw):
            raise InvalidPathError(f"{clean} is a binary file; only text files can be read.")
        return raw.decode("utf-8", errors="replace")

    async def get_tree(self) -> list[Entry]:
        response = await self._get(
            f"/repos/{self._repo}/git/trees/{quote(self._branch)}",
            what=None,
            params={"recursive": "1"},
        )
        data = response.json()
        if data.get("truncated"):
            log.warning("Git tree for %s is truncated; some entries are missing", self._repo)
        return [
            Entry(path=item["path"], is_dir=item["type"] == "tree", size=item.get("size", 0))
            for item in data["tree"]
            if item["type"] in ("blob", "tree")
        ]

    async def list_dir(self, path: str) -> list[Entry]:
        clean = normalize_path(path)
        entries = await self.get_tree()
        if clean and not any(e.is_dir and e.path == clean for e in entries):
            if any(e.path == clean for e in entries):
                raise InvalidPathError(f"{clean} is a file. Use read_file to read it.")
            raise KBNotFoundError(f"No folder {clean!r} in the knowledge base. Call list_dir with no path for the top level.")
        prefix = f"{clean}/" if clean else ""
        children = [e for e in entries if e.path.startswith(prefix) and "/" not in e.path[len(prefix):]]
        return sorted(children, key=lambda e: (not e.is_dir, e.path.lower()))

    async def snapshot_text_files(self) -> list[tuple[str, str]]:
        started = time.perf_counter()
        response = await self._get(
            f"/repos/{self._repo}/tarball/{quote(self._branch)}",
            what=None,
            timeout=60.0,
        )
        files = await asyncio.to_thread(_extract_text_files, response.content)
        log.info(
            "GitHub tarball: %.1f MB downloaded, %d text files (%.1f MB of text) in %d ms",
            len(response.content) / 1_000_000,
            len(files),
            sum(len(text) for _, text in files) / 1_000_000,
            _elapsed_ms(started),
        )
        return files

    async def check_access(self, root_index: str) -> tuple[str, int]:
        branch = (await self._get(f"/repos/{self._repo}/branches/{quote(self._branch)}", what=None)).json()
        text = await self.read_text(root_index)
        return branch.get("commit", {}).get("sha", "unknown")[:7], len(text)
