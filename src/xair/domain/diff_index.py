"""Diff-aware anchor resolver for PR review comments.

GitHub's `POST /pulls/{N}/reviews` endpoint is atomic: if any entry in
`comments[]` points at a line outside the diff, the whole review is rejected
with 422 "Line could not be resolved". DiffIndex lets the publisher partition
findings into inline-valid vs file-level BEFORE the POST, so a single bad
anchor cannot sink the rest.

Reference: https://docs.github.com/en/rest/pulls/reviews
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class LineAnchor:
    """A (line, side) pair that GitHub's review comment API can resolve."""
    line: int
    side: str  # "RIGHT" (post-change) or "LEFT" (pre-change)


@dataclass(frozen=True, slots=True)
class _Range:
    start: int
    end: int
    side: str


class DiffIndex:
    """Maps (path, line, side) to whether a review comment can anchor there.

    `side` semantics match GitHub's diff model:
      - RIGHT: the file after the change (additions + context lines)
      - LEFT:  the file before the change (deletions + context lines)

    An empty diff produces an empty index; every call returns "unresolvable",
    which causes every finding to demote to file-level — safe by default.
    """

    def __init__(self, patch_text: str) -> None:
        self._ranges: dict[str, list[_Range]] = {}
        if not patch_text:
            return
        try:
            from unidiff import PatchSet
            patch = PatchSet.from_string(patch_text)
        except Exception:
            # Unparseable diff -- index stays empty; all findings demote.
            return
        for pf in patch:
            path = self._file_path(pf)
            if not path:
                continue
            for hunk in pf:
                if hunk.target_length > 0:
                    self._ranges.setdefault(path, []).append(
                        _Range(hunk.target_start,
                               hunk.target_start + hunk.target_length - 1,
                               "RIGHT")
                    )
                if hunk.source_length > 0:
                    self._ranges.setdefault(path, []).append(
                        _Range(hunk.source_start,
                               hunk.source_start + hunk.source_length - 1,
                               "LEFT")
                    )

    @staticmethod
    def _file_path(pf) -> str:
        """Extract the canonical path from a unidiff PatchedFile.

        Prefer the post-change name. Fall back to the pre-change name for
        pure deletions (target is /dev/null).
        """
        target = getattr(pf, "target_file", "") or ""
        source = getattr(pf, "source_file", "") or ""
        if target and target != "/dev/null":
            return target[2:] if target.startswith("b/") else target
        if source and source != "/dev/null":
            return source[2:] if source.startswith("a/") else source
        return ""

    def has_file(self, path: str) -> bool:
        """True when the diff touches this path at all."""
        return bool(path) and path in self._ranges

    def resolve(self, path: str, line: int, side: str = "RIGHT") -> bool:
        """True when GitHub can anchor a comment at (path, line, side)."""
        if line <= 0 or not path:
            return False
        side = side.upper()
        for r in self._ranges.get(path, ()):
            if r.side == side and r.start <= line <= r.end:
                return True
        return False

    def snap(self, path: str, line: int, window: int = 0) -> Optional[LineAnchor]:
        """Return a valid LineAnchor at or near (path, line).

        1. If the line sits inside any hunk, return it unchanged. RIGHT wins
           ties because additions are more useful targets for code review
           than deletions.
        2. Otherwise, if window > 0, snap to the nearest hunk edge within
           `window` lines. RIGHT still wins ties at equal distance.
        3. Else return None -- the finding should be demoted to file-level.
        """
        if line <= 0 or not path:
            return None
        ranges = self._ranges.get(path, ())
        if not ranges:
            return None

        # Strict: exact line inside a hunk.
        inside_right = any(r.side == "RIGHT" and r.start <= line <= r.end for r in ranges)
        if inside_right:
            return LineAnchor(line, "RIGHT")
        inside_left = any(r.side == "LEFT" and r.start <= line <= r.end for r in ranges)
        if inside_left:
            return LineAnchor(line, "LEFT")

        if window <= 0:
            return None

        # Lenient: snap to nearest edge within window.
        best_key: Optional[tuple[int, int]] = None
        best_anchor: Optional[LineAnchor] = None
        for r in ranges:
            if line < r.start:
                d = r.start - line
                snapped = r.start
            else:  # line > r.end (inside-checks above ruled out equality)
                d = line - r.end
                snapped = r.end
            if d > window:
                continue
            side_priority = 0 if r.side == "RIGHT" else 1
            key = (d, side_priority)
            if best_key is None or key < best_key:
                best_key = key
                best_anchor = LineAnchor(snapped, r.side)
        return best_anchor
