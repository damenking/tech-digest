#!/usr/bin/env python3
"""Generate a daily tech digest from curated sources."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import feedparser
import requests
import yaml
from jinja2 import Template


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def fetch_rss_feeds(sources: list[dict], errors: list[str]) -> list[dict]:
    """Fetch and parse RSS feeds, returning normalized article dicts."""
    articles = []
    for source in sources:
        try:
            feed = feedparser.parse(source["url"])
            if feed.bozo and not feed.entries:
                errors.append(f"Couldn't connect to {source['name']}")
                continue
            if not feed.entries:
                errors.append(f"No articles found from {source['name']}")
                continue
            for entry in feed.entries[:10]:  # Cap per source to avoid flooding
                articles.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:500],
                    "source": source["name"],
                    "category": source.get("category", "tech"),
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            errors.append(f"Couldn't connect to {source['name']}: {e}")
    return articles


def fetch_hackernews(top_n: int = 30, errors: list[str] | None = None) -> list[dict]:
    """Fetch top stories from Hacker News API."""
    articles = []
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        )
        story_ids = resp.json()[:top_n]

        for story_id in story_ids:
            try:
                story = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                    timeout=5,
                ).json()
                if story and story.get("title"):
                    articles.append({
                        "title": story["title"],
                        "link": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                        "summary": "",
                        "source": "Hacker News",
                        "category": "tech",
                        "published": "",
                        "score": story.get("score", 0),
                    })
            except Exception:
                continue
    except Exception as e:
        if errors is not None:
            errors.append(f"Couldn't connect to Hacker News: {e}")
    return articles


def build_claude_prompt(articles: list[dict], config: dict) -> str:
    """Build the prompt for Claude to generate the digest."""
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    topics = ", ".join(config["topics"])

    articles_json = json.dumps(articles, indent=2, default=str)

    exclude_list = "\n".join(f"  - {e}" for e in config.get("exclude", []))
    prioritize_list = "\n".join(f"  - {p}" for p in config.get("prioritize", []))

    return f"""You are a tech news digest curator. Today is {today}.

Here are raw articles pulled from various tech news sources today:

{articles_json}

Your job is to produce a daily digest with these rules:

1. **Deduplicate**: If the same story appears from multiple sources, merge them into one entry. Prefer the source with the best summary.
2. **Filter**: Remove clickbait, fluff, listicles, and sponsored content. Keep only genuinely newsworthy items.
3. **EXCLUDE these topics — do NOT include stories about**:
{exclude_list}
4. **PRIORITIZE these topics — boost them when selecting stories**:
{prioritize_list}
5. **Select**: Pick the {config['max_stories']} most important/interesting stories from what remains after filtering.
6. **Categorize**: Assign each story to one of these topics: {topics}
7. **Summarize**: Write a DETAILED summary for each story — 8 to 12 sentences. Give real context: what happened, why it matters, who it affects, relevant background, and your take. The reader should feel fully informed without needing to click through.
8. **Editorial voice**: {config['editorial_voice']}

Return your response as JSON with this exact structure:
{{
  "date": "{today}",
  "intro": "A 1-2 sentence overview of today's biggest theme or most important story.",
  "sections": [
    {{
      "topic": "Topic Name",
      "stories": [
        {{
          "title": "Story headline",
          "summary": "Your detailed 8-12 sentence editorial summary.",
          "source": "Original source name",
          "link": "URL to the article"
        }}
      ]
    }}
  ]
}}

Only include sections that have stories. Order sections by importance. Return ONLY valid JSON, no markdown fences."""


def generate_with_claude(prompt: str, config: dict) -> dict:
    """Call Claude API to generate the digest."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_PERSONAL_API_KEY"])

    message = client.messages.create(
        model=config["model"],
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text

    # Parse the JSON response
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Try to extract JSON from the response if it has extra text
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
        raise


def find_previous_digest(docs_dir: Path, today_str: str) -> str | None:
    """Find the most recent digest file before today."""
    existing = sorted(docs_dir.glob("????-??-??.html"), reverse=True)
    for f in existing:
        if f.stem < today_str:
            return f.name
    return None


def render_html(
    digest: dict,
    errors: list[str] | None = None,
    previous_file: str | None = None,
) -> str:
    """Render the digest as a mobile-friendly HTML page."""
    template_path = Path(__file__).parent / "template.html"
    with open(template_path) as f:
        template = Template(f.read())

    return template.render(
        date=digest["date"],
        intro=digest["intro"],
        sections=digest["sections"],
        errors=errors or [],
        previous_file=previous_file,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def render_redirect(target_file: str) -> str:
    """Render a simple redirect page pointing to the latest digest."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url={target_file}">
    <title>Tech Digest</title>
</head>
<body>
    <p>Redirecting to <a href="{target_file}">today's digest</a>...</p>
</body>
</html>"""


def main():
    print("Loading config...")
    config = load_config()
    errors = []

    print("Fetching RSS feeds...")
    articles = fetch_rss_feeds(config["sources"]["rss"], errors)

    if config["sources"]["hackernews"]["enabled"]:
        print("Fetching Hacker News...")
        hn_articles = fetch_hackernews(config["sources"]["hackernews"]["top_n"], errors)
        articles.extend(hn_articles)

    print(f"Collected {len(articles)} articles from all sources")
    if errors:
        print(f"Source errors: {errors}")

    print("Generating digest with Claude...")
    prompt = build_claude_prompt(articles, config)
    digest = generate_with_claude(prompt, config)

    print("Rendering HTML...")
    docs_dir = Path(__file__).parent / "docs"
    docs_dir.mkdir(exist_ok=True)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    previous_file = find_previous_digest(docs_dir, today_str)
    html = render_html(digest, errors, previous_file)

    # Write dated file (overwrites if same day)
    dated_path = docs_dir / f"{today_str}.html"
    dated_path.write_text(html)

    # Write redirect as index.html
    index_path = docs_dir / "index.html"
    index_path.write_text(render_redirect(f"{today_str}.html"))

    # Also save the raw JSON for debugging/archiving
    json_path = docs_dir / f"{today_str}.json"
    json_path.write_text(json.dumps(digest, indent=2))

    print(f"Digest written to {dated_path}")


if __name__ == "__main__":
    main()
