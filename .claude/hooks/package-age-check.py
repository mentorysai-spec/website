#!/usr/bin/env python3
"""PreToolUse hook: enforces package/extension policy for npm/yarn/pnpm, uv, and VS Code."""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

MIN_AGE_DAYS = 7
CONTACT = "Avi Bensimon — Mentorys.ai  (security review & approval)"
CONTACT_HE = "אבי בנסימון — Mentorys.ai  (סקירת אבטחה ואישור)"
SEP = "━" * 42

NPM_PATTERN = re.compile(
    r'\b(npm|yarn|pnpm)\b.+?\b(install|add|i|ci)\b', re.IGNORECASE
)
UV_PATTERN = re.compile(
    r'\buv\b.+?\b(add|install|pip\s+install)\b', re.IGNORECASE
)
VSCODE_PATTERN = re.compile(
    r'\b(code|code-insiders)\b.*--install-extension\b', re.IGNORECASE
)

PKG_TOKEN = re.compile(
    r'(?:^|\s)(@?[a-zA-Z0-9](?:[a-zA-Z0-9._-]*)(?:/[a-zA-Z0-9._-]+)?)@([^\s]+)'
)
SCOPED_PKG_TOKEN = re.compile(
    r'(@[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+)@([^\s]+)'
)


# ── HTTP helpers ────────────────────────────────────────────────────────────

def fetch_json(url, *, method="GET", body=None, headers=None):
    try:
        req = Request(url, data=body, headers=headers or {}, method=method)
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (URLError, Exception):
        return None


# ── npm / PyPI registry ─────────────────────────────────────────────────────

def get_npm_release_dates(pkg_name):
    data = fetch_json(f"https://registry.npmjs.org/{pkg_name}")
    if not data or "time" not in data:
        return None
    dates = {}
    for ver, ts in data["time"].items():
        if ver in ("created", "modified"):
            continue
        try:
            dates[ver] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return dates


def get_pypi_release_dates(pkg_name):
    data = fetch_json(f"https://pypi.org/pypi/{pkg_name}/json")
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


# ── VS Code Marketplace ─────────────────────────────────────────────────────

def query_vscode_marketplace(ext_id):
    url = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
    body = json.dumps({
        "filters": [{
            "criteria": [{"filterType": 7, "value": ext_id}],
            "pageSize": 1,
            "pageNumber": 1
        }],
        "flags": 17  # IncludeVersions | IncludeVersionProperties
    }).encode()
    return fetch_json(
        url,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json;api-version=7.2-preview.1",
            "User-Agent": "claude-code-hook/1.0",
        },
    )


# ── Shared date / version helpers ────────────────────────────────────────────

def days_ago(dt, today):
    return (today - dt).days


def days_word_he(n):
    return "יום אחד" if n == 1 else f"{n} ימים"


def find_safe_version(release_dates, today):
    cutoff = today - timedelta(days=MIN_AGE_DAYS)
    candidates = [(v, d) for v, d in release_dates.items() if d <= cutoff]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


# ── Message builders ─────────────────────────────────────────────────────────

def build_age_block_message(pkg, version, release_dt, safe, today, ecosystem="npm"):
    age = days_ago(release_dt, today)
    age_str = f"{age} day{'s' if age != 1 else ''} ago"
    age_str_he = f"לפני {days_word_he(age)}"
    release_date_str = release_dt.strftime("%Y-%m-%d")
    approve_dt = release_dt + timedelta(days=MIN_AGE_DAYS)
    approve_str = approve_dt.strftime("%Y-%m-%d")
    days_left = max(1, (approve_dt.date() - today.date()).days)
    days_left_he = days_word_he(days_left)

    if ecosystem == "npm":
        install_cmd = "npm install -g"
    elif ecosystem == "uv":
        install_cmd = "uv add"
    else:
        install_cmd = "code --install-extension"

    en = [
        SEP,
        "🚫 BLOCKED — Package Age Policy Violation",
        SEP, "",
        f"Package : {pkg}@{version}",
        f"Released: {release_date_str} ({age_str} — minimum is {MIN_AGE_DAYS} days)",
        "",
    ]
    he = [
        SEP,
        "🚫 חסום — הפרת מדיניות גיל חבילה",
        SEP, "",
        f"חבילה  : {pkg}@{version}",
        f"שוחררה : {release_date_str} ({age_str_he} — המינימום הוא {MIN_AGE_DAYS} ימים)",
        "",
    ]

    if safe:
        sv, sd = safe
        s_age = days_ago(sd, today)
        s_date = sd.strftime("%Y-%m-%d")
        en += [
            f"✅ Suggested safe version: {pkg}@{sv} (released {s_date}, {s_age} day{'s' if s_age != 1 else ''} ago)",
            f"   Run instead: {install_cmd} {pkg}@{sv}",
            "",
        ]
        he += [
            f"✅ גרסה בטוחה מוצעת: {pkg}@{sv} (שוחררה {s_date}, לפני {days_word_he(s_age)})",
            f"   הרץ במקום: {install_cmd} {pkg}@{sv}",
            "",
        ]
    else:
        en += [
            f"⚠️  No version of this package meets the {MIN_AGE_DAYS}-day policy.",
            f"   The earliest it can be installed: {approve_str} (in {days_left} day{'s' if days_left != 1 else ''})",
            "",
        ]
        he += [
            f"⚠️  אין גרסה של חבילה זו העומדת במדיניות {MIN_AGE_DAYS} הימים.",
            f"   המועד המוקדם ביותר להתקנה: {approve_str} (בעוד {days_left_he})",
            "",
        ]

    en += [
        f"It will be automatically approved on: {approve_str} (in {days_left} day{'s' if days_left != 1 else ''})",
        "unless a security vulnerability is announced about this release.", "",
        "For urgent installation, contact:",
        f"  {CONTACT}",
    ]
    he += [
        f"היא תאושר אוטומטית בתאריך: {approve_str} (בעוד {days_left_he})",
        "אלא אם כן תתגלה פגיעות אבטחה בגרסה זו.", "",
        "לצורך התקנה דחופה, צור קשר עם:",
        f"  {CONTACT_HE}",
    ]

    return "\n".join(en) + "\n\n" + "\n".join(he)


