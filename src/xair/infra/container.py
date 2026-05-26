"""Simple DI container -- a dataclass, not a framework.

Extended with optional deep_llm for dual-provider setups
(GPT as primary reviewer, Claude for deep analysis).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..contracts import ActionsIO, FileStore, GitHubClient, IssueTrackerClient, LlmProvider
from .actions_io import GitHubActionsIO
from .file_store import TmpFileStore
from .github_provider import SubprocessGitHubClient
from .openai_provider import OpenAIProvider
from ..log import logger


@dataclass(slots=True)
class Container:
    """Holds provider instances the pipeline needs.

    ``deep_llm`` is optional -- used for Claude deep analysis when
    the primary ``llm`` is GPT. Falls back to ``llm`` if not set.
    ``tracker`` is optional -- only needed for the resolve pipeline.
    """

    llm: LlmProvider
    github: GitHubClient
    store: FileStore
    actions: ActionsIO
    deep_llm: LlmProvider | None = None
    tracker: IssueTrackerClient | None = None

    def get_deep_llm(self) -> LlmProvider:
        """Return the deep analysis provider, falling back to the primary LLM."""
        return self.deep_llm if self.deep_llm is not None else self.llm

    @classmethod
    def production(cls) -> Container:
        """Standard production container: GPT primary + Claude deep (if key available).

        Deep analysis provider priority:
        1. AgentSDKProvider (claude-agent-sdk) — Claude Code with repo tools
        2. None — falls back to primary LLM (GPT)

        Golden rule: all Claude usage goes through Agent SDK (Claude Code).
        Raw Anthropic API is never used — Claude without tools is blind.
        """
        deep: LlmProvider | None = None
        # Agent SDK reads CLAUDE_CODE_OAUTH_TOKEN natively (Max-quota OAuth path,
        # the billing route since 2026-05-12 AIR decommission). ANTHROPIC_API_KEY
        # is kept as a fallback for local dev or legacy setups.
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
            try:
                from .agent_sdk_provider import AgentSDKProvider
                deep = AgentSDKProvider()
                logger.info("Deep analysis: ✅ Agent SDK (Claude Code with repo tools)")
            except Exception as e:
                logger.warning(f"::warning::Deep analysis DEGRADED — Agent SDK unavailable ({e}). Review is GPT-only.")
                deep = None
        else:
            logger.warning("::warning::Deep analysis OFF — neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY set. Review is GPT-only.")

        return cls(
            llm=OpenAIProvider(),
            github=SubprocessGitHubClient(),
            store=TmpFileStore(),
            actions=GitHubActionsIO(),
            deep_llm=deep,
        )

    @classmethod
    def resolve_mode(cls) -> Container:
        """Container for the resolve pipeline. Includes an issue tracker client.

        No tracker is bundled with the framework. Consumers wire theirs by
        subclassing Container and overriding this classmethod, or via a
        future plugin entry point — the resolve pipeline accepts any object
        conforming to :class:`IssueTrackerClient`.

        LLM is not needed for resolve mode (Agent SDK handles code gen),
        but Container requires it. Use OpenAI if key is available,
        otherwise a stub that raises on call.
        """
        # No framework-bundled tracker — consumers wire theirs via subclass
        # override or plugin entry point. See classmethod docstring.
        tracker: IssueTrackerClient | None = None

        # LLM is optional for resolve mode — only Agent SDK is used
        llm: LlmProvider
        if os.environ.get("OPENAI_API_KEY"):
            llm = OpenAIProvider()
        else:
            from ..domain.exceptions import ConfigError

            class _StubLlm:
                """Placeholder — resolve mode uses Agent SDK, not LlmProvider."""
                last_usage: dict = {}
                def call(self, **_: object) -> dict:
                    raise ConfigError("LLM not available in resolve mode")

            llm = _StubLlm()  # type: ignore[assignment]

        return cls(
            llm=llm,
            github=SubprocessGitHubClient(),
            store=TmpFileStore(),
            actions=GitHubActionsIO(),
            tracker=tracker,
        )
