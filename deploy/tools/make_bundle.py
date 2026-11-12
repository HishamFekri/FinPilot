"""Packs the minimum FinPilot source for the server's isolated stack test into one tarball.

    python deploy/tools/make_bundle.py OUTPUT.tar.gz

Deny by default: only the files the three images and the stack test need. It refuses (and writes
nothing) if a selected file looks secret (.env, keys, certificates, a private-key block, a
connection string with a password) or if a shell script, compose file or Caddyfile has Windows
line endings, which would break it on the server. Prints a manifest summary and the SHA-256.
Never reads backend/.env or any other excluded file.
"""

import hashlib
import io
import re
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# (folder, recursive?) roots and single files that make up the bundle.
FILES = [
    "package.json",
    "package-lock.json",
    ".dockerignore",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/alembic.ini",
    "backend/Dockerfile",
    "backend/.dockerignore",
]
TREES = [
    "apps/web",
    "backend/app",
    "backend/migrations",
    "backend/scripts",
    "backend/tests_stack",
    "deploy",
]
SKIP_DIRS = {
    "node_modules", ".next", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "test-results", "playwright-report", ".turbo", "state", "backups",
}  # fmt: skip
SKIP_FILES = {
    "deploy/finpilot.env", "deploy/release.env", "deploy/backup-recipients.txt",
    "apps/web/next-env.d.ts",
}  # fmt: skip
SECRET_NAMES = re.compile(r"(^\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|^id_(rsa|ed25519|ecdsa))")
SECRET_TEXT = [
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"postgres(?:ql)?(?:\+\w+)?://[^\s:/@\"']+:[^\s@\"'{}$]{8,}@"),
]
LF_ONLY = re.compile(r"(\.sh|\.ya?ml|Caddyfile(\.\w+)?|\.caddy|Dockerfile)$")


def selected() -> list[Path]:
    chosen = [REPO / f for f in FILES]
    for tree in TREES:
        for path in sorted((REPO / tree).rglob("*")):
            relative = path.relative_to(REPO)
            if any(part in SKIP_DIRS for part in relative.parts[:-1]) or not path.is_file():
                continue
            if relative.as_posix() in SKIP_FILES:
                continue
            if relative.parts[:2] == ("deploy", "secrets") and path.name != "README.md":
                continue
            if path.name.endswith(".tsbuildinfo") or path.suffix in {".pyc", ".age", ".log"}:
                continue
            chosen.append(path)
    return chosen


def problems(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        relative = path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else str(path)
        if SECRET_NAMES.search(path.name) and not path.name.endswith(".example"):
            found.append(f"secret-looking file name: {relative}")
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_TEXT):
            found.append(f"secret-looking content: {relative}")
        if LF_ONLY.search(path.name) and b"\r" in data:
            found.append(f"Windows line endings (would break on the server): {relative}")
    return found


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].endswith(".tar.gz"):
        print("usage: python deploy/tools/make_bundle.py OUTPUT.tar.gz")
        return 2
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        print(f"{output} already exists. Stopped.")
        return 1
    paths = selected()
    missing = [p for p in paths if not p.exists()]
    issues = [f"missing: {p.relative_to(REPO).as_posix()}" for p in missing] + problems(
        [p for p in paths if p.exists()]
    )
    if issues:
        print("Refused; nothing written:")
        print("\n".join(f"  {issue}" for issue in issues))
        return 1
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for path in paths:
            relative = path.relative_to(REPO).as_posix()
            info = tarfile.TarInfo(relative)
            data = path.read_bytes()
            info.size = len(data)
            info.mode = 0o755 if path.suffix in {".sh"} else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    output.write_bytes(buffer.getvalue())
    digest = hashlib.sha256(buffer.getvalue()).hexdigest()
    by_root: dict[str, int] = {}
    for path in paths:
        root = "/".join(path.relative_to(REPO).parts[:2])
        by_root[root] = by_root.get(root, 0) + 1
    print(f"wrote {output} ({len(paths)} files, {len(buffer.getvalue()) // 1024} KiB)")
    for root, count in sorted(by_root.items()):
        print(f"  {count:5d}  {root}")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
