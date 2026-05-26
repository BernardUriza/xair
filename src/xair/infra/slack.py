"""Slack webhook — bare HTTP POST."""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contracts import ActionsIO


def post_to_slack(
    webhook_url: str,
    message: str,
    *,
    actions: ActionsIO | None = None,
    timeout: int = 30,
) -> None:
    """POST a text message to a Slack webhook."""
    masked_url = webhook_url[:40] + "..."
    msg_preview = message[:120] + ("..." if len(message) > 120 else "")
    if actions:
        actions.notice(f"Slack POST -> {masked_url} ({len(message)} chars): {msg_preview}")

    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    status = resp.getcode()
    body = resp.read().decode("utf-8", errors="replace")
    if actions:
        actions.notice(f"Slack response: {status} -- {body}")


def post_blocks_to_slack(
    webhook_url: str,
    blocks: list[dict],
    *,
    fallback_text: str = "",
    actions: ActionsIO | None = None,
    timeout: int = 30,
) -> None:
    """POST a Slack Block Kit message to a webhook.

    `blocks` is a list of Block Kit block dicts (header/section/context/divider/etc.).
    `fallback_text` is required by Slack as the screen-reader / notification preview.

    Used by the preflight pipeline (<ticket-id>) to post rich pre-deploy cards
    with separated customer-impact and engineer-summary sections. The simpler
    `post_to_slack` remains for plain-text use cases (changelog, alerts).
    """
    masked_url = webhook_url[:40] + "..."
    if actions:
        actions.notice(f"Slack blocks POST -> {masked_url} ({len(blocks)} blocks)")

    payload_dict: dict = {"blocks": blocks}
    if fallback_text:
        payload_dict["text"] = fallback_text
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    status = resp.getcode()
    body = resp.read().decode("utf-8", errors="replace")
    if actions:
        actions.notice(f"Slack response: {status} -- {body}")
