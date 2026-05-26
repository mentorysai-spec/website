#!/usr/bin/env python3
"""PreToolUse hook: blocks npm/yarn/pnpm/uv package installs released < 7 days ago."""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen
from urllib.error import URLError

MIN_AGE_DAYS = 7
CONTACT = "Avi Bensimon — Mentorys.ai  (security review & approval)"
CONTACT_HE = "אבי בנסימון — Mentorys.ai  (סקירת אבטחה ואישור)"

# Patterns that signal an npm/yarn/pnpm package install
NPM_PATTERN = re.compile(
    r'\b(npm|yarn|pnpm)\b.+?\b(install|add|i|ci)\b', re.IGNORECASE
)
# Patterns for uv (Python package manager)
UV_PATTERN = re.compile(
    r'\buv\b.+?\b(add|install|pip\s+install)\b', re.IGNORECASE
)

# Extract "name@version" tokens from a command string
PKG_TOKEN = re.compile(
    r'(?:^|\s)(@?[a-zA-Z0-9](?:[a-zA-Z0-9._-]*)(?:/[a-zA-Z0-9._-]+)?)@([^\s]+)'
)
# Scoped packages: @scope/name@version
SCOPED_PKG_TOKEN = re.compile(
    r'(@[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+)@([^\s]+)'
)


def fetch_json(url):
    try:
        with urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (URLError, Exception):
        return None


def get_npm_release_dates(pkg_name):
    """Returns {version: datetime} for all published versions, or None on error."""
    url = f"https://registry.npmjs.org/{pkg_name}"
    data = fetch_json(url)
    if not data or "time" not in data:
        return None
    dates = {}
    for ver, ts in data["time"].items():
        if ver in ("created", "modified"):
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            dates[ver] = dt
        except ValueError:
            pass
    return dates


def get_pypi_release_dates(pkg_name):
    """Returns {version: datetime} for all published versions, or None on error."""
    url = f"https://pypi.org/pypi/{pkg_name}/json"
    data = fetch_json(url)
    if not data or "releases" not in data:
        return None
    dates = {}
    for ver, files in data["releases"].items():
        if not files:
            continue
        try:
            earliest = min(
                datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00"))
                for f in files if f.get("upload_time_iso_8601")
            )
            dates[ver] = earliest
        except (ValueError, KeyError):
            pass
    return dates


def days_ago(dt, today):
    delta = today - dt
    return delta.days


def find_safe_version(release_dates, today):
    """Return the newest version that is at least MIN_AGE_DAYS old, or None."""
    cutoff = today - timedelta(days=MIN_AGE_DAYS)
    candidates = [(v, d) for v, d in release_dates.items() if d <= cutoff]
    if not candidates:
        return None
    # Sort by date descending, pick newest
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def days_word_he(n):
    if n == 1:
        return "יום אחד"
    return f"{n} ימים"


def build_block_message(pkg, version, release_dt, safe, today, ecosystem="npm"):
    age = days_ago(release_dt, today)
    age_str = f"{age} day{'s' if age != 1 else ''} ago"
    age_str_he = f"לפני {days_word_he(age)}"

    release_date_str = release_dt.strftime("%Y-%m-%d")
    approve_dt = release_dt + timedelta(days=MIN_AGE_DAYS)
    approve_str = approve_dt.strftime("%Y-%m-%d")
    days_left = max(1, (approve_dt.date() - today.date()).days)

    if ecosystem == "npm":
        install_cmd = "npm install -g" if version else "npm install"
    else:
        install_cmd = "uv add"

    sep = "━" * 42

    lines_en = [
        sep,
        "🚫 BLOCKED — Package Age Policy Violation",
        sep,
        "",
        f"Package : {pkg}@{version}",
        f"Released: {release_date_str} ({age_str} — minimum is {MIN_AGE_DAYS} days)",
        "",
    ]
    lines_he = [
        sep,
        "🚫 חסום — הפרת מדיניות גיל חבילה",
        sep,
        "",
        f"חבילה  : {pkg}@{version}",
        f"שוחררה : {release_date_str} ({age_str_he} — המינימום הוא {MIN_AGE_DAYS} ימים)",
        "",
    ]

    if safe:
        safe_ver, safe_dt = safe
        safe_age = days_ago(safe_dt, today)
        safe_date_str = safe_dt.strftime("%Y-%m-%d")
        safe_age_str = f"{safe_age} day{'s' if safe_age != 1 else ''} ago"
        safe_age_str_he = f"לפני {days_word_he(safe_age)}"
        lines_en += [
            f"✅ Suggested safe version: {pkg}@{safe_ver} (released {safe_date_str}, {safe_age_str})",
            f"   Run instead: {install_cmd} {pkg}@{safe_ver}",
            "",
        ]
        lines_he += [
            f"✅ גרסה בטוחה מוצעת: {pkg}@{safe_ver} (שוחררה {safe_date_str}, {safe_age_str_he})",
            f"   הרץ במקום: {install_cmd} {pkg}@{safe_ver}",
            "",
        ]
    else:
        lines_en += [
            f"⚠️  No version of this package meets the {MIN_AGE_DAYS}-day policy.",
            f"   The earliest it can be installed: {approve_str} (in {days_left} day{'s' if days_left != 1 else ''})",
            "",
        ]
        lines_he += [
            f"⚠️  אין גרסה של חבילה זו העומדת במדיניות {MIN_AGE_DAYS} הימים.",
            f"   המועד המוקדם ביותר להתקנה: {approve_str} (בעוד {days_word_he(days_left)})",
            "",
        ]

    lines_en += [
        "Unless a security vulnerability is announced about this release,",
        f"it will be automatically approved on: {approve_str} (in {days_left} day{'s' if days_left != 1 else ''})",
        "",
        "For urgent installation, contact:",
        f"  {CONTACT}",
    ]
    lines_he += [
        "אלא אם כן תתגלה פגיעות אבטחה בגרסה זו,",
        f"היא תאושר אוטומטית בתאריך: {approve_str} (בעוד {days_word_he(days_left)})",
        "",
        "לצורך התקנה דחופה, צור קשר עם:",
        f"  {CONTACT_HE}",
    ]

    message = "\n".join(lines_en) + "\n\n" + "\n".join(lines_he)
    return message


