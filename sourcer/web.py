"""Local web app: a small stdlib HTTP server that runs searches and browses their results.

It only listens on 127.0.0.1, checks the Host header (against DNS rebinding) and requires a custom header on
every write, so other websites open in the same browser can't start searches or change the token.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import jd
from .cli import load_roles, role_problems, save_role
from .github import ROOT, TOKEN_VARS, AuthError, GitHub, GitHubError, load_token

STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "results"
RUN_FILES = {"report.html": "text/html; charset=utf-8", "candidates.csv": "text/csv; charset=utf-8",
             "candidates.json": "application/json; charset=utf-8"}
TEXT_OPTIONS = {"location": "--location", "repos": "--repos", "languages": "--languages"}
INT_OPTIONS = {"limit": "--limit", "min_score": "--min-score", "max_enrich": "--max-enrich", "max_deep": "--max-deep",
               "min_followers": "--min-followers", "min_stars": "--min-stars", "seed_pages": "--seed-pages"}
FLAG_OPTIONS = {"emails": "--emails", "keep_unknown_location": "--keep-unknown-location",
                "include_inactive": "--include-inactive", "refresh": "--refresh"}
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_]{20,255}")
MAX_BODY = 256 * 1024  # job descriptions can be long

JOBS = {}
JOBS_LOCK = threading.Lock()


class BadRequest(ValueError):
    pass


class NotFound(LookupError):
    pass


def roles_or_error():
    try:
        return load_roles()
    except SystemExit as err:  # load_roles exits with a readable message when roles.json is broken
        raise BadRequest(str(err.code)) from None


# -- results on disk -------------------------------------------------------

def run_path(name):
    """The results folder for a run name taken from a URL, or None if it isn't one."""
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        return None
    path = RESULTS / name
    if path.resolve().parent != RESULTS.resolve() or not (path / "candidates.json").is_file():
        return None
    return path


def read_run(path):
    return json.loads((path / "candidates.json").read_text(encoding="utf-8"))


def list_runs():
    runs = []
    if not RESULTS.is_dir():
        return runs
    for path in RESULTS.iterdir():
        if not run_path(path.name):
            continue
        try:
            data = read_run(path)
        except (OSError, ValueError):
            continue
        meta, people = data.get("meta") or {}, data.get("candidates") or []
        runs.append({
            "name": path.name, "role": meta.get("role"), "role_label": meta.get("role_label") or path.name,
            "location": (meta.get("query") or {}).get("location") or "", "generated_at": meta.get("generated_at") or "",
            "count": len(people), "tier_a": sum(1 for c in people if c.get("tier") == "A"),
            "with_email": sum(1 for c in people if c.get("email")),
        })
    return sorted(runs, key=lambda r: r["generated_at"], reverse=True)


# -- searches ----------------------------------------------------------------

def custom_role_file(role):
    """Write a role built from a job description to a file the search subprocess can read."""
    if not isinstance(role, dict):
        raise BadRequest("Analyze a job description first.")
    problems = role_problems(role)
    if problems:
        raise BadRequest("The search profile has problems: " + "; ".join(problems))
    key = jd.slug(role.get("label")) if not isinstance(role.get("key"), str) else jd.slug(role["key"].removeprefix("jd-"))
    folder = RESULTS / ".roles"  # a dot name never counts as a run
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime)[:-50]:
        old.unlink(missing_ok=True)
    path = folder / f"{uuid.uuid4().hex[:12]}.json"
    path.write_text(json.dumps({**role, "key": key}, ensure_ascii=False), encoding="utf-8")
    return path


