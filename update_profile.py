#!/usr/bin/env python3
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USERNAME = os.getenv("GITHUB_USERNAME", "lucasonline0")
TOKEN = os.getenv("GITHUB_TOKEN", "")
SVG_FILES = (Path("dark_mode.svg"), Path("light_mode.svg"))
CACHE_FILE = Path("cache/profile_stats.json")

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
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {body}") from exc


def graphql(query, variables=None):
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is missing")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    headers = dict(HEADERS)
    headers["Content-Type"] = "application/json"
    body = request_json("https://api.github.com/graphql", data=payload, headers=headers)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"]))
    return body["data"]


def fetch_public_profile_and_repos():
    profile = request_json(f"https://api.github.com/users/{USERNAME}")
    repos = []
    page = 1
    while True:
        batch = request_json(
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?type=owner&sort=updated&per_page=100&page={page}"
        )
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return profile, repos


def fetch_contributed_repos():
    query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositoriesContributedTo(
          first: 100,
          after: $cursor,
          includeUserRepositories: true,
          contributionTypes: [COMMIT]
        ) {
          totalCount
          nodes { nameWithOwner isPrivate }
          pageInfo { endCursor hasNextPage }
        }
      }
    }
    """
    cursor = None
    names = []
    total = 0
    while True:
        data = graphql(query, {"login": USERNAME, "cursor": cursor})["user"]["repositoriesContributedTo"]
        total = int(data["totalCount"])
        for node in data.get("nodes") or []:
            names.append(node["nameWithOwner"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return total, names


def repo_head_oid(owner, repo):
    query = """
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        defaultBranchRef {
          target { ... on Commit { oid } }
        }
      }
    }
    """
    data = graphql(query, {"owner": owner, "repo": repo}).get("repository")
    if not data or not data.get("defaultBranchRef"):
        return None
    return data["defaultBranchRef"]["target"]["oid"]


def authored_history(owner, repo, author_id):
    query = """
    query($owner: String!, $repo: String!, $authorId: ID!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor, author: {id: $authorId}) {
                totalCount
                nodes { additions deletions }
                pageInfo { endCursor hasNextPage }
              }
            }
          }
        }
      }
    }
    """
    cursor = None
    commits = additions = deletions = 0
    while True:
        data = graphql(
            query,
            {"owner": owner, "repo": repo, "authorId": author_id, "cursor": cursor},
        ).get("repository")
        if not data or not data.get("defaultBranchRef"):
            return 0, 0, 0
        history = data["defaultBranchRef"]["target"]["history"]
        if cursor is None:
            commits = int(history["totalCount"])
        for node in history.get("nodes") or []:
            additions += int(node.get("additions") or 0)
            deletions += int(node.get("deletions") or 0)
        if not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]
    return commits, additions, deletions


def load_cache():
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("repos"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"repos": {}}


def save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def calculate_commit_and_loc_stats(repo_names, author_id):
    cache = load_cache()
    old = cache.get("repos", {})
    new = {}

    for name_with_owner in sorted(set(repo_names)):
        if "/" not in name_with_owner:
            continue
        owner, repo = name_with_owner.split("/", 1)
        cached = old.get(name_with_owner)
        try:
            head = repo_head_oid(owner, repo)
            if not head:
                continue
            if cached and cached.get("head_oid") == head:
                new[name_with_owner] = cached
                continue
            commits, additions, deletions = authored_history(owner, repo, author_id)
            new[name_with_owner] = {
                "head_oid": head,
                "commits": commits,
                "additions": additions,
                "deletions": deletions,
            }
            print(f"refreshed {name_with_owner}: {commits} commits, +{additions}/-{deletions}")
        except Exception as exc:
            print(f"warning: skipping {name_with_owner}: {exc}")
            if cached:
                new[name_with_owner] = cached

    save_cache({"repos": new})
    commits = sum(int(v.get("commits", 0)) for v in new.values())
    additions = sum(int(v.get("additions", 0)) for v in new.values())
    deletions = sum(int(v.get("deletions", 0)) for v in new.values())
    return commits, additions, deletions


def replace_tspan(svg, element_id, value):
    pattern = rf'(<tspan[^>]*id="{re.escape(element_id)}"[^>]*>).*?(</tspan>)'
    updated, count = re.subn(pattern, rf"\g<1>{value}\g<2>", svg, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"SVG element not found: {element_id}")
    return updated


def dot_string(value, width):
    missing = max(0, width - len(str(value)))
    if missing == 0:
        return ""
    if missing == 1:
        return " "
    if missing == 2:
        return ". "
    return " " + ("." * missing) + " "


def update_svg(path, stats):
    svg = path.read_text(encoding="utf-8")
    formatted = {
        key: f"{value:,}" if isinstance(value, int) else str(value)
        for key, value in stats.items()
    }
    dots = {
        "repo_data_dots": dot_string(formatted["repo_data"], 6),
        "star_data_dots": dot_string(formatted["star_data"], 14),
        "commit_data_dots": dot_string(formatted["commit_data"], 22),
        "follower_data_dots": dot_string(formatted["follower_data"], 10),
        "loc_data_dots": dot_string(formatted["loc_data"], 9),
        "loc_del_dots": dot_string(formatted["loc_del"], 7),
    }
    for element_id, value in {**formatted, **dots}.items():
        svg = replace_tspan(svg, element_id, value)
    path.write_text(svg, encoding="utf-8")


def main():
    profile, owned_repos = fetch_public_profile_and_repos()
    owned_names = [repo["full_name"] for repo in owned_repos]

    contributed_count = len(owned_names)
    contributed_names = []
    try:
        contributed_count, contributed_names = fetch_contributed_repos()
    except Exception as exc:
        print(f"warning: contributed repository lookup failed: {exc}")

    repo_names = sorted(set(owned_names + contributed_names))
    commits = additions = deletions = 0
    try:
        author_id = profile["node_id"]
        commits, additions, deletions = calculate_commit_and_loc_stats(repo_names, author_id)
    except Exception as exc:
        print(f"warning: commit/LOC calculation failed: {exc}")
        save_cache(load_cache())

    stats = {
        "repo_data": int(profile.get("public_repos", len(owned_names))),
        "contrib_data": int(contributed_count),
        "star_data": sum(int(repo.get("stargazers_count", 0)) for repo in owned_repos),
        "commit_data": commits,
        "follower_data": int(profile.get("followers", 0)),
        "loc_data": additions - deletions,
        "loc_add": additions,
        "loc_del": deletions,
    }

    for svg_file in SVG_FILES:
        update_svg(svg_file, stats)

    print("GitHub profile stats updated:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
