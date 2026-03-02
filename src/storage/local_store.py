"""Local filesystem storage for pipeline artifacts."""

import json
from datetime import date
from pathlib import Path

BASE = Path("/tmp/news_digest")


def get_run_date() -> str:
    return date.today().isoformat()


def raw_dir(run_date: str) -> Path:
    p = BASE / "raw" / run_date
    p.mkdir(parents=True, exist_ok=True)
    return p


def processed_dir(run_date: str) -> Path:
    p = BASE / "processed" / run_date
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = BASE / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_raw(articles: list[dict], source_prefix: str, run_date: str) -> Path:
    path = raw_dir(run_date) / f"{source_prefix}.json"
    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_raw_all(run_date: str) -> list[dict]:
    articles = []
    for f in raw_dir(run_date).glob("*.json"):
        articles.extend(json.loads(f.read_text(encoding="utf-8")))
    return articles


def save_processed(articles: list[dict], filename: str, run_date: str) -> Path:
    path = processed_dir(run_date) / filename
    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_processed(filename: str, run_date: str) -> list[dict]:
    path = processed_dir(run_date) / filename
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def digest_path(run_date: str) -> Path:
    return processed_dir(run_date) / f"digest_{run_date}.html"
