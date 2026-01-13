from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from .cache import CommandCache
from .dashboard import Dashboard
from .executor_async import AsyncBatchExecutor
from .inspector import ResultsInspector, format_result, format_summary
from .runner import SafeCommandRunner
from .schemas import CommandInput, CommandResult


def count_lines(path: Path) -> int:
    count = 0
    with open(path, "rb") as f:
        for _ in f:
            count += 1
    return count


@click.group()
def cli() -> None:
    pass


DANGEROUS_FLAG = "--i-understand-awk-sed-can-execute-arbitrary-code"


@cli.command()
@click.argument("commands_file", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
@click.option("--cache-db", type=click.Path(path_type=Path), default=Path("cache.sqlite"))
@click.option("--concurrency", "-c", type=int, default=100)
@click.option("--timeout", "-t", type=int, default=30)
@click.option("--no-dashboard", is_flag=True, default=False, help="Disable live dashboard")
@click.option(
    DANGEROUS_FLAG,
    "allow_dangerous",
    is_flag=True,
    default=False,
    hidden=True,
    help="Allow awk/sed in pipes. DANGEROUS: they can execute arbitrary commands!",
)
def run(
    commands_file: Path,
    output: Path,
    cache_db: Path,
    concurrency: int,
    timeout: int,
    no_dashboard: bool,
    allow_dangerous: bool,
) -> None:
    if allow_dangerous:
        click.echo("=" * 60, err=True)
        click.echo("WARNING: Dangerous mode enabled!", err=True)
        click.echo("awk and sed can execute arbitrary commands via system()", err=True)
        click.echo("Only use this if you trust ALL commands in the input file!", err=True)
        click.echo("=" * 60, err=True)

    click.echo("Counting commands...", err=True)
    total_commands = count_lines(commands_file)
    click.echo(f"Found {total_commands:,} commands", err=True)

    cache = CommandCache(cache_db)
    runner = SafeCommandRunner(timeout=timeout, allow_dangerous=allow_dangerous)
    executor = AsyncBatchExecutor(cache, runner, concurrency=concurrency)

    def load_commands():
        with open(commands_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield CommandInput.from_json(line)

    output_handle = open(output, "w")
    dashboard: Dashboard | None = None

    if not no_dashboard:
        dashboard = Dashboard(
            total_commands=total_commands,
            output_path=output,
            cache_path=cache_db,
        )

    results_written = 0

    def on_result(result: CommandResult) -> None:
        nonlocal results_written
        output_handle.write(result.to_json() + "\n")
        results_written += 1
        if results_written % 50 == 0:
            output_handle.flush()

        if dashboard:
            dashboard.update(
                success=(result.return_code == 0),
                cached=result.cached,
            )
        elif results_written % 1000 == 0:
            click.echo(f"Processed {results_written:,} commands...", err=True)

    try:
        if dashboard:
            dashboard.start()
        asyncio.run(executor.run(load_commands(), on_result))
    finally:
        output_handle.flush()
        output_handle.close()
        cache.close()
        if dashboard:
            dashboard.stop()
            dashboard.print_final_summary()
        else:
            click.echo("-" * 60, err=True)
            click.echo(f"Total commands:  {results_written:,}", err=True)
            click.echo(f"\nResults written to: {output}", err=True)


@cli.command()
@click.option("--cache-db", type=click.Path(exists=True, path_type=Path), required=True)
def stats(cache_db: Path) -> None:
    cache = CommandCache(cache_db)
    cache_stats = cache.stats()
    cache.close()

    click.echo("=" * 40)
    click.echo("CACHE STATISTICS")
    click.echo("=" * 40)
    click.echo(f"Total entries:  {cache_stats.total_entries:,}")
    click.echo(f"Session hits:   {cache_stats.hits:,}")
    click.echo(f"Session misses: {cache_stats.misses:,}")
    click.echo(f"Hit rate:       {cache_stats.hit_rate:.1%}")


@cli.command("clear-cache")
@click.option("--cache-db", type=click.Path(exists=True, path_type=Path), required=True)
@click.confirmation_option(prompt="Are you sure you want to clear the cache?")
def clear_cache(cache_db: Path) -> None:
    cache = CommandCache(cache_db)
    count = cache.clear()
    cache.close()
    click.echo(f"Cleared {count:,} entries from cache")


@cli.command()
@click.argument("results_file", type=click.Path(exists=True, path_type=Path))
@click.option("--sample", "-n", type=int, default=5)
@click.option("--repo", type=str, default=None)
@click.option("--cmd-pattern", type=str, default=None)
@click.option("--status", type=click.Choice(["success", "failed", "cached"]), default=None)
@click.option("--index", "-i", type=int, default=None)
@click.option("--summary", "-s", is_flag=True, default=False)
def inspect(
    results_file: Path,
    sample: int,
    repo: str | None,
    cmd_pattern: str | None,
    status: str | None,
    index: int | None,
    summary: bool,
) -> None:
    inspector = ResultsInspector(results_file)

    if summary:
        click.echo(format_summary(inspector.summary()))
        return

    if index is not None:
        result = inspector.get_by_index(index)
        if result:
            click.echo(format_result(result, index))
        else:
            click.echo(f"No result at index {index}", err=True)
            sys.exit(1)
        return

    results: list[CommandResult] = []
    if repo:
        results = inspector.filter_by_repo(repo)
    elif cmd_pattern:
        results = inspector.filter_by_cmd_pattern(cmd_pattern)
    elif status:
        results = inspector.filter_by_status(status)
    else:
        results = inspector.sample(sample)

    if not results:
        click.echo("No matching results found", err=True)
        return

    for i, result in enumerate(results[:sample]):
        click.echo(format_result(result, i))
        click.echo("")


@cli.command("generate-test")
@click.argument("repo_path", type=click.Path(exists=True, path_type=Path))
@click.option("--count", "-n", type=int, default=1000)
def generate_test(repo_path: Path, count: int) -> None:
    import json
    import random

    templates = [
        "ls -la",
        "ls -la {subdir}",
        "find . -name '*.py' -type f | head -10",
        "find . -name '*.js' -type f | head -10",
        "find . -type f -name '*.md' | wc -l",
        "grep -r 'def ' . --include='*.py' | head -20",
        "grep -r 'function' . --include='*.js' | head -20",
        "grep -r 'class ' . --include='*.py' | head -10",
        "grep -r 'import' . --include='*.py' | wc -l",
        "git status",
        "git log --oneline -10",
        "git branch -a",
        "git diff --stat HEAD~1",
        "git ls-files | head -20",
        "git ls-files | wc -l",
        "git rev-parse HEAD",
        "wc -l {file}",
        "head -20 {file}",
        "tail -20 {file}",
        "cat {file} | head -50",
        "file {file}",
        "stat {file}",
    ]

    subdirs = [".", "src", "lib", "tests", "docs"]

    files: list[str] = []
    for ext in ["*.py", "*.js", "*.md", "*.txt", "*.json"]:
        import subprocess
        result = subprocess.run(
            ["find", str(repo_path), "-name", ext, "-type", "f"],
            capture_output=True,
            text=True,
        )
        found = [
            f for f in result.stdout.strip().split("\n")
            if f and not any(x in f for x in ["node_modules", ".git", "__pycache__"])
        ]
        files.extend(found[:50])

    if not files:
        files = ["."]

    for _ in range(count):
        template = random.choice(templates)
        cmd = template
        if "{subdir}" in cmd:
            cmd = cmd.replace("{subdir}", random.choice(subdirs))
        if "{file}" in cmd:
            cmd = cmd.replace("{file}", random.choice(files))

        line = json.dumps({"cmd": cmd, "repo": str(repo_path)})
        click.echo(line)


if __name__ == "__main__":
    cli()
