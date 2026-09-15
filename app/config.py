"""Single source of configuration for Claré.

Every tunable value in the system is defined here and nowhere else. Modules
import ``get_settings()``; they never read ``os.environ`` or open ``.env``
themselves. If you find yourself reaching for an environment variable outside
this file, add it to :class:`Settings` instead.

Nothing in this file encodes a fact about French administrative procedure.
Deadlines, fees, document lists and eligibility rules come from retrieved
source documents only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Repository root: this file is app/config.py, so two parents up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when a setting is missing or cannot be interpreted."""


def _raw(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ConfigError(
            f"Required setting {name} is not set. Copy .env.example to .env "
            f"and fill it in."
        )
    return value.strip()


def _as_int(name: str, default: str) -> int:
    value = _raw(name, default)
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {value!r}") from exc


def _as_float(name: str, default: str) -> float:
    value = _raw(name, default)
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _as_bool(name: str, default: str) -> bool:
    value = _raw(name, default).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {value!r}")


def _as_path(name: str, default: str) -> Path:
    value = Path(_raw(name, default)).expanduser()
    return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def _as_list(name: str, default: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _raw(name, default).split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one run of the system."""

    # Chat provider. "groq" calls a hosted API; "ollama" runs locally.
    # The rest of the system talks to app.llm and never branches on this.
    llm_provider: str
    groq_base_url: str
    groq_model: str
    groq_reasoning_effort: str
    groq_api_key: str = field(repr=False)  # never shown in logs or tracebacks

    # Local chat, used when llm_provider == "ollama".
    ollama_base_url: str
    ollama_chat_model: str

    # Embeddings. Always local: no hosted provider in use here serves a
    # multilingual embedding model.
    embed_provider: str
    embed_model: str

    # Retrieval
    retrieval_k: int
    max_passages_per_document: int
    dense_weight: float
    keyword_weight: float
    relevance_threshold: float
    answerability_check: bool

    # Chunking
    chunk_target_tokens: int
    chunk_max_tokens: int
    embed_max_tokens: int
    embed_batch_size: int
    embed_token_budget: int
    embed_half_precision: bool
    collection_name: str

    # Source feed
    feed_version: str
    feed_url_template: str
    feed_segments: tuple[str, ...]

    # Paths
    data_dir: Path
    chroma_dir: Path

    # Behaviour
    answer_language: str
    debug_panel: bool

    # How much source history to keep, and how much is enough to answer a
    # question about the past. The second is deliberately separate: keeping
    # versions is cheap, having enough of them to be useful takes weeks.
    retention_days: int
    retention_versions: int
    minimum_historical_days: int
    minimum_historical_versions: int

    # Bug reports. Delivery is optional: a report is always written to disk
    # first, and the interface never claims an email was sent unless one was.
    bug_email_to: str
    bug_email_from: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str = field(repr=False)  # never shown in logs or tracebacks
    smtp_starttls: bool

    # Derived paths, filled in by __post_init__.
    raw_dir: Path = field(init=False)
    extracted_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    bugs_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        # frozen=True blocks normal assignment, so set derived fields directly.
        object.__setattr__(self, "raw_dir", self.data_dir / "raw")
        object.__setattr__(self, "extracted_dir", self.data_dir / "extracted")
        object.__setattr__(self, "cache_dir", self.data_dir / "cache")
        object.__setattr__(self, "bugs_dir", self.data_dir / "bugs")

    def feed_url(self, segment: str) -> str:
        """Download URL for one audience segment of the DILA feed."""
        if segment not in self.feed_segments:
            raise ConfigError(
                f"Unknown feed segment {segment!r}; configured segments are "
                f"{', '.join(self.feed_segments)}"
            )
        return self.feed_url_template.format(version=self.feed_version, segment=segment)

    @property
    def email_configured(self) -> bool:
        """Whether a report can actually be delivered anywhere.

        Checked before the interface says anything about email, so it never
        promises delivery that was never configured.
        """
        return bool(self.smtp_host and self.bug_email_to)

    @property
    def groq_api_key_masked(self) -> str:
        """Safe to print: enough to identify the key, not enough to use it."""
        if not self.groq_api_key:
            return "(unset)"
        return f"{self.groq_api_key[:7]}...{self.groq_api_key[-4:]}"

    def ensure_dirs(self) -> None:
        """Create the data directories this run will write to."""
        for path in (self.data_dir, self.raw_dir, self.extracted_dir,
                     self.cache_dir, self.chroma_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process.

    Reads ``.env`` if present. Real environment variables win over ``.env``,
    so a one-off override works without editing the file.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    settings = Settings(
        llm_provider=_raw("LLM_PROVIDER", "groq").lower(),
        groq_base_url=_raw("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        groq_model=_raw("GROQ_MODEL", ""),
        groq_reasoning_effort=_raw("GROQ_REASONING_EFFORT", "low").lower(),
        groq_api_key=_raw("GROQ_API_KEY", ""),
        ollama_base_url=_raw("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_chat_model=_raw("OLLAMA_CHAT_MODEL", ""),
        embed_provider=_raw("EMBED_PROVIDER", "sentence-transformers").lower(),
        embed_model=_raw("EMBED_MODEL", "BAAI/bge-m3"),
        retrieval_k=_as_int("RETRIEVAL_K", "6"),
        max_passages_per_document=_as_int("MAX_PASSAGES_PER_DOCUMENT", "2"),
        dense_weight=_as_float("DENSE_WEIGHT", "0.5"),
        keyword_weight=_as_float("KEYWORD_WEIGHT", "0.5"),
        relevance_threshold=_as_float("RELEVANCE_THRESHOLD", "0.35"),
        answerability_check=_as_bool("ANSWERABILITY_CHECK", "true"),
        chunk_target_tokens=_as_int("CHUNK_TARGET_TOKENS", "600"),
        chunk_max_tokens=_as_int("CHUNK_MAX_TOKENS", "1200"),
        embed_max_tokens=_as_int("EMBED_MAX_TOKENS", "8192"),
        embed_batch_size=_as_int("EMBED_BATCH_SIZE", "128"),
        embed_token_budget=_as_int("EMBED_TOKEN_BUDGET", "24576"),
        embed_half_precision=_as_bool("EMBED_HALF_PRECISION", "true"),
        collection_name=_raw("COLLECTION_NAME", "reperes"),
        feed_version=_raw("FEED_VERSION", "3.5"),
        feed_url_template=_raw(
            "FEED_URL_TEMPLATE",
            "https://lecomarquage.service-public.gouv.fr"
            "/vdd/{version}/{segment}/zip/vosdroits-latest.zip",
        ),
        feed_segments=_as_list("FEED_SEGMENTS", "part,pro"),
        data_dir=_as_path("DATA_DIR", "./data"),
        chroma_dir=_as_path("CHROMA_DIR", "./data/chroma"),
        answer_language=_raw("ANSWER_LANGUAGE", "auto").lower(),
        debug_panel=_as_bool("DEBUG_PANEL", "true"),
        retention_days=_as_int("RETENTION_DAYS", "90"),
        retention_versions=_as_int("RETENTION_VERSIONS", "20"),
        minimum_historical_days=_as_int("MINIMUM_HISTORICAL_DAYS", "30"),
        minimum_historical_versions=_as_int("MINIMUM_HISTORICAL_VERSIONS", "3"),
        bug_email_to=_raw("BUG_REPORT_EMAIL_TO", ""),
        bug_email_from=_raw("BUG_REPORT_EMAIL_FROM", ""),
        smtp_host=_raw("SMTP_HOST", ""),
        smtp_port=_as_int("SMTP_PORT", "587"),
        smtp_username=_raw("SMTP_USERNAME", ""),
        smtp_password=_raw("SMTP_PASSWORD", ""),
        smtp_starttls=_as_bool("SMTP_STARTTLS",
                               _raw("SMTP_USE_TLS", "true")),
    )

    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    if s.retrieval_k < 1:
        raise ConfigError("RETRIEVAL_K must be at least 1")
    if not 0.0 <= s.relevance_threshold <= 1.0:
        raise ConfigError("RELEVANCE_THRESHOLD must be between 0 and 1")
    if s.dense_weight < 0 or s.keyword_weight < 0:
        raise ConfigError("DENSE_WEIGHT and KEYWORD_WEIGHT must not be negative")
    if s.dense_weight + s.keyword_weight == 0:
        raise ConfigError("DENSE_WEIGHT and KEYWORD_WEIGHT cannot both be zero")
    if s.chunk_max_tokens < s.chunk_target_tokens:
        raise ConfigError("CHUNK_MAX_TOKENS must be at least CHUNK_TARGET_TOKENS")
    if s.embed_max_tokens < s.chunk_max_tokens:
        raise ConfigError(
            "EMBED_MAX_TOKENS must be at least CHUNK_MAX_TOKENS, otherwise "
            "chunks would be silently truncated when embedded"
        )
    if s.retention_versions < 2:
        raise ConfigError(
            "RETENTION_VERSIONS must be at least 2: pruning to a single "
            "version would leave nothing to roll back to")
    if s.retention_days < 1:
        raise ConfigError("RETENTION_DAYS must be at least 1")
    if s.answer_language not in {"auto", "en", "fr"}:
        raise ConfigError("ANSWER_LANGUAGE must be one of: auto, en, fr")
    if not s.feed_segments:
        raise ConfigError("FEED_SEGMENTS must name at least one segment")
    if s.llm_provider not in {"groq", "ollama"}:
        raise ConfigError("LLM_PROVIDER must be one of: groq, ollama")
    if s.llm_provider == "groq":
        if not s.groq_api_key:
            raise ConfigError(
                "GROQ_API_KEY is required when LLM_PROVIDER=groq. Put it in "
                ".env, which is gitignored; never in source."
            )
        if not s.groq_model:
            raise ConfigError("GROQ_MODEL is required when LLM_PROVIDER=groq")
        # Groq's agentic models browse the web. That would let facts reach an
        # answer without passing through retrieval, which is the one thing this
        # system must never do.
        if s.groq_reasoning_effort not in {"low", "medium", "high"}:
            raise ConfigError(
                "GROQ_REASONING_EFFORT must be one of: low, medium, high"
            )
        if s.groq_model.startswith("groq/compound"):
            raise ConfigError(
                f"GROQ_MODEL={s.groq_model!r} is an agentic model with built-in "
                "web access. It can introduce facts that never came from the "
                "corpus, so it is refused. Choose a plain chat model."
            )
    if s.llm_provider == "ollama" and not s.ollama_chat_model:
        raise ConfigError("OLLAMA_CHAT_MODEL is required when LLM_PROVIDER=ollama")
