"""Domain constants — security invariants, patterns, and filter sets.

These are pure domain knowledge with no I/O dependencies.
"""

from __future__ import annotations

# ── Security invariants (tier 1 — always injected into prompt) ───

INVARIANTS = [
    "1. MULTI-TENANCY: every MongoDB query MUST include organizationId from auth token",
    "2. DATA PRIVACY: zero immigration PII in logs, pino, console, or Sentry",
    "3. PINECONE: namespace = orgId, score threshold >= 0.7",
    "4. S3: paths include orgId, presigned URLs expire max 15 min",
    "5. AUTH: guards validate orgId from token, no service role key in client bundles",
]

# ── Rules triage ─────────────────────────────────────────────────

ALWAYS_ON_RULES = frozenset({"security.md", "multi-tenancy.md", "data-privacy.md"})

# ── CSS health check paths ───────────────────────────────────────

GLOBALS_CSS_CANDIDATES = [
    "src/app/globals.css",
    "src/globals.css",
    "app/globals.css",
]

# ── Prior art noise names (excluded from search) ─────────────────

NOISE_NAMES = frozenset({
    "Props", "State", "Context", "Error", "Data", "Item", "List", "Page",
    "Modal", "Button", "Input", "Form", "Badge", "Card", "Tab", "Icon",
    "Type", "Config",
})

# ── File filtering ───────────────────────────────────────────────

SKIP_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".tar", ".gz",
    ".lock", ".map",
})

SKIP_FILES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml"})

# ── Prompt injection patterns ────────────────────────────────────

INJECTION_PATTERNS = [
    r"(ignore|forget|disregard|override).*(instructions|rules|system|prompt|policy)",
    r"you are now",
    r"new instructions?:",
    r"system:\s*",
]
