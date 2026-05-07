import asyncio, json
from collections import Counter

async def main():
    from opensearchpy import AsyncOpenSearch
    import os
    url = os.getenv("OPENSEARCH_URL", "https://81.17.98.185:9200")
    user = os.getenv("OPENSEARCH_USER", "kiber_app")
    pwd = os.getenv("OPENSEARCH_PASSWORD", "")

    client = AsyncOpenSearch(
        hosts=[url], http_auth=(user, pwd),
        use_ssl=True, verify_certs=False, ssl_show_warn=False
    )

    total = await client.count(index="news_articles")
    t = total["count"]
    print(f"Total articles: {t}")

    with_any = await client.count(index="news_articles", body={
        "query": {"bool": {"should": [
            {"exists": {"field": "entities.cve"}},
            {"exists": {"field": "entities.product"}},
            {"exists": {"field": "entities.vendor"}},
            {"exists": {"field": "entities.actor"}},
            {"exists": {"field": "entities.malware"}}
        ], "minimum_should_match": 1}}
    })
    w = with_any["count"]
    print(f"Articles with entity fields present: {w} ({100*w//t if t else 0}%)")

    sample = await client.search(index="news_articles", body={
        "size": 500, "_source": ["entities", "source_name"],
        "sort": [{"published_at": {"order": "desc"}}]
    })
    hits = sample["hits"]["hits"]
    non_empty = sum(1 for h in hits if any(v for v in h["_source"].get("entities", {}).values()))
    n = len(hits)
    print(f"Of last {n} articles, {non_empty} have non-empty entity values ({100*non_empty//n if n else 0}%)")

    types = Counter()
    for h in hits:
        for k, v in h["_source"].get("entities", {}).items():
            if v:
                types[k] += 1
    print(f"Entity type coverage (last {n}): {dict(types)}")

    sources = Counter()
    for h in hits:
        if any(v for v in h["_source"].get("entities", {}).values()):
            sources[h["_source"].get("source_name", "unknown")] += 1
    print(f"\nTop sources with entities: {dict(sources.most_common(10))}")

    await client.close()

asyncio.run(main())
