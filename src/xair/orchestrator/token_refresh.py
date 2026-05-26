"""Refresh GitHub App installation tokens mid-run.

GitHub App installation tokens last exactly 1 hour. A multi-step orchestrator
run that crosses that boundary loses auth — git push and gh api calls start
returning ``fatal: could not read Username for 'https://github.com'``.

The fix is to mint a fresh installation token before each push. The App's
private key is durable (lives in the VAIR_APP_PRIVATE_KEY secret); only the
installation token derived from it expires.

Usage from the executor:
    refresher = TokenRefresher.from_env()
    fresh = refresher.token()  # cached if still valid; refreshed otherwise
    os.environ['GH_TOKEN'] = fresh
    # Then configure git http extraheader to use it (see executor.py).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

# PyJWT is the only viable signer for the App JWT (RS256 with PKCS#1 RSA key).
# Imported lazily so module load doesn't fail in environments without it
# (e.g. test runners that mock the refresher).


_GITHUB_API = "https://api.github.com"
# App JWTs expire after 10 minutes. We mint short-lived ones (5 min) so a
# leaked JWT is useless quickly. Installation tokens last 1 hour from issue.
_APP_JWT_TTL_SECONDS = 5 * 60
# Refresh installation tokens proactively when they have <10 min left, so
# a long-running git push doesn't fail mid-network with token expiry.
_REFRESH_MARGIN_SECONDS = 10 * 60


@dataclass
class _CachedToken:
    token: str
    expires_at: float  # epoch seconds


class TokenRefresher:
    """Refresh installation tokens on demand. Caches until near-expiry."""

    def __init__(self, app_id: str, private_key: str, *, owner: str = "xair-org") -> None:
        if not app_id:
            raise ValueError("TokenRefresher: app_id is required.")
        if not private_key:
            raise ValueError("TokenRefresher: private_key is required.")
        self._app_id = app_id
        self._private_key = private_key
        self._owner = owner
        self._installation_id: int | None = None
        self._cached: _CachedToken | None = None

    @classmethod
    def from_env(cls) -> "TokenRefresher":
        """Construct from VAIR_APP_ID + VAIR_APP_PRIVATE_KEY env vars."""
        return cls(
            app_id=os.environ.get("VAIR_APP_ID", ""),
            private_key=os.environ.get("VAIR_APP_PRIVATE_KEY", ""),
        )

    def _make_app_jwt(self) -> str:
        import jwt  # type: ignore[import-not-found]

        now = int(time.time())
        payload = {
            "iat": now - 30,  # backdate to avoid clock skew rejections
            "exp": now + _APP_JWT_TTL_SECONDS,
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def _find_installation_id(self) -> int:
        if self._installation_id is not None:
            return self._installation_id

        app_jwt = self._make_app_jwt()
        resp = requests.get(
            f"{_GITHUB_API}/app/installations",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        installations = resp.json()

        for inst in installations:
            account = inst.get("account") or {}
            if account.get("login") == self._owner:
                self._installation_id = int(inst["id"])
                return self._installation_id

        raise RuntimeError(
            f"XAIR App has no installation on {self._owner!r}. "
            f"Found {len(installations)} installation(s): "
            f"{[i.get('account', {}).get('login') for i in installations]}"
        )

    def token(self, *, force: bool = False) -> str:
        """Return a valid installation token. Refreshes if near expiry."""
        now = time.time()
        if (
            not force
            and self._cached is not None
            and self._cached.expires_at - now > _REFRESH_MARGIN_SECONDS
        ):
            return self._cached.token

        installation_id = self._find_installation_id()
        app_jwt = self._make_app_jwt()
        resp = requests.post(
            f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        token = data["token"]
        # GitHub returns expires_at as ISO 8601. Parse minimally — we just
        # need a posix timestamp for the cache.
        from datetime import datetime

        expires_at_str = data["expires_at"].replace("Z", "+00:00")
        expires_at = datetime.fromisoformat(expires_at_str).timestamp()

        self._cached = _CachedToken(token=token, expires_at=expires_at)
        return token
