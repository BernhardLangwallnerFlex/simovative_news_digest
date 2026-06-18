"""Live smoke test: confirm pattern-based discovery works for the new
public-administration crawl domains. Requires network + Playwright.

Run: .venv/bin/python scripts/test_verwaltung_crawl.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.crawlers.university_domain_crawler import crawl_university_domain

DOMAINS = [
    "https://www.dstgb.de/publikationen/pressemitteilungen/",
    "https://www.dstgb.de/aktuelles/",
    "https://www.kommune21.de/",
    "https://www.move-online.de/",
]


def main():
    failures = []
    for domain in DOMAINS:
        print(f"\n=== {domain} ===")
        articles = crawl_university_domain(domain, max_articles=10, days_back=0)
        methods = {a.get("discovery_method") for a in articles}
        print(f"  {len(articles)} articles | methods={methods}")
        for a in articles[:5]:
            print(f"   - {a['title'][:70]} | {a['url']}")
        if not articles:
            failures.append(domain)
        elif "pattern" not in methods:
            print(f"  WARNING: no pattern-based discovery (fell back to {methods})")

    if failures:
        print(f"\nFAILED — zero articles for: {failures}")
        sys.exit(1)
    print("\nAll domains yielded articles.")


if __name__ == "__main__":
    main()
