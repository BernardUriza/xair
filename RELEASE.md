# Release Process — xair

This document is the canonical playbook for cutting a release of **xair**. Every release should be reproducible by anyone with the repo + write access to anaconda.org/bernardurizaorozco.

The release is tag-driven: pushing a `v*` tag triggers [`/.github/workflows/publish.yml`](.github/workflows/publish.yml) which builds the sdist + conda package, creates the GitHub Release, and uploads to anaconda.org.

---

## Pre-release checklist

Before pushing the tag:

- [ ] **All work merged to `main`.** No work-in-progress branches outside what the tag will capture.
- [ ] **`pyproject.toml` `version` reflects the intended release** (`0.0.x` → `0.0.x+1` etc.).
- [ ] **`src/xair/__init__.py` `__version__` matches `pyproject.toml`.** (The publish workflow guards against drift, but catching it locally is faster.)
- [ ] **`CHANGELOG.md` has an entry for this version** with `Added` / `Changed` / `Removed` / `Fixed` / `Deprecated` sections as applicable. Move from `[Unreleased]` into the new version heading.
- [ ] **Tests pass locally** (`pytest`).
- [ ] **Smoke test from CLEAN env** (the Golden Rule — see "External-user smoke test" below).

If any item is missing, fix it BEFORE cutting the tag. Do not push a tag and "iterate" after — anaconda.org will have already published the artifact.

---

## Cut the release

```bash
# Assume version is being bumped 0.0.1 → 0.0.2.

# 1. Bump version in pyproject.toml
perl -i -pe 's/^version = "0\.0\.1"/version = "0.0.2"/' pyproject.toml

# 2. Update src/xair/__init__.py
perl -i -pe 's/^__version__ = "0\.0\.1"/__version__ = "0.0.2"/' src/xair/__init__.py

# 3. Update CHANGELOG.md
# (Manual: move [Unreleased] entries into a new "## [0.0.2] — YYYY-MM-DD" section.)

# 4. Commit
git add pyproject.toml src/xair/__init__.py CHANGELOG.md
git commit -m "chore: bump xair to 0.0.2

See CHANGELOG.md for the entries that ship with this version."

# 5. Push to main
git push origin main

# 6. Tag + push
git tag v0.0.2
git push origin v0.0.2
```

GitHub Actions will then:

1. Verify `pyproject.toml` version matches the tag (`v0.0.2` → `0.0.2`). Fails the run if not.
2. Build sdist and create a GitHub Release with the tarball as asset.
3. Build the conda `noarch:python` package (`xair-0.0.2-py_0.conda`).
4. `anaconda upload --skip-existing` to https://anaconda.org/bernardurizaorozco/xair.

Watch progress: `gh run watch --repo BernardUriza/xair`

---

## External-user smoke test (the Golden Rule)

**Before cutting any release, run this from a CLEAN env. If anything fails or requires knowledge not in the README, fix the package or the README first.**

```bash
# Fresh env, no inherited state
conda create -n xair-smoke -y python=3.12
conda activate xair-smoke

# Install ONLY from anaconda.org bernardurizaorozco channel + conda-forge for deps
conda install -y -c bernardurizaorozco -c conda-forge xair

# Imports the README promises
python -c "
import xair
from xair.dispatch import dispatch
from xair.command_registry import command, CommandContext, DispatchResult, register_ack_meta
print('xair', xair.__version__)
print('public API OK')
"

# CLI smoke (will print usage and exit non-zero — expected for framework w/o registered commands)
python -m xair || true

# Cleanup
conda deactivate
conda env remove -n xair-smoke -y
```

If any line fails on `import` or raises an `AttributeError` for a documented attribute, the package is broken and the release MUST NOT ship.

---

## After the release

- [ ] Verify on anaconda.org: `anaconda show bernardurizaorozco/xair` (should list the new version).
- [ ] Verify GitHub Release exists with sdist asset: <https://github.com/BernardUriza/xair/releases>.
- [ ] Test `conda install -c bernardurizaorozco xair=<new-version>` actually pulls the new artifact.
- [ ] Update any consumer that requires this new version (e.g., `bair`'s `pyproject.toml` if it pins `xair>=`).

---

## Hotfix flow

If `0.0.2` has a critical bug after publishing:

1. Bump `0.0.2 → 0.0.3` following the same flow. **Do NOT delete the broken artifact** — leave it for traceability.
2. Document the bug in `CHANGELOG.md` under `[0.0.2]` as a known-issue note, and the fix under `[0.0.3]`.
3. If consumers pinned `xair==0.0.2`, ping them to upgrade.

Never force-push a tag. Never re-publish the same version with different content (anaconda's `--skip-existing` will refuse anyway).

---

## Authoring rules

- Do not mix packaging changes with refactors of pipeline logic in the same commit.
- Do not change conda recipe AND pyproject version in the same commit unless they must move together (recipe is sed'd at build time, so they can drift in source — but they shouldn't on `main`).
- Conventional commits: `chore(release):` for the version-bump commit; `feat:` / `fix:` etc. for the actual work.
