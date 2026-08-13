#!/usr/bin/env python3
"""
check_links.py

1. Scans the ServiceNowDocs "australia" branch (checked out by the GitHub
   Action into ./servicenowdocs) for every link that starts with:
       https://support.servicenow.com/kb?sys_kb_id
2. Visits each unique link and reads the HTML <title> tag.
3. Buckets each link into one of two groups:
       - "Knowledge Article View - Now Support Portal"  -> the generic
         app-shell title the portal shows when it could NOT load a real
         article (usually because the article needs a login, is retired,
         or doesn't exist).
       - Everything else -> the portal returned a real, specific article
         title, meaning the link is genuinely public and working.
4. Writes:
       docs/data/latest.json   -> full detail for the most recent run
       docs/data/history.json  -> one summary entry per calendar month,
                                    used to draw the month-over-month chart
"""

import json
import os
import re
import time
import html
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOCS_REPO_PATH = "servicenowdocs"                 # where the other repo was checked out
DATA_DIR = os.path.join("docs", "data")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")

TARGET_TITLE = "Knowledge Article View - Now Support Portal"

# Matches links like:
# https://support.servicenow.com/kb?sys_kb_id=e831c74edb51ed10770be6be13961912&...
LINK_PATTERN = re.compile(
    r"https://support\.servicenow\.com/kb\?sys_kb_id=[0-9a-fA-F]{32}(?:&[\w\-.=%]*)?"
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; KB-Link-Checker/1.0; "
        "+https://github.com/) Python-requests"
    )
}
REQUEST_TIMEOUT = 20  # seconds
DELAY_BETWEEN_REQUESTS = 1.0  # seconds, be polite to the server

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Step 1: find every matching link in the docs repo
# ---------------------------------------------------------------------------

def find_links(repo_path):
    links = set()
    for root, dirs, files in os.walk(repo_path):
        # skip git internals
        if ".git" in dirs:
            dirs.remove(".git")
        for filename in files:
            filepath = os.path.join(root, filename)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            for match in LINK_PATTERN.findall(content):
                links.add(match)
    return sorted(links)


# ---------------------------------------------------------------------------
# Step 2: fetch each link and read its <title>
# ---------------------------------------------------------------------------

def check_link(url):
    """Returns (title, status_code_or_None, error_or_None)."""
    try:
        resp = requests.get(
            url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
    except requests.RequestException as exc:
        return None, None, str(exc)

    match = TITLE_RE.search(resp.text or "")
    if not match:
        return None, resp.status_code, "No <title> tag found in response"

    title = html.unescape(match.group(1)).strip()
    # collapse internal whitespace/newlines
    title = re.sub(r"\s+", " ", title)
    return title, resp.status_code, None


# ---------------------------------------------------------------------------
# Step 3: run the checks
# ---------------------------------------------------------------------------

def run_checks(links):
    results = []
    for i, url in enumerate(links, start=1):
        title, status_code, error = check_link(url)

        if error is not None:
            category = "other"
            display_title = f"[ERROR] {error}"
        elif title == TARGET_TITLE:
            category = "knowledge_article_view"
            display_title = title
        else:
            category = "other"
            display_title = title

        results.append(
            {
                "url": url,
                "title": display_title,
                "status_code": status_code,
                "category": category,
            }
        )

        print(f"[{i}/{len(links)}] {url} -> {display_title!r} ({category})")

        if i < len(links):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return results


# ---------------------------------------------------------------------------
# Step 4: write output files
# ---------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def write_latest(results, checked_at):
    kav_count = sum(1 for r in results if r["category"] == "knowledge_article_view")
    other_count = sum(1 for r in results if r["category"] == "other")

    data = {
        "last_checked": checked_at,
        "summary": {
            "total": len(results),
            "knowledge_article_view_count": kav_count,
            "other_count": other_count,
        },
        "links": results,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return data["summary"]


def upsert_history(summary, checked_at):
    history_data = load_json(HISTORY_PATH, {"history": []})
    month_key = checked_at[:7]  # "YYYY-MM"

    entry = {
        "month": month_key,
        "checked_at": checked_at,
        "total": summary["total"],
        "knowledge_article_view_count": summary["knowledge_article_view_count"],
        "other_count": summary["other_count"],
    }

    history = history_data.get("history", [])
    # Replace this month's entry if it already exists, otherwise append
    replaced = False
    for i, existing in enumerate(history):
        if existing.get("month") == month_key:
            history[i] = entry
            replaced = True
            break
    if not replaced:
        history.append(entry)

    history.sort(key=lambda e: e["month"])
    history_data["history"] = history

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history_data, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Scanning {DOCS_REPO_PATH} for matching links...")
    links = find_links(DOCS_REPO_PATH)
    print(f"Found {len(links)} unique link(s) matching the target pattern.")

    results = run_checks(links)
    summary = write_latest(results, checked_at)
    upsert_history(summary, checked_at)

    print("\nSummary:")
    print(f"  Total links checked:                        {summary['total']}")
    print(f"  '{TARGET_TITLE}':  {summary['knowledge_article_view_count']}")
    print(f"  All other titles:                           {summary['other_count']}")


if __name__ == "__main__":
    main()
