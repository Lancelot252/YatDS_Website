#!/usr/bin/env python3
"""Pull the configured main branch and publish its site/ tree."""
from contextlib import contextmanager
import fcntl
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(os.environ.get("YATDS_ROOT", "/srv/yatds")).resolve()
REPO = ROOT / "repo.git"
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
PORT = int(os.environ.get("YATDS_PORT", "8788"))


def git(*args, timeout=30):
    result = subprocess.run(
        ["git", "--git-dir", str(REPO), *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return result.stdout.strip()


@contextmanager
def deploy_lock():
    with (ROOT / ".deploy.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def unpack_site(archive, destination):
    destination.mkdir()
    with tarfile.open(archive, "r:") as source:
        for entry in source:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unexpected archive path")
            target = destination.joinpath(*path.parts)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(entry) as data, target.open("xb") as output:
                    shutil.copyfileobj(data, output)
            else:
                raise ValueError("site/ must contain only regular files and directories")


def deploy():
    git("fetch", "--no-tags", "origin",
        "+refs/heads/main:refs/remotes/origin/main", timeout=150)
    commit = git("rev-parse", "--verify", "refs/remotes/origin/main^{commit}")
    release = RELEASES / commit
    target = Path("releases") / commit

    if CURRENT.is_symlink() and CURRENT.readlink() == target:
        return commit, "unchanged"

    with tempfile.TemporaryDirectory(prefix=".stage-", dir=RELEASES) as temporary:
        stage = Path(temporary)
        if not release.exists():
            archive = stage / "site.tar"
            webroot = stage / "site"
            git("archive", "--format=tar", f"--output={archive}", f"{commit}:site")
            unpack_site(archive, webroot)
            index = webroot / "index.html"
            if not index.is_file() or index.stat().st_size == 0:
                raise ValueError("Expected a non-empty site/index.html")
            webroot.rename(release)
        link = stage / "current"
        link.symlink_to(target)
        link.replace(CURRENT)
    return commit, "deployed"


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, payload):
        body = (json.dumps(payload) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if self.path != "/_deploy/yatds":
            self.reply(404, {"error": "not_found"})
            return
        authorization = self.headers.get("Authorization", "").encode("latin-1")
        if not hmac.compare_digest(authorization, self.server.expected_authorization):
            self.reply(401, {"error": "unauthorized"})
            return
        try:
            with deploy_lock():
                commit, status = deploy()
        except BlockingIOError:
            self.reply(409, {"error": "deployment_in_progress"})
            return
        except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
            logging.exception("Deployment failed")
            self.reply(500, {"error": "deployment_failed"})
            return
        logging.info("%s %s", status, commit)
        self.reply(200, {"status": status, "commit": commit})

    def do_GET(self):
        self.reply(405, {"error": "post_required"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = os.environ.pop("DEPLOY_TOKEN", "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise SystemExit("DEPLOY_TOKEN must be generated with openssl rand -hex 32")
    if not REPO.is_dir():
        raise SystemExit(f"Repository not found: {REPO}")
    RELEASES.mkdir(exist_ok=True)
    with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as server:
        server.expected_authorization = f"Bearer {token}".encode()
        server.serve_forever()


if __name__ == "__main__":
    main()
