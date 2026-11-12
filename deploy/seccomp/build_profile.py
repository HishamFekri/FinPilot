"""Builds api-chromium.json from Docker's own default seccomp profile, reproducibly.

    python deploy/seccomp/build_profile.py          # writes api-chromium.json
    python deploy/seccomp/build_profile.py --check  # exits 1 if the file is out of date

The only change: `clone`, `unshare` and `chroot` are allowed without CAP_SYS_ADMIN or
CAP_SYS_CHROOT. Chromium's own sandbox needs them to put each renderer in new user, PID and network
namespaces and chroot it into an empty directory. Docker's default allows those only to
containers with CAP_SYS_ADMIN, which FinPilot never grants. Docker's two argument-filtered `clone`
rules are replaced by the plain one, so no syscall has two rules. Everything else, including the
default action (refuse) and `clone3` (reported as unimplemented, so libc falls back to `clone`),
is Docker's own. See deploy/seccomp/README.md.
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "docker-default-29.9.2.json"
SOURCE_SHA256 = "785b2429264afba4d594320337cb17f144f3c7d51585f9805eef72e28f4f9334"
TARGET = HERE / "api-chromium.json"
ADDED = ["clone", "unshare", "chroot"]


def build() -> str:
    raw = SOURCE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(f"{SOURCE.name} isn't Docker's file (sha256 {digest}). Stopped.")
    profile: dict[str, Any] = json.loads(raw)
    rules = [
        rule for rule in profile["syscalls"] if not (rule["names"] == ["clone"] and "args" in rule)
    ]
    if len(rules) != len(profile["syscalls"]) - 2:
        raise SystemExit("Docker's clone rules changed shape; review before rebuilding. Stopped.")
    rules.append(
        {
            "names": ADDED,
            "action": "SCMP_ACT_ALLOW",
            "comment": "FinPilot API: Chromium's namespace sandbox, without CAP_SYS_ADMIN",
        }
    )
    profile["syscalls"] = rules
    return json.dumps(profile, indent=2, ensure_ascii=True) + "\n"


def main() -> int:
    wanted = build()
    if "--check" in sys.argv[1:]:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != wanted:
            print(f"{TARGET.name} is out of date: run deploy/seccomp/build_profile.py")
            return 1
        return 0
    TARGET.write_bytes(wanted.encode("ascii"))
    print(f"wrote {TARGET.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
