#!/usr/bin/env python3
"""Audit URL patterns for the university domain crawler.

Phase 1 (--no-browser): Check for URL key mismatches between config.py and pattern JSON files.
Phase 2 (default): Visit each listing page with Playwright and test pattern matching.

Usage:
    python scripts/audit_patterns.py --no-browser          # Fast: key mismatch check only
    python scripts/audit_patterns.py                        # Full: visit pages and test patterns
    python scripts/audit_patterns.py --domains URL1 URL2    # Test specific domains only
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config import UNIVERSITY_NEWS_URLS, MANDATORY_DOMAINS
from src.crawlers.university_domain_crawler import (
    _get_patterns,
    _find_matching_links,
)


def phase1_key_mismatches():
    """Report listing URLs in config that have no matching key in pattern files."""
    patterns_map = _get_patterns()
    all_config_urls = UNIVERSITY_NEWS_URLS + MANDATORY_DOMAINS

    mismatches = []
    matched = []
    for url in all_config_urls:
        if url in patterns_map:
            matched.append(url)
        else:
            mismatches.append(url)

    orphan_patterns = [k for k in patterns_map if k not in all_config_urls]

    print("\n=== Phase 1: URL Key Mismatch Report ===\n")
    print(f"Config URLs: {len(all_config_urls)}")
    print(f"Pattern keys: {len(patterns_map)}")
    print(f"Matched: {len(matched)}")
    print(f"Config URLs WITHOUT pattern: {len(mismatches)}")
    print(f"Pattern keys NOT in config: {len(orphan_patterns)}")

    if mismatches:
        print("\n--- Config URLs missing from pattern files ---")
        for url in mismatches:
            print(f"  MISSING: {url}")

    if orphan_patterns:
        print("\n--- Pattern keys not referenced in config ---")
        for url in orphan_patterns:
            print(f"  ORPHAN: {url}")

    return mismatches, orphan_patterns


def phase2_test_patterns(domain_urls: list[str] | None = None):
    """Visit listing pages and test pattern matching."""
    from bs4 import BeautifulSoup
    from playwright.sync_api import sync_playwright
    from src.utils.scraping_helpers import dismiss_cookies

    patterns_map = _get_patterns()
    all_config_urls = domain_urls or (UNIVERSITY_NEWS_URLS + MANDATORY_DOMAINS)

    results = []

    print("\n=== Phase 2: Pattern Match Testing ===\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        for domain_url in all_config_urls:
            patterns = patterns_map.get(domain_url)
            if not patterns:
                results.append({
                    "domain": domain_url,
                    "status": "no_pattern",
                    "total_links": 0,
                    "matched": 0,
                    "sample_matches": [],
                })
                print(f"  SKIP (no pattern): {domain_url}")
                continue

            page = browser.new_page()
            try:
                try:
                    page.goto(domain_url, timeout=30000, wait_until="networkidle")
                except Exception:
                    page.goto(domain_url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                dismiss_cookies(page)
                html = page.content()
                soup = BeautifulSoup(html, "lxml")

                # Count total links
                all_links = [a for a in soup.find_all("a", href=True)]
                total = len(all_links)

                # Find matches
                discovered = _find_matching_links(soup, domain_url, patterns)
                n_matched = len(discovered)

                # Classify result
                if n_matched == 0:
                    status = "no_match"
                elif n_matched > 50:
                    status = "too_broad"
                else:
                    status = "ok"

                sample = [d["url"] for d in discovered[:5]]
                results.append({
                    "domain": domain_url,
                    "status": status,
                    "total_links": total,
                    "matched": n_matched,
                    "sample_matches": sample,
                })

                icon = {"ok": "OK", "no_match": "FAIL", "too_broad": "BROAD"}[status]
                print(f"  [{icon:>5}] {domain_url} — {n_matched}/{total} links matched")

            except Exception as e:
                results.append({
                    "domain": domain_url,
                    "status": "error",
                    "total_links": 0,
                    "matched": 0,
                    "error": str(e),
                    "sample_matches": [],
                })
                print(f"  [ERROR] {domain_url} — {e}")
            finally:
                page.close()

        browser.close()

    # Summary
    print("\n--- Summary ---")
    for status_label in ["ok", "no_match", "too_broad", "no_pattern", "error"]:
        count = sum(1 for r in results if r["status"] == status_label)
        if count:
            print(f"  {status_label}: {count}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Audit university crawler URL patterns")
    parser.add_argument("--no-browser", action="store_true", help="Phase 1 only (no Playwright)")
    parser.add_argument("--domains", nargs="+", help="Test specific domain URLs only")
    parser.add_argument("--output", type=str, help="Save JSON report to file")
    args = parser.parse_args()

    mismatches, orphans = phase1_key_mismatches()

    if not args.no_browser:
        results = phase2_test_patterns(args.domains)
    else:
        results = []

    if args.output:
        report = {
            "mismatches": mismatches,
            "orphan_patterns": orphans,
            "pattern_tests": results,
        }
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nReport saved to {args.output}")


if __name__ == "__main__":
    main()