def search_argv(form, roles):
    """CLI arguments for a search form. `--flag=value` keeps a value like "-foo" from parsing as an option."""
    if form.get("custom_role") is not None:
        argv = ["search", f"--role-file={custom_role_file(form['custom_role'])}", f"--out={RESULTS}"]
    elif form.get("role") in roles:
        argv = ["search", form["role"], f"--out={RESULTS}"]
    else:
        raise BadRequest("Pick a role.")
    for field, flag in TEXT_OPTIONS.items():
        value = " ".join(str(form.get(field) or "").split())
        if value:
            argv.append(f"{flag}={value}")
    for field, flag in INT_OPTIONS.items():
        value = form.get(field)
        if value in (None, ""):
            continue
        try:
            argv.append(f"{flag}={int(str(value).strip())}")
        except ValueError:
            raise BadRequest(f"{field.replace('_', ' ').capitalize()} must be a whole number.") from None
    argv += [flag for field, flag in FLAG_OPTIONS.items() if form.get(field) is True]
    excludes = form.get("exclude_runs") or []
    for name in excludes if isinstance(excludes, list) else []:
        path = run_path(str(name))
        if path and (path / "candidates.csv").is_file():
            argv.append(f"--exclude={path / 'candidates.csv'}")
    return argv


class Job:
    """One `source.py search` subprocess, with its output kept for the browser to poll."""

    def __init__(self, argv):
        self.id = uuid.uuid4().hex[:12]
        self.lines, self.state, self.run, self.started = [], "running", None, time.time()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "source.py"), *argv], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.lines.append(line)
            match = re.match(r"Report:\s+(.+)[/\\]report\.html$", line.strip())
            if match:
                self.run = Path(match.group(1)).name
        self.proc.stdout.close()
        code = self.proc.wait()
        if self.state == "running":
            self.state = "done" if code == 0 else "failed"

    def cancel(self):
        if self.state == "running":
            self.state = "cancelled"
            self.proc.terminate()

    def view(self, since=0):
        return {"id": self.id, "state": self.state, "run": self.run, "started": self.started,
                "lines": self.lines[since:], "total": len(self.lines)}


def start_job(form):
    argv = search_argv(form, roles_or_error() if form.get("custom_role") is None else {})
    with JOBS_LOCK:
        if any(job.state == "running" for job in JOBS.values()):
            raise BadRequest("A search is already running. Wait for it or cancel it first.")
        job = Job(argv)
        JOBS[job.id] = job
        for old in sorted(JOBS.values(), key=lambda j: j.started)[:-20]:
            JOBS.pop(old.id, None)
    return job


def current_job():
    with JOBS_LOCK:
        running = [job for job in JOBS.values() if job.state == "running"]
    return running[0] if running else None


def analyze_jd(text):
    """The search profile for a pasted JD, with seed repos checked and extended on GitHub when a token is set."""
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("Paste a job description first.")
    try:
        result = jd.analyze(text)
    except ValueError as err:
        raise BadRequest(str(err)) from None
    token = load_token()
    if token:
        try:
            jd.refine_with_github(GitHub(token), result)
            result["github"] = True
        except AuthError:
            result["github"] = False
            result["analysis"]["notes"].append("GitHub rejected the token, so seed repos weren't checked.")
    else:
        result["github"] = False
        result["analysis"]["notes"].append("Connect GitHub to check the seed repos and find more matching projects.")
    return result


def save_preset(body):
    try:
        key = save_role(body.get("key"), body.get("role") if isinstance(body.get("role"), dict) else {})
    except ValueError as err:
        raise BadRequest(str(err)) from None
    except SystemExit as err:  # roles.json itself is broken
        raise BadRequest(str(err.code)) from None
    return {"key": key}


# -- GitHub token ----------------------------------------------------------

def github_status():
    token = load_token()
    if not token:
        return {"ok": False, "reason": "missing"}
    try:
        gh = GitHub(token)
        me = gh.viewer() or {}
        limits = (gh.rate_limit() or {}).get("resources") or {}
    except AuthError:
        return {"ok": False, "reason": "invalid"}
    except GitHubError as err:
        return {"ok": False, "reason": "offline", "detail": str(err)[:200]}
    return {"ok": True, "login": me.get("login"),
            "limits": {name: {key: (limits.get(name) or {}).get(key) for key in ("remaining", "limit", "reset")}
                       for name in ("core", "search", "graphql")}}


