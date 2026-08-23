#!/usr/bin/env python3
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

USERNAME = os.getenv("GITHUB_USERNAME", "lucasonline0")
TOKEN = os.getenv("PROFILE_TOKEN") or os.getenv("GITHUB_TOKEN", "")
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
        raise RuntimeError("A GitHub token is required")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    headers = dict(HEADERS)
    headers["Content-Type"] = "application/json"
    body = request_json("https://api.github.com/graphql", data=payload, headers=headers)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"]))
    return body["data"]


def get_user():
    query = """
    query($login: String!) {
      user(login: $login) {
        id
        followers { totalCount }
      }
    }
    """
    return graphql(query, {"login": USERNAME})["user"]


def get_repositories(owner_affiliations):
    query = """
    query($login: String!, $affiliations: [RepositoryAffiliation], $cursor: String) {
      user(login: $login) {
        repositories(
          first: 100,
          after: $cursor,
          ownerAffiliations: $affiliations,
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          totalCount
          edges {
            node {
              nameWithOwner
              isFork
              stargazers { totalCount }
              defaultBranchRef {
                target {
                  ... on Commit {
                    history { totalCount }
                  }
                }
              }
            }
          }
          pageInfo { endCursor hasNextPage }
        }
      }
    }
    """

    edges = []
    cursor = None
    total_count = 0
    while True:
        data = graphql(
            query,
            {"login": USERNAME, "affiliations": owner_affiliations, "cursor": cursor},
        )["user"]["repositories"]
        total_count = int(data["totalCount"])
        edges.extend(data["edges"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return total_count, edges


def get_authored_history(owner, repo, author_id):
    query = """
    query($owner: String!, $repo: String!, $authorId: ID!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor, author: {id: $authorId}) {
                edges {
                  node { additions deletions }
                }
                pageInfo { endCursor hasNextPage }
              }
            }
          }
        }
      }
    }
    """

    commits = additions = deletions = 0
    cursor = None
    while True:
        data = graphql(
            query,
            {"owner": owner, "repo": repo, "authorId": author_id, "cursor": cursor},
        )["repository"]
        if not data or not data.get("defaultBranchRef"):
            return 0, 0, 0
        history = data["defaultBranchRef"]["target"]["history"]
        for edge in history["edges"]:
            commits += 1
            additions += int(edge["node"]["additions"])
            deletions += int(edge["node"]["deletions"])
        if not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]
    return commits, additions, deletions


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"repos": {}}


def save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def calculate_commit_and_loc_stats(repo_edges, author_id):
    cache = load_cache()
    old_repos = cache.get("repos", {})
    new_repos = {}

    for edge in repo_edges:
        node = edge["node"]
        name_with_owner = node["nameWithOwner"]
        branch = node.get("defaultBranchRef")
        total_history = 0
        if branch and branch.get("target") and branch["target"].get("history"):
            total_history = int(branch["target"]["history"]["totalCount"])

        cached = old_repos.get(name_with_owner)
        if cached and int(cached.get("history_total", -1)) == total_history:
            new_repos[name_with_owner] = cached
            continue

        owner, repo = name_with_owner.split("/", 1)
        commits, additions, deletions = get_authored_history(owner, repo, author_id)
        new_repos[name_with_owner] = {
            "history_total": total_history,
            "commits": commits,
            "additions": additions,
            "deletions": deletions,
        }

    cache = {"repos": new_repos}
    save_cache(cache)

    commits = sum(int(v.get("commits", 0)) for v in new_repos.values())
    additions = sum(int(v.get("additions", 0)) for v in new_repos.values())
    deletions = sum(int(v.get("deletions", 0)) for v in new_repos.values())
    return commits, additions, deletions


def current_svg_values():
    try:
        svg = SVG_FILES[0].read_text(encoding="utf-8")
    except OSError:
        return {}
    values = {}
    for element_id in (
        "repo_data", "contrib_data", "star_data", "commit_data",
        "follower_data", "loc_data", "loc_add", "loc_del",
    ):
        match = re.search(
            rf'<tspan[^>]*id="{re.escape(element_id)}"[^>]*>(.*?)</tspan>',
            svg,
            flags=re.DOTALL,
        )
        if match:
            values[element_id] = match.group(1)
    return values


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


def dot_string(value, width):
    text = str(value)
    missing = max(0, width - len(text))
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

    dot_values = {
        "repo_data_dots": dot_string(formatted["repo_data"], 6),
        "star_data_dots": dot_string(formatted["star_data"], 14),
        "commit_data_dots": dot_string(formatted["commit_data"], 22),
        "follower_data_dots": dot_string(formatted["follower_data"], 10),
        "loc_data_dots": dot_string(formatted["loc_data"], 9),
        "loc_del_dots": dot_string(formatted["loc_del"], 7),
    }

    for element_id, value in {**formatted, **dot_values}.items():
        svg = replace_tspan(svg, element_id, value)

    path.write_text(svg, encoding="utf-8")


def main():
    previous = current_svg_values()
    try:
        user = get_user()
        repo_count, owner_edges = get_repositories(["OWNER"])
        contributed_count, all_edges = get_repositories(
            ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"]
        )
        stars = sum(int(edge["node"]["stargazers"]["totalCount"]) for edge in owner_edges)
        commits, additions, deletions = calculate_commit_and_loc_stats(all_edges, user["id"])

        stats = {
            "repo_data": repo_count,
            "contrib_data": contributed_count,
            "star_data": stars,
            "commit_data": commits,
            "follower_data": int(user["followers"]["totalCount"]),
            "loc_data": additions - deletions,
            "loc_add": additions,
            "loc_del": deletions,
        }
    except Exception as exc:
        print(f"Live stats refresh failed; keeping last generated values: {exc}")
        stats = {
            "repo_data": previous.get("repo_data", "--"),
            "contrib_data": previous.get("contrib_data", "--"),
            "star_data": previous.get("star_data", "--"),
            "commit_data": previous.get("commit_data", "--"),
            "follower_data": previous.get("follower_data", "--"),
            "loc_data": previous.get("loc_data", "--"),
            "loc_add": previous.get("loc_add", "--"),
            "loc_del": previous.get("loc_del", "--"),
        }

    for svg_file in SVG_FILES:
        update_svg(svg_file, stats)

    print("GitHub profile stats:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
