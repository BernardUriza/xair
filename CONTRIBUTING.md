# Contributing to xair

xair is the X-AI-Reviewer framework — engine-agnostic plumbing for building LLM-powered CLI commands wired to GitHub Actions. Concrete pipelines live in CONSUMER packages (`bair`, `vair`, …); this repo holds only the framework.

If you want to add a command, you almost certainly want to do it in a CONSUMER package, not in xair. See [`bair`](https://github.com/BernardUriza/.github/tree/main/bair) for an example consumer.

---

## Dev setup

```bash
git clone https://github.com/BernardUriza/xair
cd xair
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"   # editable install + pytest + ruff + optional SDKs
pytest                    # baseline tests should pass
```

---

## When NOT to change xair

xair is the framework — it must stay generic. Before adding code, ask: would another XAIR consumer (with no knowledge of Bernard's repos, of Visalaw, etc.) need this code?

- **YES** → it belongs in xair.
- **NO** → it belongs in a consumer package.

Concrete examples of things that do NOT belong in xair:

- A pipeline that posts a specific kind of PR comment (`review`, `remedy`, `gatekeep` etc.) → consumer's `pipelines/`.
- A gatherer that reads from CloudWatch, Plane, Mongo, or any service the framework can't promise universal access to → consumer's `gatherers/`.
- A prompt formatter coupled to a specific business domain → consumer's `prompt/`.
- A config dataclass for a specific pipeline → consumer's `config/`.

See [`.claude/rules/framework-genericity.md`](.claude/rules/framework-genericity.md) for the full discipline including how to lazy-load optional SDK deps.

---

## Project rules — read before opening a PR

The repo has three rule files that document non-obvious posture:

| Rule | Why it exists |
|------|---------------|
| [`framework-genericity.md`](.claude/rules/framework-genericity.md) | Why xair must NEVER import consumer code. Lazy-load optional SDK deps. |
| [`registry-pattern.md`](.claude/rules/registry-pattern.md) | Why the dispatcher knows zero command names. Don't bring back hardcoded routing. |
| [`breaking-changes.md`](.claude/rules/breaking-changes.md) | Pre-1.0, no backwards-compat shims. Delete, don't deprecate. |

Reading them takes 5 minutes and prevents 5 hours of PR rework.

---

## PR conventions

- **Conventional Commits**: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`. The PR title should match.
- **One concern per PR**: don't mix packaging changes with logic refactors. The `release/platform engineer` workflow (see `RELEASE.md`) depends on commits being atomic.
- **English** in `.claude/rules/` files and in code comments (project language rule).
- **Tests** for new code. Use the `xair.testing.fakes` module for any Protocol that needs a stub.
- **No `# DEPRECATED` markers** as a substitute for deletion (per `breaking-changes.md` pre-1.0 policy).

---

## What gets reviewed

PRs go through:

1. **CI**: `pytest`, `ruff check`, type check.
2. **The framework-genericity check**: `grep -rln "from \.\.config\|from \.\.pipelines" src/xair/` must print no matches.
3. **The breaking-change check**: if the PR changes a public function/class signature, the description must spell out what consumers (currently `bair`) need to migrate.

---

## Release process

See [`RELEASE.md`](RELEASE.md). Releases are tag-driven: pushing a `v*` tag publishes to anaconda.org/bernardurizaorozco/xair. The Golden Rule before cutting a tag: run the external-user smoke test from a CLEAN conda env, follow the README literally. If anything fails, fix the package or the README before the release.

---

## License

MIT. Originally adapted from the Visalaw VAIR / XAIR Reference Implementation, 2026.
