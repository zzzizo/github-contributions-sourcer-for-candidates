"""Small GitHub REST + GraphQL client with on-disk caching and rate-limit handling."""

import hashlib
import http.client
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent
TOKEN_VARS = ("GITHUB_TOKEN", "GH_TOKEN")


class GitHubError(RuntimeError):
    pass


class AuthError(Exception):
    """Bad or expired token. Deliberately not a GitHubError, so per-call error handling doesn't swallow it."""


def ssl_context():
    """Default trust store, falling back to certifi or the OS bundle.

    python.org installs on macOS ship with an empty store until "Install Certificates.command" is run.
    """
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"]:
        return context
    try:
        import certifi
        context.load_verify_locations(certifi.where())
        return context
    except ImportError:
        pass
    for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if os.path.exists(bundle):
            context.load_verify_locations(bundle)
            break
    return context


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_token():
    """Token from the environment, then ./.env, then the gh CLI."""
    for var in TOKEN_VARS:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            lines = env_file.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as err:
            log(f"  ! could not read {env_file}: {err}")
            lines = []
        for line in lines:
            key, sep, value = line.partition("=")
            if sep and key.strip().removeprefix("export ").strip() in TOKEN_VARS:
                value = value.strip().strip("'\"")
                if value:
                    return value
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _seconds_until(value, default=60):
    """Retry-After may be seconds or an HTTP date; X-RateLimit-Reset is a Unix time."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        pass
    try:
        return max(0, int(parsedate_to_datetime(value).timestamp() - time.time()))
    except (TypeError, ValueError):
        return default


class GitHub:
    def __init__(self, token, cache_dir=ROOT / ".cache", ttl_hours=72, use_cache=True):
        self.token = token
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl_hours * 3600
        self.use_cache = use_cache
        self.ssl = ssl_context()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._prune()

    # -- cache -------------------------------------------------------------

    def _cache_path(self, key):
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _prune(self):
        """Drop expired entries and leftover temp files so the cache doesn't grow forever."""
        cutoff = time.time() - self.ttl
        for path in [*self.cache_dir.glob("*.json"), *self.cache_dir.glob("*.tmp")]:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def _cached(self, key):
        if not self.use_cache:
            return None
        path = self._cache_path(key)
        try:
            if time.time() - path.stat().st_mtime < self.ttl:
                return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        return None

    def _store(self, key, data):
        path = self._cache_path(key)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError as err:
            # A full disk or read-only folder only costs us the cache, not the run.
            log(f"  ! could not write cache: {err}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # -- transport ---------------------------------------------------------

    def _request(self, method, url, body=None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-sourcer",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"

        for attempt in range(6):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60, context=self.ssl) as resp:
                    return json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as err:
                text = err.read().decode(errors="replace")
                if err.code == 401:
                    raise AuthError(f"GitHub rejected the token (HTTP 401): {text[:200]}") from None
                # 404 = gone/renamed, 409 = empty repository, 451 = blocked: treat as "no data".
                if err.code in (404, 409, 451):
                    return None
                wait = self._backoff(err, text, attempt)
                if wait is None:
                    raise GitHubError(f"{method} {url} -> HTTP {err.code}: {text[:300]}") from None
                log(f"  GitHub returned {err.code}; waiting {wait}s before retrying…")
                time.sleep(wait)
            # Network drops, truncated bodies and non-JSON pages from proxies are all worth a retry.
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, ValueError) as err:
                if attempt == 5:
                    raise GitHubError(f"{method} {url} failed: {err}") from None
                time.sleep(2 ** attempt)
        raise GitHubError(f"{method} {url} failed after retries")

    @staticmethod
    def _backoff(err, text, attempt):
        headers = err.headers
        if err.code in (403, 429):
            if headers.get("Retry-After"):
                return _seconds_until(headers["Retry-After"]) + 1
            if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
                reset = _seconds_until(headers["X-RateLimit-Reset"], default=None)
                return 60 if reset is None else max(1, reset - int(time.time())) + 2
            if "rate limit" in text.lower() and attempt < 3:
                return 60
            return None
        if err.code >= 500 and attempt < 4:
            return 2 ** (attempt + 1)
        return None

    def _graphql_reset_wait(self):
        try:
            reset = self.rate_limit()["resources"]["graphql"]["reset"]
            return max(1, int(reset) - int(time.time())) + 2
        except (GitHubError, KeyError, TypeError, ValueError):
            return 60

    # -- public API --------------------------------------------------------

    def rest(self, path, params=None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        key = "GET " + url
        cached = self._cached(key)
        if cached is not None:
            return cached
        data = self._request("GET", url)
        if data is not None:
            self._store(key, data)
        return data

    def graphql(self, query, variables=None):
        body = {"query": query, "variables": variables or {}}
        key = "POST /graphql " + json.dumps(body, sort_keys=True)
        cached = self._cached(key)
        if cached is not None:
            return cached
        for attempt in range(3):
            result = self._request("POST", API + "/graphql", body) or {}
            errors = [e for e in result.get("errors") or [] if e.get("type") != "NOT_FOUND"]
            # GraphQL reports its rate limit as HTTP 200 with a RATE_LIMITED error.
            if attempt < 2 and any(e.get("type") == "RATE_LIMITED" for e in errors):
                wait = self._graphql_reset_wait()
                log(f"  GraphQL rate limit hit; waiting {wait}s before retrying…")
                time.sleep(wait)
                continue
            break
        data = result.get("data")
        if not isinstance(data, dict) or (errors and not any(data.values())):
            raise GitHubError(f"GraphQL error: {json.dumps(errors or result)[:400]}")
        # Partial results are still useful now, but caching them would hide the missing parts for 72h.
        if not errors:
            self._store(key, data)
        return data

    def rate_limit(self):
        return self._request("GET", API + "/rate_limit")

    def viewer(self):
        return self._request("GET", API + "/user")
