"""Redacted source/history checks; site-specific deny terms stay outside Git."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess


PATTERNS = {
    "personal-home-path": re.compile(r"(?:/Users/|/home/)[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[^\\\s]+\\"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "access-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b"),
    "credential-in-url": re.compile(r"[a-z]+://[^\s/:<>]+:[^\s/@<>]+@", re.I),
}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
PRIVATE_NETS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
CREDENTIAL = re.compile(
    r"(?im)^\s*(?:export\s+)?[\"']?(?:[A-Z0-9_]*_)?(?:PASSWORD|PASSWD|API_TOKEN|API_KEY|SECRET)"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_./+!@-]{4,})[\"']?\s*[,;]?\s*$"
)
PLACEHOLDERS = {"none", "null", "true", "false", "example", "placeholder"}


def text_findings(text, private_terms=()):
    findings = set()
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            findings.add((text.count("\n", 0, match.start()) + 1, name))
    for match in IPV4.finditer(text):
        try:
            address = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        # Only the three canonical rule ranges are exempt, not URLs or site subnets.
        suffix = re.match(r"/\d{1,2}(?!\d)", text[match.end():])
        rule_range = suffix and any(f"{address}{suffix.group()}" == str(net) for net in PRIVATE_NETS)
        if any(address in net for net in PRIVATE_NETS) and not rule_range:
            findings.add((text.count("\n", 0, match.start()) + 1, "private-network-host"))
    for match in CREDENTIAL.finditer(text):
        if match.group(1).lower() not in PLACEHOLDERS:
            findings.add((text.count("\n", 0, match.start()) + 1, "literal-credential"))
    for number, line in enumerate(text.splitlines(), 1):
        if any(term.casefold() in line.casefold() for term in private_terms):
            findings.add((number, "private-deny-term"))
    return sorted(findings)


def forbidden_path(path):
    name = Path(path).name.lower()
    parts = Path(path).parts
    if any(part.startswith("servo-diagnostic") for part in parts):
        return True
    if tuple(parts) == ("modules", "feature", "hmi", "servo", "diagnostic", "module.json"):
        return True
    return (name == ".env" or name.startswith(".env.")
            or name in {"id_rsa", "id_ed25519", "credentials", "mctivity_hmi_state.json"}
            or name.endswith((".pem", ".key", ".p12", ".pfx", ".log", ".pid", ".bak", ".pyc")))


def git(*args):
    return subprocess.check_output(["git", *args])


def at_repository_root():
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == Path.cwd().resolve()


def check_bytes(label, data, private_terms):
    if b"\0" in data:
        return []
    return [f"{label}:{line}: {rule}" for line, rule in text_findings(data.decode("utf-8", errors="replace"), private_terms)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", metavar="BASE", help="also scan every snapshot and commit message after BASE")
    args = parser.parse_args()
    terms = json.loads(os.environ.get("MCTIVITY_RELEASE_DENY_TERMS", "[]"))
    if not isinstance(terms, list) or any(not isinstance(term, str) or not term for term in terms):
        parser.error("MCTIVITY_RELEASE_DENY_TERMS must be a JSON array of nonempty strings")
    hits = []
    repository = at_repository_root()
    if args.history and not repository:
        parser.error("--history requires the Git repository root; archives support current-file checks only")
    if repository:
        paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    else:
        paths = [str(path) for path in Path(".").rglob("*")
                 if ".git" not in path.parts and (path.is_file() or path.is_symlink())]
    for name in sorted(set(filter(None, paths))):
        path = Path(name)
        if path.is_symlink():
            hits.append(f"{name}: prohibited-release-symlink")
            continue
        if not path.exists():
            continue
        if forbidden_path(name):
            hits.append(f"{name}: prohibited-release-file")
        hits.extend(check_bytes(name, path.read_bytes(), terms))
    if args.history:
        seen_blobs = set()
        for commit in git("rev-list", f"{args.history}..HEAD").decode().splitlines():
            hits.extend(check_bytes(commit[:12], git("show", "-s", "--format=fuller", commit), terms))
            for entry in git("ls-tree", "-rz", commit).split(b"\0"):
                if not entry:
                    continue
                metadata, name = entry.split(b"\t", 1)
                mode, kind, oid = metadata.decode().split()
                label = f"{commit[:12]}:{name.decode()}"
                if forbidden_path(name.decode()):
                    hits.append(f"{label}: prohibited-release-file")
                if kind == "blob" and oid not in seen_blobs:
                    seen_blobs.add(oid)
                    hits.extend(check_bytes(label, git("cat-file", "blob", oid), terms))
    if hits:
        print("\n".join(hits))
        print("Release content check failed; matched values are intentionally omitted.")
        return 1
    print("Release content check passed (text/filename heuristics, not a complete security audit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
