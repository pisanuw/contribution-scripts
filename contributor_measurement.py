#!/usr/bin/env python3
"""Measure and rank contributor activity for a public GitHub repository."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

GITHUB_API_BASE = "https://api.github.com"

# Weights are intentionally easy to tune in code.
WEIGHTS = {
    "commits": 1.0,
    "line_changes": 0.01,
    "merged_prs": 5.0,
    "issues_closed": 2.0,
    "issue_comments": 1.0,
}


@dataclasses.dataclass
class ContributorStats:
    name: str
    contributor_type: str = "user"
    commits: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    merged_prs: int = 0
    issues_closed: int = 0
    issue_comments: int = 0

    @property
    def weighted_score(self) -> float:
        return (
            self.commits * WEIGHTS["commits"]
            + (self.lines_added + self.lines_removed) * WEIGHTS["line_changes"]
            + self.merged_prs * WEIGHTS["merged_prs"]
            + self.issues_closed * WEIGHTS["issues_closed"]
            + self.issue_comments * WEIGHTS["issue_comments"]
        )


def is_bot(identity: str) -> bool:
    return identity.strip().lower().endswith("[bot]")


def parse_repo_url(repo_url: str) -> Tuple[str, str]:
    """Return (owner, repo_name)."""
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.scheme and parsed.netloc:
        path = parsed.path
    else:
        # handle git@github.com:owner/repo.git
        path = repo_url.split(":", 1)[-1]

    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Unsupported GitHub repository URL: {repo_url}")

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def run_git(args: List[str], cwd: Optional[str] = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def clone_repository(repo_url: str, clone_root: Optional[str] = None) -> str:
    temp_dir = clone_root or tempfile.mkdtemp(prefix="contrib-measure-")
    repo_name = parse_repo_url(repo_url)[1]
    clone_dir = os.path.join(temp_dir, repo_name)
    run_git(["clone", "--no-single-branch", repo_url, clone_dir])
    run_git(["fetch", "--all", "--tags", "--prune"], cwd=clone_dir)
    return clone_dir


def collect_git_stats(repo_dir: str) -> Dict[str, ContributorStats]:
    output = run_git(["log", "--all", "--numstat", "--format=--COMMIT--%x09%aN%x09%aE"], cwd=repo_dir)
    stats: Dict[str, ContributorStats] = {}
    current_author: Optional[str] = None

    for line in output.splitlines():
        if line.startswith("--COMMIT--\t"):
            _, author_name, author_email = line.split("\t", 2)
            current_author = author_name.strip() or author_email.strip()
            if current_author not in stats:
                stats[current_author] = ContributorStats(
                    name=current_author,
                    contributor_type="bot" if is_bot(current_author) else "user",
                )
            stats[current_author].commits += 1
            continue

        if not current_author:
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            continue

        added_raw, removed_raw = parts[0], parts[1]
        added = int(added_raw) if added_raw.isdigit() else 0
        removed = int(removed_raw) if removed_raw.isdigit() else 0
        stats[current_author].lines_added += added
        stats[current_author].lines_removed += removed

    return stats


def github_api_get_json(url: str, token: Optional[str] = None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "contributor-measurement-script",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            import json

            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed ({exc.code}) for {url}: {detail}") from exc


def paginate(endpoint: str, token: Optional[str] = None) -> Iterable[dict]:
    page = 1
    while True:
        separator = "&" if "?" in endpoint else "?"
        url = f"{GITHUB_API_BASE}{endpoint}{separator}per_page=100&page={page}"
        payload = github_api_get_json(url, token=token)
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if isinstance(item, dict):
                yield item
        page += 1


def merge_api_stats(
    stats: Dict[str, ContributorStats],
    owner: str,
    repo: str,
    token: Optional[str] = None,
) -> None:
    # merged PRs
    for pr in paginate(f"/repos/{owner}/{repo}/pulls?state=closed", token=token):
        if not pr.get("merged_at"):
            continue
        login = ((pr.get("user") or {}).get("login") or "").strip()
        if not login:
            continue
        contributor = stats.setdefault(
            login,
            ContributorStats(name=login, contributor_type="bot" if is_bot(login) else "user"),
        )
        contributor.merged_prs += 1

    # issue comments
    for comment in paginate(f"/repos/{owner}/{repo}/issues/comments", token=token):
        login = ((comment.get("user") or {}).get("login") or "").strip()
        if not login:
            continue
        contributor = stats.setdefault(
            login,
            ContributorStats(name=login, contributor_type="bot" if is_bot(login) else "user"),
        )
        contributor.issue_comments += 1

    # closed issues (excluding pull requests)
    for issue in paginate(f"/repos/{owner}/{repo}/issues?state=closed", token=token):
        if "pull_request" in issue:
            continue
        login = ((issue.get("closed_by") or {}).get("login") or "").strip()
        if not login:
            continue
        contributor = stats.setdefault(
            login,
            ContributorStats(name=login, contributor_type="bot" if is_bot(login) else "user"),
        )
        contributor.issues_closed += 1


def get_repo_metadata(owner: str, repo: str, token: Optional[str] = None) -> dict:
    payload = github_api_get_json(f"{GITHUB_API_BASE}/repos/{owner}/{repo}", token=token)
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected repository metadata response from GitHub API")
    return payload


def render_markdown_report(
    repo_url: str,
    owner: str,
    repo: str,
    metadata: dict,
    contributors: List[ContributorStats],
    api_warnings: Optional[List[str]] = None,
) -> str:
    if "fork" in metadata:
        is_fork_label = "Yes" if metadata.get("fork") else "No"
        parent = ((metadata.get("parent") or {}).get("full_name") or "-") if metadata.get("fork") else "-"
    else:
        is_fork_label = "Unknown"
        parent = "-"

    lines = [
        f"# Contribution Report: {owner}/{repo}",
        "",
        f"- Repository URL: {repo_url}",
        f"- Fork: {is_fork_label}",
        f"- Parent Repository: {parent}",
        f"- Generated At (UTC): {dt.datetime.now(dt.timezone.utc).isoformat()}",
        "",
        "## Weights",
        "",
        "| Metric | Weight |",
        "|---|---:|",
        f"| Commits | {WEIGHTS['commits']} |",
        f"| Line changes (added + removed) | {WEIGHTS['line_changes']} |",
        f"| Merged PRs | {WEIGHTS['merged_prs']} |",
        f"| Issues closed | {WEIGHTS['issues_closed']} |",
        f"| Issue comments | {WEIGHTS['issue_comments']} |",
        "",
        "## Contributor Rankings",
        "",
        "| Rank | Contributor | Type | Commits | Lines Added | Lines Removed | Merged PRs | Issues Closed | Issue Comments | Weighted Score |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for rank, contributor in enumerate(contributors, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    contributor.name,
                    contributor.contributor_type,
                    str(contributor.commits),
                    str(contributor.lines_added),
                    str(contributor.lines_removed),
                    str(contributor.merged_prs),
                    str(contributor.issues_closed),
                    str(contributor.issue_comments),
                    f"{contributor.weighted_score:.2f}",
                ]
            )
            + " |"
        )

    if api_warnings:
        lines.extend(["", "## API Warnings", ""])
        lines.extend([f"- {warning}" for warning in api_warnings])

    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_url", help="Public GitHub repository URL")
    parser.add_argument(
        "-o",
        "--output",
        default="contributor_report.md",
        help="Path to Markdown report output",
    )
    parser.add_argument(
        "--github-token",
        default=os.getenv("GITHUB_TOKEN"),
        help="GitHub token (defaults to GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--keep-clone",
        action="store_true",
        help="Keep local clone instead of deleting temporary directory",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    owner, repo = parse_repo_url(args.repo_url)

    clone_root = tempfile.mkdtemp(prefix="contrib-measure-")
    repo_dir = clone_repository(args.repo_url, clone_root=clone_root)
    stats = collect_git_stats(repo_dir)

    metadata: dict = {}
    api_warnings: List[str] = []
    try:
        metadata = get_repo_metadata(owner, repo, token=args.github_token)
        merge_api_stats(stats, owner, repo, token=args.github_token)
    except RuntimeError as error:
        api_warnings.append(str(error))

    contributors = sorted(
        stats.values(),
        key=lambda c: (c.weighted_score, c.commits, c.lines_added + c.lines_removed),
        reverse=True,
    )

    report = render_markdown_report(
        args.repo_url, owner, repo, metadata, contributors, api_warnings=api_warnings
    )
    with open(args.output, "w", encoding="utf-8") as output_file:
        output_file.write(report)

    if args.keep_clone:
        print(f"Local clone kept at: {repo_dir}")
    else:
        shutil.rmtree(clone_root, ignore_errors=True)

    print(f"Report written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
