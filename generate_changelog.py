#!/usr/bin/env python3
import os
import sys
import argparse
import tempfile
from datetime import datetime, UTC
from typing import List, Optional

import git
from openai import OpenAI
from tqdm import tqdm

# -----------------------
# Prompt template
# -----------------------
SYS_PROMPT = (
    "You summarize Git changes into a concise human-readable CHANGELOG.\n"
    "- Input is a unified diff oriented from PARENT→COMMIT: lines starting with '+' were ADDED in the commit, '-' were REMOVED.\n"
    "- Produce 3–6 bullet points max, action-style, grouped by area if clear.\n"
    "- Mention important files/paths and breaking changes.\n"
    "- Prefer terse, technical phrasing; no fluff.\n"
)

USER_PROMPT_TMPL = (
    "Commit: {sha}\n"
    "Author: {author}\n"
    "Date (UTC): {date}\n"
    "Changed files ({nfiles}): {files}\n\n"
    "Diff (may be truncated):\n"
    "```\n{diff}\n```\n"
)

def parse_args():
    p = argparse.ArgumentParser(description="AI-generated CHANGELOG from Git diffs.")
    p.add_argument("--repo", default="https://github.com/shaman007/home-k3s", help="Git repo URL or local path")
    p.add_argument("--branch", default="main", help="Branch or ref")
    p.add_argument("--since", help='Filter commits since this date (e.g. "2024-01-01")')
    p.add_argument("--until", help='Filter commits until this date (e.g. "2025-12-31")')
    p.add_argument("--max-commits", type=int, default=0, help="Limit number of commits (0 = all)")
    p.add_argument("--output", default="CHANGELOG.md", help="Output file")
    p.add_argument("--model", default=os.getenv("CHANGELOG_MODEL", "gpt-4o-mini"), help="OpenAI model")
    p.add_argument("--per-commit-budget", type=int, default=8000, help="Max diff chars sent per commit")
    p.add_argument("--include-merges", action="store_true", help="Include merge commits (default: skip)")
    p.add_argument("--no-trim-whitespace", action="store_true", help="Do not ask git to ignore whitespace changes")
    p.add_argument("--no-renames", action="store_true", help="Do not simplify renames (more raw diffs)")
    return p.parse_args()

def ensure_repo(repo_arg: str, branch: str) -> git.Repo:
    if os.path.isdir(repo_arg) and os.path.isdir(os.path.join(repo_arg, ".git")):
        repo = git.Repo(repo_arg)
        repo.git.checkout(branch)
        return repo
    tmpdir = tempfile.mkdtemp(prefix="repo_")
    path = os.path.join(tmpdir, "repo")
    return git.Repo.clone_from(repo_arg, path, branch=branch)

def list_commits(repo: git.Repo, branch: str, since: Optional[str], until: Optional[str], max_commits: int) -> List[git.objects.Commit]:
    kwargs = {}
    if since: kwargs["since"] = since
    if until: kwargs["until"] = until
    it = repo.iter_commits(branch, **kwargs)
    commits = list(it)  # newest -> oldest
    if max_commits and max_commits > 0:
        commits = commits[:max_commits]
    commits.reverse()    # oldest -> newest (natural reading order)
    return commits

def commit_datetime_utc(commit: git.objects.Commit) -> datetime:
    # timezone-aware datetime (UTC)
    return datetime.fromtimestamp(commit.committed_date, UTC)

def diff_text_parent_to_commit(repo: git.Repo, commit: git.objects.Commit, trim_ws: bool, no_renames: bool) -> str:
    """
    Generate unified diff oriented from parent -> this commit.
    For root commit (no parent), uses --root so empty tree -> commit.
    """
    args = ["--root", "-p", commit.hexsha]
    if trim_ws:
        args.insert(0, "-w")          # ignore whitespace for heuristics
    if no_renames:
        args.insert(0, "--no-renames")
    # diff-tree prints parent->commit patch
    return repo.git.diff_tree(*args)

def changed_files(commit: git.objects.Commit) -> List[str]:
    # Use stats to obtain file list quickly
    try:
        return sorted(commit.stats.files.keys())
    except Exception:
        return []

def summarize(client: OpenAI, model: str, sha: str, author: str, date_utc: str, files: List[str], diff: str) -> str:
    files_display = ", ".join(files[:30]) + (", …" if len(files) > 30 else "")
    user_prompt = USER_PROMPT_TMPL.format(
        sha=sha,
        author=author,
        date=date_utc,
        nfiles=len(files),
        files=files_display if files_display else "(none)",
        diff=diff
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=350,
    )
    return resp.choices[0].message.content.strip()

def main():
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: set OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    repo = ensure_repo(args.repo, args.branch)
    commits = list_commits(repo, args.branch, args.since, args.until, args.max_commits)

    out_lines = ["# Changelog", ""]
    for c in tqdm(commits, desc="Summarizing commits"):
        if len(c.parents) > 1 and not args.include_merges:
            # Skip merges for signal/noise. Use --include-merges to include.
            continue

        sha = c.hexsha[:7]
        author = c.author.name
        date_utc = commit_datetime_utc(c).strftime("%Y-%m-%d")
        files = changed_files(c)

        try:
            patch = diff_text_parent_to_commit(
                repo,
                c,
                trim_ws=not args.no_trim_whitespace,
                no_renames=args.no_renames,
            )
            patch = patch[: args.per_commit_budget]
            if not patch.strip() and not files:
                continue

            summary = summarize(client, args.model, sha, author, date_utc, files, patch)
        except Exception as e:
            summary = f"⚠️ Error summarizing commit {sha}: {e}"

        out_lines.append(f"## {date_utc} – `{sha}` by {author}")
        out_lines.append(summary)
        out_lines.append("")

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))

    print(f"✅ Wrote {args.output}")

if __name__ == "__main__":
    main()

