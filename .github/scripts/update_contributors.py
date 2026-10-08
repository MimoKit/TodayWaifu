import os
import re
import sys
import html
import json
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from urllib.request import Request, urlopen

README_PATH = Path(__file__).resolve().parents[2] / "README.md"
START_MARKER = "<!-- contributors:start -->"
END_MARKER = "<!-- contributors:end -->"
API_HOST = "api.github.com"


def fetch_contributors(repository: str, token: str) -> list[dict]:
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("GITHUB_REPOSITORY must have the owner/repository format")

    url = f"https://{API_HOST}/repos/{repository}/contributors?per_page=100"
    contributors = []
    visited = set()

    while url:
        parsed_url = urlsplit(url)
        if parsed_url.scheme != "https" or parsed_url.netloc != API_HOST:
            raise ValueError("GitHub API pagination returned an unexpected URL")
        if url in visited:
            raise RuntimeError("GitHub API returned a pagination loop")
        visited.add(url)

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "TodayWaifu-contributors-refresh",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        request = Request(url, headers=headers)
        with urlopen(request, timeout=30) as response:
            page = json.loads(response.read())
            if not isinstance(page, list):
                raise RuntimeError("GitHub contributors API returned an invalid response")
            contributors.extend(page)
            link_header = response.headers.get("Link", "")

        next_link = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        url = next_link.group(1) if next_link else ""

    return [
        contributor
        for contributor in contributors
        if contributor.get("login")
        and contributor.get("type") != "Bot"
        and not contributor["login"].lower().endswith("[bot]")
    ]


def render_contributors(contributors: list[dict]) -> str:
    rows = []
    for offset in range(0, len(contributors), 6):
        cells = []
        for contributor in contributors[offset : offset + 6]:
            login = html.escape(str(contributor["login"]), quote=True)
            profile_url = html.escape(str(contributor["html_url"]), quote=True)
            avatar_parts = urlsplit(str(contributor["avatar_url"]))
            avatar_query = parse_qsl(avatar_parts.query, keep_blank_values=True)
            avatar_query.append(("size", "64"))
            avatar_url = html.escape(
                urlunsplit(
                    (
                        avatar_parts.scheme,
                        avatar_parts.netloc,
                        avatar_parts.path,
                        urlencode(avatar_query),
                        avatar_parts.fragment,
                    )
                ),
                quote=True,
            )
            contributions = int(contributor.get("contributions", 0))
            contribution_label = "contribution" if contributions == 1 else "contributions"
            cells.append(
                "    <td align=\"center\">"
                f'<a href="{profile_url}"><img src="{avatar_url}" width="56" height="56" '
                f'alt="{login}" title="{login}" /></a><br />'
                f"<sub><b>{login}</b></sub><br />"
                f"<sub>{contributions} {contribution_label}</sub></td>"
            )
        rows.append("  <tr>\n" + "\n".join(cells) + "\n  </tr>")

    if not rows:
        raise RuntimeError("GitHub returned no non-bot contributors; README was not changed")
    return "<table>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"


def update_readme(content: str, rendered_contributors: str) -> str:
    start = content.find(START_MARKER)
    end = content.find(END_MARKER)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError("README contributor markers are missing or out of order")

    body_start = start + len(START_MARKER)
    return (
        content[:body_start]
        + "\n"
        + rendered_contributors
        + "\n"
        + content[end:]
    )


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    try:
        contributors = fetch_contributors(repository, token)
        rendered = render_contributors(contributors)
        content = README_PATH.read_text(encoding="utf-8")
        updated = update_readme(content, rendered)
        if updated != content:
            README_PATH.write_text(updated, encoding="utf-8", newline="\n")
            print(f"Updated contributor avatars and counts for {len(contributors)} contributors.")
        else:
            print("Contributor avatars and counts are already up to date.")
    except (HTTPError, URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Unable to refresh contributor data: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