def build_vscode_removed_message(ext_id, ver_label):
    en = [
        SEP,
        "🚫 BLOCKED — Extension Removed from VS Code Marketplace",
        SEP, "",
        f"Extension : {ext_id}{ver_label}", "",
        "This extension is no longer available in the VS Code Marketplace.",
        "It may have been removed due to a policy violation or security concern.", "",
        "For urgent installation, contact:",
        f"  {CONTACT}",
    ]
    he = [
        SEP,
        "🚫 חסום — התוסף הוסר מ-VS Code Marketplace",
        SEP, "",
        f"תוסף      : {ext_id}{ver_label}", "",
        "תוסף זה אינו זמין עוד ב-VS Code Marketplace.",
        "ייתכן שהוסר עקב הפרת מדיניות או חשש אבטחה.", "",
        "לצורך התקנה דחופה, צור קשר עם:",
        f"  {CONTACT_HE}",
    ]
    return "\n".join(en) + "\n\n" + "\n".join(he)


def build_vscode_unverified_message(ext_id, ver_label, pub):
    pub_name = pub.get("publisherName", "unknown")
    pub_display = pub.get("displayName", pub_name)
    en = [
        SEP,
        "🚫 BLOCKED — Unverified Publisher (Microsoft Signature Warning)",
        SEP, "",
        f"Extension : {ext_id}{ver_label}",
        f"Publisher : {pub_display} ({pub_name})", "",
        "This publisher has not been domain-verified by Microsoft.",
        "VS Code will display a signature warning when installing this extension.", "",
        "Only install extensions from verified (domain-verified) publishers.", "",
        "For urgent installation, contact:",
        f"  {CONTACT}",
    ]
    he = [
        SEP,
        "🚫 חסום — מפרסם לא מאומת (אזהרת חתימה של מיקרוסופט)",
        SEP, "",
        f"תוסף      : {ext_id}{ver_label}",
        f"מפרסם     : {pub_display} ({pub_name})", "",
        "מפרסם זה לא עבר אימות דומיין על ידי מיקרוסופט.",
        "VS Code יציג אזהרת חתימה בעת התקנת תוסף זה.", "",
        "התקן תוספים ממפרסמים מאומתים בלבד.", "",
        "לצורך התקנה דחופה, צור קשר עם:",
        f"  {CONTACT_HE}",
    ]
    return "\n".join(en) + "\n\n" + "\n".join(he)


# ── Parsers ──────────────────────────────────────────────────────────────────

def parse_npm_packages(cmd):
    found, seen = [], set()
    for m in SCOPED_PKG_TOKEN.finditer(cmd):
        name, ver = m.group(1), m.group(2)
        if name not in seen:
            found.append((name, ver))
            seen.add(name)
    for m in PKG_TOKEN.finditer(cmd):
        name, ver = m.group(1), m.group(2)
        if name.startswith("@") or name in seen or name.startswith("-"):
            continue
        found.append((name, ver))
        seen.add(name)
    return found


