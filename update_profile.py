#!/usr/bin/env python3
import json
import os
import re
import urllib.error
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
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {body}") from exc


def public_stats():
    """Return public repository count, total stars on owned public repos, and followers."""
    profile = request_json(f"https://api.github.com/users/{USERNAME}")
    repos = int(profile.get("public_repos", 0))
    followers = int(profile.get("followers", 0))

    stars = 0
    page = 1
    while True:
        batch = request_json(
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?type=owner&sort=updated&per_page=100&page={page}"
        )
        stars += sum(int(repo.get("stargazers_count", 0)) for repo in batch)
        if len(batch) < 100:
            break
        page += 1

    return repos, stars, followers


def yearly_contributions():
    """Return GitHub contribution-calendar total for the current UTC year."""
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is required for contribution stats")

    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    payload = json.dumps(
        {
            "query": query,
            "variables": {
                "login": USERNAME,
                "from": start.isoformat().replace("+00:00", "Z"),
                "to": now.isoformat().replace("+00:00", "Z"),
            },
        }
    ).encode("utf-8")

    headers = dict(HEADERS)
    headers["Content-Type"] = "application/json"
    body = request_json("https://api.github.com/graphql", data=payload, headers=headers)

    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"]))

    return int(
        body["data"]["user"]["contributionsCollection"]["contributionCalendar"][
            "totalContributions"
        ]
    )


def replace_tspan(svg, element_id, value):
    pattern = rf'(<tspan[^>]*id="{re.escape(element_id)}"[^>]*>).*?(</tspan>)'
    updated, count = re.subn(
        pattern,
        rf"\g<1>{value}\g<2>",
        svg,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"Could not find SVG element id={element_id}")
    return updated


def current_tspan_value(svg, element_id):
    pattern = rf'<tspan[^>]*id="{re.escape(element_id)}"[^>]*>(.*?)</tspan>'
    match = re.search(pattern, svg, flags=re.DOTALL)
    return match.group(1) if match else "--"


def update_svg(path, values):
    with open(path, "r", encoding="utf-8") as handle:
        svg = handle.read()

    for element_id, value in values.items():
        if value is not None:
            svg = replace_tspan(svg, element_id, value)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg)


def old_value(element_id):
    """Keep the last known good value if one API endpoint temporarily fails."""
    try:
        with open(SVG_FILES[0], "r", encoding="utf-8") as handle:
            return current_tspan_value(handle.read(), element_id)
    except OSError:
        return "--"


def main():
    values = {}

    try:
        repos, stars, followers = public_stats()
        values.update(
            {
                "repo_data": f"{repos:,}",
                "star_data": f"{stars:,}",
                "follower_data": f"{followers:,}",
            }
        )
    except Exception as exc:
        print(f"Public stats lookup failed; keeping previous values: {exc}")
        values.update(
            {
                "repo_data": old_value("repo_data"),
                "star_data": old_value("star_data"),
                "follower_data": old_value("follower_data"),
            }
        )

    try:
        contributions = yearly_contributions()
        values["contribution_data"] = f"{contributions:,}"
    except Exception as exc:
        print(f"Contribution lookup failed; keeping previous value: {exc}")
        values["contribution_data"] = old_value("contribution_data")

    values["updated_data"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for svg_file in SVG_FILES:
        update_svg(svg_file, values)

    print("Updated profile stats:")
    for key, value in values.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
