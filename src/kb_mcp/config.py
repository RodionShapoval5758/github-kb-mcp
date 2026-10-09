import os
from dataclasses import dataclass

MIN_TOKEN_LENGTH = 32


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    github_token: str
    kb_repo: str
    kb_branch: str
    kb_root_index: str
    bearer_tokens: tuple[str, ...]


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable {name}")
    return value


def _optional(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def load_settings() -> Settings:
    github_token = _require("GITHUB_TOKEN")

    kb_repo = _require("KB_REPO")
    owner, _, name = kb_repo.partition("/")
    if not owner or not name or "/" in name:
        raise ConfigError("KB_REPO must look like 'owner/name'")

    tokens = tuple(t.strip() for t in _require("MCP_BEARER_TOKENS").split(",") if t.strip())
    if not tokens or any(len(t) < MIN_TOKEN_LENGTH for t in tokens):
        raise ConfigError(f"Every MCP_BEARER_TOKENS entry must be at least {MIN_TOKEN_LENGTH} characters")

    return Settings(
        github_token=github_token,
        kb_repo=kb_repo,
        kb_branch=_optional("KB_BRANCH", "main"),
        kb_root_index=_optional("KB_ROOT_INDEX", "INDEX.md"),
        bearer_tokens=tokens,
    )
