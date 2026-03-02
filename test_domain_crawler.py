from src.crawlers.domain_crawler import crawl_domain


from config import RSS_FEEDS, MANDATORY_DOMAINS, ARTICLES_PER_DOMAIN, NEWSAPI_QUERIES, UNIVERSITY_NEWS_URLS, EMAIL_RECIPIENTS

DAYS_BACK = 4


for i, domain_url in enumerate(UNIVERSITY_NEWS_URLS):
    print(f"Crawling domain {domain_url}...")
    articles = crawl_domain(domain_url, max_articles=ARTICLES_PER_DOMAIN, days_back=DAYS_BACK)
    print(f"Domain {domain_url}: {len(articles)} articles")

    if i > 25:
        break
    
    print([article["title"] for article in articles])