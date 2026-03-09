# Tech Digest

A daily tech & AI news digest, auto-generated and published to GitHub Pages.

Pulls from curated RSS feeds and APIs, uses Claude to deduplicate, filter, and summarize into a finite, phone-friendly daily briefing.

## Setup

1. Clone this repo
2. Add your `ANTHROPIC_API_KEY` as a GitHub Actions secret
3. Enable GitHub Pages (deploy from `gh-pages` branch)
4. The digest runs nightly at 11 PM ET via GitHub Actions

## Local Development

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-key-here
python generate_digest.py
# Output lands in docs/index.html
```

## Configuration

Edit `config.yaml` to add/remove sources or tweak the summarization prompt.
