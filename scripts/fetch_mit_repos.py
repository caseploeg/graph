"""
Fetch Popular MIT-Licensed GitHub Repositories

This script documents the steps used to fetch a large list of popular
MIT-licensed GitHub repositories using the GitHub CLI (gh).

Prerequisites:
    - GitHub CLI installed: https://cli.github.com/
    - Authenticated: `gh auth login`

Usage:
    uv run fetch_mit_repos.py
    uv run fetch_mit_repos.py --min-stars 10000 --output my_repos.txt
"""

import json
import subprocess
import sys
from pathlib import Path


def run_gh_search(min_stars: int = 0, max_stars: int | None = None, limit: int = 100) -> list[dict]:
    """
    Search GitHub for MIT-licensed repositories sorted by stars.

    The GitHub CLI `gh search repos` command supports:
        --license=mit     Filter by MIT license
        --sort=stars      Sort by star count
        --limit=N         Number of results (max 100 per query)
        --json            Output as JSON

    For pagination, we use star count ranges since gh doesn't support offset.
    """
    query_parts = ["license:mit"]

    if max_stars:
        query_parts.append(f"stars:{min_stars}..{max_stars}")
    elif min_stars > 0:
        query_parts.append(f"stars:>={min_stars}")

    query = " ".join(query_parts)

    cmd = [
        "gh", "search", "repos",
        "--license=mit",
        "--sort=stars",
        f"--limit={limit}",
        "--json=fullName,stargazersCount,url",
    ]

    if max_stars or min_stars > 0:
        cmd.append("--")
        if max_stars:
            cmd.append(f"stars:{min_stars}..{max_stars}")
        else:
            cmd.append(f"stars:>={min_stars}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error running gh: {result.stderr}", file=sys.stderr)
        return []

    return json.loads(result.stdout)


def verify_license(repo_full_name: str) -> str | None:
    """
    Verify a repository's license using gh repo view.

    Returns the license name or None if verification fails.
    """
    cmd = [
        "gh", "repo", "view", repo_full_name,
        "--json=licenseInfo",
        "--jq=.licenseInfo.name"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        return result.stdout.strip()
    return None


def fetch_all_mit_repos(min_stars: int = 15000) -> list[dict]:
    """
    Fetch all MIT-licensed repos above a star threshold using pagination.

    Since GitHub search API limits to 100 results per query, we paginate
    by using decreasing star count ranges.
    """
    all_repos = []
    current_max = None

    print(f"Fetching MIT-licensed repos with {min_stars}+ stars...")

    while True:
        repos = run_gh_search(min_stars=min_stars, max_stars=current_max, limit=100)

        if not repos:
            break

        all_repos.extend(repos)
        print(f"  Fetched {len(repos)} repos (total: {len(all_repos)})")

        lowest_stars = min(r["stargazersCount"] for r in repos)

        if lowest_stars <= min_stars or len(repos) < 100:
            break

        current_max = lowest_stars - 1

    seen = set()
    unique_repos = []
    for repo in all_repos:
        if repo["url"] not in seen:
            seen.add(repo["url"])
            unique_repos.append(repo)

    unique_repos.sort(key=lambda x: x["stargazersCount"], reverse=True)

    return unique_repos


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetch MIT-licensed GitHub repos")
    parser.add_argument("--min-stars", type=int, default=15000,
                        help="Minimum star count (default: 15000)")
    parser.add_argument("--output", type=str, default="mit_repos.txt",
                        help="Output file path (default: mit_repos.txt)")
    parser.add_argument("--verify", type=int, default=5,
                        help="Number of repos to verify license (default: 5)")
    args = parser.parse_args()

    repos = fetch_all_mit_repos(min_stars=args.min_stars)

    print(f"\nTotal unique repos found: {len(repos)}")

    if args.verify > 0:
        print(f"\nVerifying licenses for {args.verify} random repos...")
        import random
        samples = random.sample(repos, min(args.verify, len(repos)))
        for repo in samples:
            license_name = verify_license(repo["fullName"])
            status = "OK" if license_name == "MIT License" else f"WARN: {license_name}"
            print(f"  {repo['fullName']}: {status}")

    output_path = Path(args.output)
    with output_path.open("w") as f:
        f.write(f"# MIT-Licensed GitHub Repositories\n")
        f.write(f"# Generated using: gh search repos --license=mit --sort=stars\n")
        f.write(f"# Total: {len(repos)} repositories with {args.min_stars}+ stars\n\n")

        for repo in repos:
            f.write(f"{repo['url']}\n")

    print(f"\nSaved {len(repos)} repo URLs to {output_path}")


if __name__ == "__main__":
    main()
