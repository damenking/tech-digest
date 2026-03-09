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


def fetch_rss_feeds(sources: list[dict]) -> list[dict]:
    """Fetch and parse RSS feeds, returning normalized article dicts."""
    articles = []
    for source in sources:
        try:
            feed = feedparser.parse(source["url"])
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
            print(f"Warning: Failed to fetch {source['name']}: {e}")
    return articles


def fetch_hackernews(top_n: int = 30) -> list[dict]:
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
        print(f"Warning: Failed to fetch Hacker News: {e}")
    return articles


def build_claude_prompt(articles: list[dict], config: dict) -> str:
    """Build the prompt for Claude to generate the digest."""
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    topics = ", ".join(config["topics"])

    articles_json = json.dumps(articles, indent=2, default=str)

    return f"""You are a tech news digest curator. Today is {today}.

Here are raw articles pulled from various tech news sources today:

{articles_json}

Your job is to produce a daily digest with these rules:

1. **Deduplicate**: If the same story appears from multiple sources, merge them into one entry. Prefer the source with the best summary.
2. **Filter**: Remove clickbait, fluff, listicles, and sponsored content. Keep only genuinely newsworthy items.
3. **Select**: Pick the {config['max_stories']} most important/interesting stories.
4. **Categorize**: Assign each story to one of these topics: {topics}
5. **Summarize**: Write a 2-3 sentence summary for each story.
6. **Editorial voice**: {config['editorial_voice']}

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
          "summary": "Your 2-3 sentence editorial summary.",
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
        max_tokens=4096,
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


def render_html(digest: dict) -> str:
    """Render the digest as a mobile-friendly HTML page."""
    template_path = Path(__file__).parent / "template.html"
    with open(template_path) as f:
        template = Template(f.read())

    return template.render(
        date=digest["date"],
        intro=digest["intro"],
        sections=digest["sections"],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def main():
    print("Loading config...")
    config = load_config()

    print("Fetching RSS feeds...")
    articles = fetch_rss_feeds(config["sources"]["rss"])

    if config["sources"]["hackernews"]["enabled"]:
        print("Fetching Hacker News...")
        hn_articles = fetch_hackernews(config["sources"]["hackernews"]["top_n"])
        articles.extend(hn_articles)

    print(f"Collected {len(articles)} articles from all sources")

    print("Generating digest with Claude...")
    prompt = build_claude_prompt(articles, config)
    digest = generate_with_claude(prompt, config)

    print("Rendering HTML...")
    html = render_html(digest)

    # Write output
    docs_dir = Path(__file__).parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    output_path = docs_dir / "index.html"
    output_path.write_text(html)

    # Also save the raw JSON for debugging/archiving
    json_path = docs_dir / "digest.json"
    json_path.write_text(json.dumps(digest, indent=2))

    print(f"Digest written to {output_path}")


if __name__ == "__main__":
    main()