def parse_uv_packages(cmd):
    tokens = cmd.split()
    pkgs, recording, skip_next = [], False, False
    skip_flags = {
        "--index", "--index-url", "-i", "--extra-index-url",
        "--find-links", "-f", "--constraint", "-c",
        "--requirement", "-r", "--python", "-p",
    }
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in ("uv", "pip"):
            continue
        if tok in ("add", "install"):
            recording = True
            continue
        if not recording:
            continue
        if tok.startswith("-"):
            if tok in skip_flags:
                skip_next = True
            continue
        m = re.match(r'^([A-Za-z0-9]([A-Za-z0-9._-]*))([=<>!~].+)?$', tok)
        if m:
            name, spec = m.group(1), m.group(3) or ""
            ver_m = re.match(r'^==([^\s,]+)', spec)
            pkgs.append((name, ver_m.group(1) if ver_m else None))
    return pkgs


def parse_vscode_extension(cmd):
    """Return (publisher, name, version_or_None) or None."""
    m = re.search(r'--install-extension\s+(\S+)', cmd)
    if not m:
        return None
    token = m.group(1)
    if token.lower().endswith(('.vsix', '.zip')):
        return None
    version = None
    if '@' in token:
        token, version = token.rsplit('@', 1)
    if '.' not in token:
        return None
    publisher, name = token.split('.', 1)
    return publisher, name, version


# ── Check logic ──────────────────────────────────────────────────────────────

def check_npm_package(pkg_name, version, get_dates_fn, ecosystem, today):
    release_dates = get_dates_fn(pkg_name)
    if release_dates is None:
        return None  # fail open
    if not version:
        if not release_dates:
            return None
        version = max(release_dates, key=lambda v: release_dates[v])
    if version not in release_dates:
        return None
    release_dt = release_dates[version]
    if days_ago(release_dt, today) >= MIN_AGE_DAYS:
        return None
    safe = find_safe_version(release_dates, today)
    return build_age_block_message(pkg_name, version, release_dt, safe, today, ecosystem)


def check_vscode_extension(publisher, name, version, today):
    """Return list of block message strings (may be multiple violations)."""
    ext_id = f"{publisher}.{name}"
    ver_label = f"@{version}" if version else ""

    data = query_vscode_marketplace(ext_id)
    if data is None:
        return []  # fail open on network error

    results = data.get("results", [{}])
    bucket = results[0] if results else {}
    extensions = bucket.get("extensions", [])

    total = 0
    for meta in bucket.get("resultMetadata", []):
        if meta.get("metadataType") == "ResultCount":
            for item in meta.get("metadataItems", []):
                if item.get("name") == "TotalCount":
                    total = item.get("count", 0)

    # ── Check 1: RemovedPackages ────────────────────────────────────────────
    if total == 0 or not extensions:
        return [build_vscode_removed_message(ext_id, ver_label)]

    ext = extensions[0]
    pub = ext.get("publisher", {})
    msgs = []

    # ── Check 2: Microsoft signature / domain verification ──────────────────
    if not pub.get("isDomainVerified", False):
        msgs.append(build_vscode_unverified_message(ext_id, ver_label, pub))

    # ── Check 3: 7-day age ──────────────────────────────────────────────────
    version_dates = {}
    for v in ext.get("versions", []):
        ver_str = v.get("version", "")
        date_str = v.get("lastUpdated", "")
        if ver_str and date_str:
            try:
                version_dates[ver_str] = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                )
            except ValueError:
                pass

    target_ver = version
    if target_ver is None:
        vlist = ext.get("versions", [])
        target_ver = vlist[0].get("version") if vlist else None

    if target_ver and target_ver in version_dates:
        release_dt = version_dates[target_ver]
        if days_ago(release_dt, today) < MIN_AGE_DAYS:
            safe = find_safe_version(version_dates, today)
            msgs.append(build_age_block_message(
                ext_id, target_ver, release_dt, safe, today, "vscode"
            ))

    return msgs


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, Exception):
        print("{}")
        return

    if payload.get("tool_name") != "Bash":
        print("{}")
        return

    cmd = payload.get("tool_input", {}).get("command", "")
    if not cmd:
        print("{}")
        return

    today = datetime.now(tz=timezone.utc)
    block_messages = []

    if NPM_PATTERN.search(cmd):
        for pkg_name, version in parse_npm_packages(cmd):
            msg = check_npm_package(pkg_name, version, get_npm_release_dates, "npm", today)
            if msg:
                block_messages.append(msg)

    elif UV_PATTERN.search(cmd):
        for pkg_name, version in parse_uv_packages(cmd):
            msg = check_npm_package(pkg_name, version, get_pypi_release_dates, "uv", today)
            if msg:
                block_messages.append(msg)

    elif VSCODE_PATTERN.search(cmd):
        result = parse_vscode_extension(cmd)
        if result:
            publisher, name, version = result
            block_messages.extend(check_vscode_extension(publisher, name, version, today))

    if block_messages:
        sep = "\n\n" + "=" * 42 + "\n\n"
        combined = "\n\n" + sep.join(block_messages)
        print(json.dumps({"continue": False, "stopReason": combined}, ensure_ascii=False))
    else:
        print("{}")


if __name__ == "__main__":
    main()
