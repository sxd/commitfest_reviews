#!/usr/bin/env python3
"""Publish canonical CommitFest review reports from the local Amauta store.

Example:
    ./sync-commitfest-reviews.py --no-push

Only reports using the runner's canonical ``commitfest-<patch>-review`` slug
and the ``review-report-v2`` schema are exported.  Specialist handoffs,
thread ledgers, and failed execution reports therefore never leave Amauta.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import html
import os
import re
import subprocess
import sys
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen
from dataclasses import dataclass
from pathlib import Path


FINAL_SLUG = re.compile(r"^commitfest-(?P<patch_id>[1-9][0-9]*)-review$")
LIST_ENTRY = re.compile(r"^(commitfest-[1-9][0-9]*-review) \[tags:")
FRONT_MATTER = re.compile(r"(?ms)^---\n(?P<body>.*?)\n---\n")
HEADING = re.compile(r"(?m)^#\s+(?P<title>.+?)\s*$")


class SyncError(RuntimeError):
    """A report export or publication operation failed."""


@dataclass(frozen=True)
class Report:
    slug: str
    patch_id: int
    title: str
    verdict: str
    rendered_html: str


def run(*args: str, cwd: Path | None = None) -> str:
    environment = None
    if args[0] == "git":
        # The host's global SSH configuration may contain site-specific proxy
        # snippets.  This publisher only needs the user's GitHub key and must
        # remain runnable when an unrelated system snippet has unsafe modes.
        environment = os.environ.copy()
        environment.setdefault("GIT_SSH_COMMAND", "ssh -F /dev/null")
    result = subprocess.run(
        args,
        cwd=cwd,
        env=environment,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise SyncError(
            f"{' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def front_matter(markdown: str) -> dict[str, str]:
    match = FRONT_MATTER.search(markdown)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    return fields


def canonical_markdown(raw: str) -> str:
    """Remove the human-readable ``amauta report show`` header."""
    match = re.search(r"(?m)^---\n", raw)
    if not match:
        raise SyncError("report show returned no Markdown document")
    return raw[match.start() :]


def fetch_rendered_html(amauta_http: str, source: str, slug: str) -> str:
    """Fetch Amauta's safe, browser-ready rendering of one canonical report."""
    base_url = amauta_http.rstrip("/")
    url = (
        f"{base_url}/ui/reports/{quote(source, safe='')}/"
        f"{quote(slug, safe='')}/download/html"
    )
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310 -- fixed local Amauta URL
            rendered = response.read().decode("utf-8")
    except (OSError, URLError, UnicodeDecodeError) as exc:
        raise SyncError(f"could not fetch Amauta HTML for {slug}: {exc}") from exc
    if not rendered.startswith("<!doctype html>") or "report-doc" not in rendered:
        raise SyncError(f"Amauta returned an invalid HTML report for {slug}")
    return rendered


def list_final_reports(amauta: str, amauta_http: str, source: str) -> list[Report]:
    listing = run(amauta, "report", "list", "--source", source, "--limit", "500")
    slugs = sorted({match.group(1) for line in listing.splitlines() if (match := LIST_ENTRY.match(line))})
    reports: list[Report] = []

    for slug in slugs:
        slug_match = FINAL_SLUG.fullmatch(slug)
        if not slug_match:
            continue
        markdown = canonical_markdown(run(amauta, "report", "show", "--source", source, slug))
        fields = front_matter(markdown)
        if fields.get("schema") != "review-report-v2":
            continue
        target = fields.get("target", "")
        if (
            "commitfest" not in target.lower()
            and not re.search(r"\bcf\s*[0-9]+\b", target, flags=re.IGNORECASE)
            and not fields.get("commitfest")
        ):
            continue
        title = fields.get("title")
        if not title:
            heading = HEADING.search(markdown)
            title = heading.group("title") if heading else slug
        reports.append(
            Report(
                slug=slug,
                patch_id=int(slug_match.group("patch_id")),
                title=title,
                verdict=fields.get("verdict", "not recorded"),
                rendered_html=fetch_rendered_html(amauta_http, source, slug),
            )
        )
    return sorted(reports, key=lambda report: report.patch_id, reverse=True)


def atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(contents, encoding="utf-8")
    os.replace(temporary, path)


def index_page(reports: list[Report]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td><a href=\"reports/{html.escape(report.slug)}.html\">{report.patch_id}</a></td>"
        f"<td>{html.escape(report.title)}</td>"
        f"<td>{html.escape(report.verdict)}</td>"
        "</tr>"
        for report in reports
    ) or "<tr><td colspan=\"3\">No final review reports are available yet.</td></tr>"
    return f"""<!doctype html>
<html lang=\"en\">
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>PostgreSQL CommitFest reviews</title>
<link rel=\"stylesheet\" href=\"style.css\">
<main>
  <h1>PostgreSQL CommitFest reviews</h1>
  <p>Canonical final reports produced by the Amauta review harness. Specialist handoffs and intermediate artifacts stay private in Amauta.</p>
  <table>
    <thead><tr><th>Patch</th><th>Review</th><th>Verdict</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class=\"generated\">Only canonical final reports are synchronized every six hours; each report is rendered by Amauta.</p>
</main>
</html>
"""


def render(repository: Path, reports: list[Report]) -> None:
    docs = repository / "docs"
    reports_dir = docs / "reports"
    expected = {f"{report.slug}.html" for report in reports}
    for old_page in reports_dir.glob("commitfest-*-review.html"):
        if old_page.name not in expected:
            old_page.unlink()
    for report in reports:
        atomic_write(reports_dir / f"{report.slug}.html", report.rendered_html)
    atomic_write(docs / "index.html", index_page(reports))


def publish(repository: Path) -> bool:
    status = run("git", "status", "--porcelain", cwd=repository)
    if not status:
        return False
    run("git", "add", "docs", cwd=repository)
    run("git", "commit", "-m", "Publish CommitFest review reports", cwd=repository)
    run("git", "push", "origin", "HEAD:main", cwd=repository)
    return True


def require_clean_worktree(repository: Path) -> None:
    status = run("git", "status", "--porcelain", cwd=repository)
    if status:
        raise SyncError("repository has uncommitted changes; refusing to mix publication with them")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--source", default="postgresql-master")
    parser.add_argument("--amauta", default="/usr/bin/amauta")
    parser.add_argument("--amauta-http", default="http://127.0.0.1:8848",
                        help="base URL of the local Amauta HTTP service")
    parser.add_argument("--no-push", action="store_true", help="render pages without committing or pushing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository = args.repository.resolve()
    if not (repository / ".git").exists():
        raise SyncError(f"not a Git worktree: {repository}")
    lock_path = Path.home() / ".local/state/commitfest_reviews/sync.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("CommitFest review sync is already running", file=sys.stderr)
            return 0
        if not args.no_push:
            require_clean_worktree(repository)
            run("git", "pull", "--ff-only", "origin", "main", cwd=repository)
        reports = list_final_reports(args.amauta, args.amauta_http, args.source)
        render(repository, reports)
        changed = False if args.no_push else publish(repository)
    print(f"synchronized {len(reports)} final reports" + (" and published" if changed else ""))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"commitfest review sync: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