def save_token(token):
    token = (token or "").strip()
    if not TOKEN_SHAPE.fullmatch(token):
        raise BadRequest("That doesn't look like a GitHub token. It should start with github_pat_ or ghp_.")
    try:
        GitHub(token).viewer()
    except AuthError:
        raise BadRequest("GitHub rejected that token. Check you copied all of it.") from None
    except GitHubError as err:
        raise BadRequest(f"Couldn't reach GitHub to check the token: {err}") from None
    env_file = ROOT / ".env"
    lines = env_file.read_text(encoding="utf-8-sig").splitlines() if env_file.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip().removeprefix("export ").strip() not in TOKEN_VARS]
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join([*lines, f"GITHUB_TOKEN={token}"]) + "\n")
    # Environment variables win over .env, so update ours too; searches inherit it.
    os.environ.pop("GH_TOKEN", None)
    os.environ["GITHUB_TOKEN"] = token


# -- HTTP --------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "GitHubSourcer"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, default=str).encode(), "application/json; charset=utf-8")

    def _trusted_host(self):
        return (self.headers.get("Host") or "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

    def _parts(self):
        url = urlparse(self.path)
        return [unquote(p) for p in url.path.split("/") if p], parse_qs(url.query)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise BadRequest("Request too large.")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise BadRequest("Invalid JSON.") from None
        if not isinstance(data, dict):
            raise BadRequest("Expected a JSON object.")
        return data

    def _handle(self, route):
        if not self._trusted_host():
            return self._json({"error": "Open the app at http://127.0.0.1"}, 403)
        try:
            route()
        except BadRequest as err:
            self._json({"error": str(err)}, 400)
        except NotFound as err:
            self._json({"error": str(err) or "Not found."}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as err:  # keep the app answering in JSON instead of dropping the connection
            self._json({"error": f"{type(err).__name__}: {err}"}, 500)

    def do_GET(self):
        self._handle(self._get)

    def do_POST(self):
        self._handle(self._post)

    def _get(self):
        parts, query = self._parts()
        if not parts:
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if parts == ["api", "roles"]:
            return self._json([{"key": key, "label": role["label"], "seed_repos": role["seed_repos"],
                                "search_languages": role["search_languages"],
                                "topic_search": role.get("topic_search", [])}
                               for key, role in roles_or_error().items()])
        if parts == ["api", "status"]:
            return self._json(github_status())
        if parts == ["api", "runs"]:
            return self._json(list_runs())
        if len(parts) == 3 and parts[:2] == ["api", "runs"]:
            path = run_path(parts[2])
            if not path:
                raise NotFound("That run doesn't exist any more.")
            return self._json(read_run(path))
        if parts == ["api", "jobs", "current"]:
            job = current_job()
            return self._json({"job": job.view() if job else None})
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            job = JOBS.get(parts[2])
            if not job:
                raise NotFound("That search isn't running any more.")
            since = int((query.get("since") or ["0"])[0] or 0)
            return self._json(job.view(max(0, since)))
        if len(parts) == 3 and parts[0] == "runs" and parts[2] in RUN_FILES:
            path = run_path(parts[1])
            if not path or not (path / parts[2]).is_file():
                raise NotFound()
            headers = {"Content-Disposition": f'attachment; filename="{path.name}.csv"'} if parts[2].endswith(".csv") else {}
            return self._send(200, (path / parts[2]).read_bytes(), RUN_FILES[parts[2]], headers)
        raise NotFound()

    def _post(self):
        if self.headers.get("X-Sourcer") != "1":
            return self._json({"error": "Missing X-Sourcer header."}, 403)
        parts, _ = self._parts()
        if parts == ["api", "search"]:
            return self._json(start_job(self._body()).view(), 201)
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            job = JOBS.get(parts[2])
            if not job:
                raise NotFound("That search isn't running any more.")
            job.cancel()
            return self._json(job.view())
        if parts == ["api", "token"]:
            save_token(self._body().get("token"))
            return self._json(github_status())
        if parts == ["api", "jd"]:
            return self._json(analyze_jd(self._body().get("text")))
        if parts == ["api", "roles"]:
            return self._json(save_preset(self._body()), 201)
        raise NotFound()


def serve(port=8765, open_browser=True):
    server = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit(f"Ports {port}-{port + 19} are all in use; pick another with --port.")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"GitHub Sourcer is running at {url}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for job in list(JOBS.values()):
            job.cancel()
        server.server_close()
