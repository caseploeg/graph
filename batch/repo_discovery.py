"""
Repo Discovery for Code Graph RAG

Search GitHub for repositories using `gh search repos` and build/expand repo lists.

Usage:
    # Search for MIT repos by language
    uv run python batch/repo_discovery.py \
        --output repos.json \
        --languages python,javascript,typescript \
        --min-stars 1000

    # Merge with existing txt file
    uv run python batch/repo_discovery.py \
        --output repos.json \
        --merge-from scripts/mit_repos.txt
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Supported languages (FULL status only)
SUPPORTED_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "rust", "cpp", "java", "lua"
})

# GitHub language name mapping to our internal names
GITHUB_LANGUAGE_MAP = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "rust": "rust",
    "c++": "cpp",
    "java": "java",
    "lua": "lua",
}

# Default star thresholds by language (popular languages need higher threshold)
DEFAULT_STAR_THRESHOLDS = {
    "python": 1000,
    "javascript": 1000,
    "typescript": 1000,
    "rust": 500,
    "java": 500,
    "cpp": 500,
    "lua": 200,
}


@dataclass
class RepoEntry:
    """Repository entry with metadata."""
    github_url: str
    owner: str
    name: str
    primary_language: str | None = None
    supported_language: bool = False
    stars: int = 0
    size_kb: int = 0
    source: str = "search"  # "search" or "file"
    api_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "github_url": self.github_url,
            "owner": self.owner,
            "name": self.name,
            "primary_language": self.primary_language,
            "supported_language": self.supported_language,
            "stars": self.stars,
            "size_kb": self.size_kb,
            "source": self.source,
            "api_error": self.api_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RepoEntry:
        return cls(
            github_url=data["github_url"],
            owner=data["owner"],
            name=data["name"],
            primary_language=data.get("primary_language"),
            supported_language=data.get("supported_language", False),
            stars=data.get("stars", 0),
            size_kb=data.get("size_kb", 0),
            source=data.get("source", "file"),
            api_error=data.get("api_error"),
        )


@dataclass
class RepoList:
    """Container for repo list with metadata."""
    version: str = "1.0"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sources: list[dict] = field(default_factory=list)
    repos: list[RepoEntry] = field(default_factory=list)

    @property
    def total_repos(self) -> int:
        return len(self.repos)

    @property
    def filtered_repos(self) -> int:
        return sum(1 for r in self.repos if r.supported_language)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "sources": self.sources,
            "total_repos": self.total_repos,
            "filtered_repos": self.filtered_repos,
            "repos": [r.to_dict() for r in self.repos],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RepoList:
        return cls(
            version=data.get("version", "1.0"),
            generated_at=data.get("generated_at", datetime.now(timezone.utc).isoformat()),
            sources=data.get("sources", []),
            repos=[RepoEntry.from_dict(r) for r in data.get("repos", [])],
        )

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"Saved {self.total_repos} repos ({self.filtered_repos} supported) to {path}")

    @classmethod
    def load(cls, path: Path) -> RepoList:
        with open(path) as f:
            return cls.from_dict(json.load(f))


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract owner/repo from GitHub URL."""
    # Match https://github.com/owner/repo or git@github.com:owner/repo
    patterns = [
        r"github\.com/([^/]+)/([^/\s]+?)(?:\.git)?$",
        r"github\.com:([^/]+)/([^/\s]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url.strip())
        if match:
            return match.group(1), match.group(2)
    return None


def normalize_language(lang: str | None) -> str | None:
    """Normalize GitHub language name to our internal name."""
    if not lang:
        return None
    return GITHUB_LANGUAGE_MAP.get(lang.lower())


def is_supported(lang: str | None) -> bool:
    """Check if language is fully supported."""
    return lang is not None and lang in SUPPORTED_LANGUAGES


def search_repos_by_language(
    language: str,
    license_type: str = "mit",
    min_stars: int = 1000,
    max_size_kb: int = 500000,
    limit: int = 100,
) -> list[RepoEntry]:
    """
    Search GitHub for repos using `gh search repos`.

    Returns list of RepoEntry objects.
    """
    # Map our internal language names to GitHub's
    gh_language = language
    if language == "cpp":
        gh_language = "c++"

    cmd = [
        "gh", "search", "repos",
        f"--license={license_type}",
        f"--language={gh_language}",
        f"--stars=>={min_stars}",
        f"--limit={limit}",
        "--json=fullName,stargazersCount,language,size,url",
    ]

    print(f"  Searching: {license_type} {gh_language} stars>={min_stars}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  Warning: gh search failed: {result.stderr}")
            return []

        data = json.loads(result.stdout)
        repos = []

        for item in data:
            full_name = item.get("fullName", "")
            if "/" not in full_name:
                continue

            owner, name = full_name.split("/", 1)
            lang_name = item.get("language")
            normalized_lang = normalize_language(lang_name)
            size_kb = item.get("size", 0)

            # Skip oversized repos
            if size_kb > max_size_kb:
                continue

            repos.append(RepoEntry(
                github_url=item.get("url", f"https://github.com/{full_name}"),
                owner=owner,
                name=name,
                primary_language=normalized_lang,
                supported_language=is_supported(normalized_lang),
                stars=item.get("stargazersCount", 0),
                size_kb=size_kb,
                source="search",
            ))

        print(f"  Found {len(repos)} repos for {language}")
        return repos

    except subprocess.TimeoutExpired:
        print(f"  Warning: gh search timed out for {language}")
        return []
    except json.JSONDecodeError as e:
        print(f"  Warning: Failed to parse gh output: {e}")
        return []


def search_all_languages(
    languages: list[str],
    license_type: str = "mit",
    min_stars: int | None = None,
    max_size_kb: int = 500000,
    limit_per_language: int = 100,
    rate_limit_delay: float = 2.0,
) -> list[RepoEntry]:
    """
    Search for repos across multiple languages.

    Uses per-language star thresholds if min_stars not specified.
    """
    all_repos: list[RepoEntry] = []
    seen_urls: set[str] = set()

    for i, lang in enumerate(languages):
        if i > 0:
            # Rate limit between searches
            time.sleep(rate_limit_delay)

        stars = min_stars if min_stars is not None else DEFAULT_STAR_THRESHOLDS.get(lang, 1000)

        repos = search_repos_by_language(
            language=lang,
            license_type=license_type,
            min_stars=stars,
            max_size_kb=max_size_kb,
            limit=limit_per_language,
        )

        # Deduplicate
        for repo in repos:
            if repo.github_url not in seen_urls:
                seen_urls.add(repo.github_url)
                all_repos.append(repo)

    return all_repos


def parse_txt_file(path: Path) -> list[RepoEntry]:
    """Parse a txt file with GitHub URLs (one per line)."""
    repos: list[RepoEntry] = []

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parsed = parse_github_url(line)
        if parsed:
            owner, name = parsed
            repos.append(RepoEntry(
                github_url=f"https://github.com/{owner}/{name}",
                owner=owner,
                name=name,
                source="file",
            ))
        else:
            print(f"  Warning: Could not parse URL: {line}")

    return repos


def fetch_repo_metadata(repo: RepoEntry, rate_limit_delay: float = 1.0) -> RepoEntry:
    """Fetch metadata for a repo using gh api."""
    cmd = [
        "gh", "api",
        f"repos/{repo.owner}/{repo.name}",
        "--jq", ".stargazers_count, .size, .language",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            repo.api_error = result.stderr.strip()
            return repo

        lines = result.stdout.strip().split("\n")
        if len(lines) >= 3:
            repo.stars = int(lines[0]) if lines[0] else 0
            repo.size_kb = int(lines[1]) if lines[1] else 0
            lang = lines[2] if lines[2] and lines[2] != "null" else None
            repo.primary_language = normalize_language(lang)
            repo.supported_language = is_supported(repo.primary_language)

        time.sleep(rate_limit_delay)
        return repo

    except (subprocess.TimeoutExpired, ValueError) as e:
        repo.api_error = str(e)
        return repo


def merge_repos(
    *repo_lists: list[RepoEntry],
    filter_supported: bool = False,
) -> list[RepoEntry]:
    """Merge multiple repo lists, deduplicating by URL."""
    seen_urls: set[str] = set()
    merged: list[RepoEntry] = []

    for repos in repo_lists:
        for repo in repos:
            if repo.github_url in seen_urls:
                continue
            if filter_supported and not repo.supported_language:
                continue
            seen_urls.add(repo.github_url)
            merged.append(repo)

    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover and build GitHub repo lists for batch processing"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--languages",
        type=str,
        default="python,javascript,typescript,rust,java,cpp,lua",
        help="Comma-separated list of languages to search (default: all supported)",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        default=None,
        help="Minimum stars (default: per-language thresholds)",
    )
    parser.add_argument(
        "--max-size-kb",
        type=int,
        default=500000,
        help="Maximum repo size in KB (default: 500000)",
    )
    parser.add_argument(
        "--license",
        type=str,
        default="mit",
        help="License type to filter by (default: mit)",
    )
    parser.add_argument(
        "--limit-per-language",
        type=int,
        default=100,
        help="Max repos per language from search (default: 100)",
    )
    parser.add_argument(
        "--merge-from",
        type=Path,
        action="append",
        default=[],
        help="Merge with existing txt file(s) containing GitHub URLs",
    )
    parser.add_argument(
        "--fetch-metadata",
        action="store_true",
        help="Fetch metadata for repos from txt files (slower)",
    )
    parser.add_argument(
        "--filter-supported",
        action="store_true",
        help="Only include repos with supported languages",
    )
    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Skip GitHub search, only process --merge-from files",
    )

    args = parser.parse_args()

    languages = [lang.strip().lower() for lang in args.languages.split(",")]

    # Validate languages
    for lang in languages:
        if lang not in SUPPORTED_LANGUAGES:
            print(f"Warning: '{lang}' is not a supported language, skipping")
    languages = [lang for lang in languages if lang in SUPPORTED_LANGUAGES]

    if not languages:
        print("Error: No valid languages specified")
        sys.exit(1)

    sources: list[dict] = []
    all_repos: list[RepoEntry] = []

    # Search GitHub
    if not args.skip_search:
        print(f"Searching GitHub for {len(languages)} languages...")
        search_repos = search_all_languages(
            languages=languages,
            license_type=args.license,
            min_stars=args.min_stars,
            max_size_kb=args.max_size_kb,
            limit_per_language=args.limit_per_language,
        )
        all_repos.extend(search_repos)
        sources.append({
            "type": "search",
            "query": f"license:{args.license} languages:{','.join(languages)}",
            "count": len(search_repos),
        })
        print(f"Found {len(search_repos)} repos from search")

    # Merge from txt files
    for txt_path in args.merge_from:
        if not txt_path.exists():
            print(f"Warning: File not found: {txt_path}")
            continue

        print(f"Parsing {txt_path}...")
        txt_repos = parse_txt_file(txt_path)

        if args.fetch_metadata:
            print(f"Fetching metadata for {len(txt_repos)} repos...")
            for i, repo in enumerate(txt_repos):
                print(f"  [{i+1}/{len(txt_repos)}] {repo.owner}/{repo.name}")
                fetch_repo_metadata(repo)

        all_repos.extend(txt_repos)
        sources.append({
            "type": "file",
            "path": str(txt_path),
            "count": len(txt_repos),
        })
        print(f"Added {len(txt_repos)} repos from {txt_path}")

    # Merge and deduplicate
    merged = merge_repos(all_repos, filter_supported=args.filter_supported)

    # Sort by stars (descending)
    merged.sort(key=lambda r: r.stars, reverse=True)

    # Create repo list
    repo_list = RepoList(
        sources=sources,
        repos=merged,
    )

    # Save
    args.output.parent.mkdir(parents=True, exist_ok=True)
    repo_list.save(args.output)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total repos:     {repo_list.total_repos}")
    print(f"Supported:       {repo_list.filtered_repos}")

    # Language breakdown
    lang_counts: dict[str, int] = {}
    for repo in merged:
        lang = repo.primary_language or "unknown"
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    print("\nBy language:")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        print(f"  {lang}: {count}")


if __name__ == "__main__":
    main()
