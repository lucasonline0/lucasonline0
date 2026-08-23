#!/usr/bin/env python3
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

USERNAME = os.getenv("GITHUB_USERNAME", "lucasonline0")
TOKEN = os.getenv("GITHUB_TOKEN", "")
SVG_FILES = ("dark_mode.svg", "light_mode.svg")

HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"{USERNAME}-profile-readme",
    "X-GitHub-Api-Version": "2022-11-28",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


def request_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def public_stats():
    profile = request_json(f"https://api.github.com/users/{USERNAME}")
    repos = profile.get("public_repos", 0)
    followers = profile.get("followers", 0)

    stars = 0
    page = 1
    while True:
        batch = request_json(
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?type=owner&sort=updated&per_page=100&page={page}"
        )
        stars += sum(repo.get("stargazers_count", 0) for repo in batch)
        if len(batch) < 100:
            break
        page += 1

    return repos, stars, followers


def yearly_contributions():
    if not TOKEN:
        return "--"

    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar { totalContributions }
        }
      }
    }
    """
    payload = json.dumps({
        "query": query,
        "variables": {
            "login": USERNAME,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
    }).encode("utf-8")
    headers = dict(HEADERS)
    headers["Content-Type"] = "application/json"
    body = request_json("https://api.github.com/graphql", data=payload, headers=headers)
    return body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]


def replace_tspan(svg, element_id, value):
    pattern = rf'(<tspan[^>]*id="{re.escape(element_id)}"[^>]*>).*?(</tspan>)'
    updated, count = re.subn(pattern, rf"\g<1>{value}\g<2>", svg, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"Could not find SVG element id={element_id}")
    return updated


def update_svg(path, values):
    with open(path, "r", encoding="utf-8") as handle:
        svg = handle.read()
    for element_id, value in values.items():
        svg = replace_tspan(svg, element_id, value)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg)


def main():
    repos, stars, followers = public_stats()
    try:
        contributions = yearly_contributions()
    except Exception as exc:
        print(f"Contribution lookup failed: {exc}")
        contributions = "--"

    values = {
        "repo_data": f"{repos:,}",
        "star_data": f"{stars:,}",
        "follower_data": f"{followers:,}",
        "contribution_data": f"{contributions:,}" if isinstance(contributions, int) else contributions,
        "updated_data": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

    for svg_file in SVG_FILES:
        update_svg(svg_file, values)

    print("Updated profile stats:", values)


if __name__ == "__main__":
    main()