def parse_npm_packages(cmd):
    """Extract list of (name, version_or_None) from an npm/yarn/pnpm command."""
    # First try scoped packages
    found = []
    seen = set()
    for m in SCOPED_PKG_TOKEN.finditer(cmd):
        name, ver = m.group(1), m.group(2)
        if name not in seen:
            found.append((name, ver))
            seen.add(name)
    # Non-scoped with explicit version
    for m in PKG_TOKEN.finditer(cmd):
        name, ver = m.group(1), m.group(2)
        if name.startswith("@"):
            continue  # already caught by scoped
        if name not in seen and not name.startswith("-"):
            found.append((name, ver))
            seen.add(name)
    return found


def parse_uv_packages(cmd):
    """Extract list of (name, version_or_None) from a uv add/install command."""
    # Strip the leading "uv add/install/pip install" part
    tokens = cmd.split()
    pkgs = []
    skip_next = False
    recording = False
    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if tok in ("uv",):
            continue
        if tok in ("add", "install", "pip"):
            recording = True
            continue
        if not recording:
            continue
        if tok.startswith("-"):
            # flags that take a value
            if tok in ("--index", "--index-url", "-i", "--extra-index-url",
                       "--find-links", "-f", "--constraint", "-c",
                       "--requirement", "-r", "--python", "-p"):
                skip_next = True
            continue
        # pkg==version or pkg>=version etc.
        m = re.match(r'^([A-Za-z0-9]([A-Za-z0-9._-]*))([=<>!~].+)?$', tok)
        if m:
            name = m.group(1)
            spec = m.group(3) or ""
            # Extract exact version from ==x.y.z
            ver_match = re.match(r'^==([^\s,]+)', spec)
            ver = ver_match.group(1) if ver_match else None
            pkgs.append((name, ver))
    return pkgs


def check_package(pkg_name, version, get_dates_fn, ecosystem, today):
    """
    Returns (should_block, message_or_None).
    """
    release_dates = get_dates_fn(pkg_name)
    if release_dates is None:
        return False, None  # fail open

    # Resolve version if not specified
    if not version:
        # Use the most recently published version
        if not release_dates:
            return False, None
        version = max(release_dates, key=lambda v: release_dates[v])

    if version not in release_dates:
        return False, None  # unknown version, fail open

    release_dt = release_dates[version]
    age = days_ago(release_dt, today)

    if age >= MIN_AGE_DAYS:
        return False, None  # old enough, allow

    safe = find_safe_version(release_dates, today)
    msg = build_block_message(pkg_name, version, release_dt, safe, today, ecosystem)
    return True, msg


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, Exception):
        print("{}")
        return

    tool_name = payload.get("tool_name", "")
    if tool_name != "Bash":
        print("{}")
        return

    cmd = payload.get("tool_input", {}).get("command", "")
    if not cmd:
        print("{}")
        return

    today = datetime.now(tz=timezone.utc)
    block_messages = []

    if NPM_PATTERN.search(cmd):
        packages = parse_npm_packages(cmd)
        for pkg_name, version in packages:
            blocked, msg = check_package(
                pkg_name, version, get_npm_release_dates, "npm", today
            )
            if blocked:
                block_messages.append(msg)

    elif UV_PATTERN.search(cmd):
        packages = parse_uv_packages(cmd)
        for pkg_name, version in packages:
            blocked, msg = check_package(
                pkg_name, version, get_pypi_release_dates, "uv", today
            )
            if blocked:
                block_messages.append(msg)

    if block_messages:
        combined = "\n\n" + ("\n\n" + "=" * 42 + "\n\n").join(block_messages)
        result = {"continue": False, "stopReason": combined}
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("{}")


if __name__ == "__main__":
    main()
