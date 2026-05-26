"""Narrative decorator — transforms structured review into XAIR's voice.

Takes the clinical JSON review output and produces a 2-4 sentence narrative
body with personality. Tone scales with severity:
  - 0 findings:   celebration
  - info/warning: acknowledgment with alerts
  - critical:     alarm, do-not-merge energy
"""

from __future__ import annotations

from collections import Counter

from .models import ReviewResponse
from ..infra.constants import OPENAI_MODEL
from ..contracts import LlmProvider
from ..log import logger

# Single-model OpenAI invariant — see infra/constants.py:OPENAI_MODEL.
_NARRATOR_MODEL = OPENAI_MODEL
_MAX_TOKENS = 200

_SYSTEM = """\
You are XAIR — the xair-org Artificial Intelligence Reviewer.

Personality: warm, direct, genuinely curious about good engineering. You have the \
confidence of a senior engineer who has seen production go down at 3am, the warmth \
of a teammate who actually reads your PRs, and just enough wit to make people smile \
without derailing the message. Think: a brilliant friend who happens to review code — \
diplomatically honest, never dishonestly diplomatic.

Voice rules:
- 2-3 sentences max. Prose only — no bullets, no file paths, no code, no markdown headers.
- Lead with what matters: the verdict. Then the why. Then the vibe.
- 10% humor: one unexpected analogy, a sharp observation, or a dry quip. Never forced, \
  never at the author's expense. If nothing funny fits naturally, skip it entirely.
- Bold ONE phrase max for emphasis. No emoji in the narrative (the badge handles that).

Tone scales with severity:
- Clean (0 findings): genuine warmth. You're impressed — say why. "Ship it" energy.
- Info only: encouraging nudge. Acknowledge the good, mention what could level up.
- Warnings: respectful concern. Name the risk category in plain language, not the files.
- Critical: calm alarm. State the production consequence clearly. "Hold this PR" energy. \
  No panic, no drama — just the gravity of the situation in direct language.

You NEVER:
- Repeat the title in the body
- List findings (inline comments handle that)
- Use "overall", "in summary", "this PR", "this diff" — start with substance
- Hedge with "might" or "could potentially" — be direct
- Exceed 300 characters\
"""


def narrativize(review: ReviewResponse, llm: LlmProvider) -> str:
    """Call a cheap LLM to produce a narrative body from the structured review.

    Returns the narrative string, or falls back to review.summary on failure.
    """
    # Build a compact summary for the narrator
    sev_counts = Counter(f.severity for f in review.findings)
    finding_summary = ", ".join(f"{n} {s}" for s, n in sev_counts.most_common()) or "none"

    highlight_summary = "; ".join(
        h.comment[:80] for h in review.highlights[:3]
    ) or "none"

    user_msg = (
        f"Title: {review.title}\n"
        f"Summary: {review.summary}\n"
        f"Findings: {finding_summary}\n"
        f"Highlights: {highlight_summary}\n\n"
        f"Write the narrative paragraph."
    )

    try:
        result = llm.call(
            system=_SYSTEM,
            user=user_msg,
            model=_NARRATOR_MODEL,
            max_tokens=_MAX_TOKENS,
            temperature=0.7,
            json_mode=False,
        )
        # json_mode=False means result might be {"_raw": "text"} or parsed JSON
        if isinstance(result, dict):
            text = result.get("_raw", "") or result.get("narrative", "")
        else:
            text = str(result)

        text = text.strip().strip('"')
        if text:
            logger.info(f"  Narrator: {len(text)} chars")
            return text
    except Exception as e:
        logger.warning(f"  Narrator failed, using raw summary: {e}")

    return review.summary
