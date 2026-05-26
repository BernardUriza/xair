"""Load engineering rules based on changed files in the PR."""

from __future__ import annotations

import json
import re

from ..domain.rules_triage import select_rules
from ..domain.models import PRIdentifier
from ..infra.constants import DEFAULT_MAX_RULES_BYTES, RULES_CONTEXT, RULES_DIR, SELECTED_RULES, TEMPLATES_DIR
from ..contracts import ActionsIO, FileStore, GitHubClient, LlmProvider
from ..log import logger

_CONDENSE_PROMPT_PATH = TEMPLATES_DIR.parent / "condense-rules.md"
_CONDENSE_MAX_TOKENS = 4096  # GPT-5.4 uses ~1K for reasoning, need headroom for actual output


def _load_rules(rule_names: list[str]) -> tuple[str, int]:
    """Read rule files with byte budget. Returns (text, total_bytes)."""
    rules_dir = RULES_DIR
    logger.debug(f"  [rules] rules_dir={rules_dir} exists={rules_dir.exists()}")
    total_bytes = 0
    parts: list[str] = ["## Engineering Rules (selected for this PR)", ""]

    for name in rule_names:
        rule_path = rules_dir / name
        if not rule_path.exists():
            parts.append(f"# {name} -- not found")
            continue

        content = rule_path.read_text(encoding="utf-8")
        file_bytes = len(content.encode("utf-8"))

        if total_bytes + file_bytes > DEFAULT_MAX_RULES_BYTES:
            parts.append(f"# Skipped {name} (would exceed {DEFAULT_MAX_RULES_BYTES} byte budget)")
            continue

        if name == "security.md":
            content = re.sub(r"## Local Secrets Map.*?(?=\n## [^L]|\Z)", "", content, flags=re.DOTALL)

        parts.append(content)
        parts.append("")
        total_bytes += file_bytes

    if total_bytes < 1000:
        parts.extend([
            "",
            f"WARNING: Only {total_bytes} bytes of rules loaded (expected 5000+).",
            "The reviewer is running WITHOUT security, multi-tenancy, or data-privacy rules.",
        ])

    return "\n".join(parts) + "\n", total_bytes


def gather_rules(
    pr: PRIdentifier, github: GitHubClient, store: FileStore, actions: ActionsIO,
) -> tuple[str, str]:
    """Triage and load rules. Returns (rules_context, selected_rules)."""
    try:
        changed_files = github.run_gh(
            "api", f"repos/{pr.repo}/pulls/{pr.number}/files",
            "--jq", ".[].filename", "--paginate",
        )
    except Exception:
        changed_files = ""

    rule_names = select_rules(changed_files)

    selected_text = "\n".join(rule_names) + "\n"
    store.write(SELECTED_RULES, selected_text)
    logger.debug("--- Selected rules ---")
    for name in rule_names:
        logger.debug(f"  {name}")

    rules_text, total_bytes = _load_rules(rule_names)
    store.write(RULES_CONTEXT, rules_text)
    actions.notice(f"Loaded {len(rule_names)} rules ({total_bytes} bytes, budget: {DEFAULT_MAX_RULES_BYTES})")
    return rules_text, selected_text


def condense_rules(rules_text: str, llm: LlmProvider, actions: ActionsIO) -> str:
    """Compress raw rules markdown into dense plain text via a cheap LLM call.

    ~20KB of markdown → ~2KB of plain text. Preserves every constraint and
    threshold while dropping formatting, examples, and prose.
    Falls back to the original text if the LLM call fails.
    """
    if len(rules_text) < 2000:
        return rules_text

    if not _CONDENSE_PROMPT_PATH.exists():
        actions.warning(f"Condense prompt not found at {_CONDENSE_PROMPT_PATH} — using raw rules")
        return rules_text

    system_prompt = _CONDENSE_PROMPT_PATH.read_text(encoding="utf-8").strip()

    try:
        result = llm.call(
            system=system_prompt,
            user=rules_text,
            model="",
            max_tokens=_CONDENSE_MAX_TOKENS,
            temperature=0.0,
            json_mode=False,
        )
    except Exception as e:
        actions.warning(f"Rules condensation failed — using raw rules: {e}")
        return rules_text

    # The LLM provider returns either:
    # - {"_raw": "text"} when response is not valid JSON (expected for plain text)
    # - a parsed dict if the response happened to be valid JSON
    # - a parsed str if the response was a JSON string like "text"
    condensed = ""
    if isinstance(result, str):
        condensed = result
    elif isinstance(result, dict):
        condensed = result.get("_raw", "")
        if not condensed:
            # Shouldn't happen, but dump the dict as fallback
            condensed = json.dumps(result)
    else:
        condensed = str(result)

    raw_bytes = len(rules_text.encode("utf-8"))
    condensed_bytes = len(condensed.encode("utf-8"))

    # If condensation produced less than 5% of original, something went wrong
    if condensed_bytes < raw_bytes * 0.05:
        actions.warning(
            f"Rules condensation suspect: {raw_bytes} → {condensed_bytes} bytes "
            f"— result too short, falling back to raw rules. "
            f"LLM returned: {repr(condensed[:200])}"
        )
        return rules_text

    ratio = round((1 - condensed_bytes / raw_bytes) * 100)
    actions.notice(f"Rules condensed: {raw_bytes} → {condensed_bytes} bytes ({ratio}% reduction)")
    return f"## Engineering Rules (condensed)\n{condensed}\n"
