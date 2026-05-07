"""Measure article body coverage and size distribution — server-side aggregations only."""
import asyncio, os
from collections import Counter

async def main():
    from opensearchpy import AsyncOpenSearch
    url = os.getenv("OPENSEARCH_URL")
    user = os.getenv("OPENSEARCH_USER")
    pwd = os.getenv("OPENSEARCH_PASSWORD")
    client = AsyncOpenSearch(
        hosts=[url], http_auth=(user, pwd),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
        timeout=60,
    )

    total = (await client.count(index="news_articles"))["count"]
    print(f"Total articles in news_articles: {total}\n")

    # body_quality distribution (terms agg)
    bq = await client.search(index="news_articles", body={
        "size": 0,
        "aggs": {
            "body_quality": {"terms": {"field": "body_quality", "size": 30}},
            "missing_bq": {"missing": {"field": "body_quality"}},
            "body_source": {"terms": {"field": "body_source", "size": 30}},
            "missing_bs": {"missing": {"field": "body_source"}},
            "is_teaser_true": {"filter": {"term": {"is_teaser": True}}},
        }
    })
    a = bq["aggregations"]
    print("body_quality:")
    for b in a["body_quality"]["buckets"]:
        print(f"  {b['key']}: {b['doc_count']} ({100*b['doc_count']//total}%)")
    print(f"  (no value): {a['missing_bq']['doc_count']} ({100*a['missing_bq']['doc_count']//total}%)")

    print("\nbody_source:")
    for b in a["body_source"]["buckets"]:
        print(f"  {b['key']}: {b['doc_count']} ({100*b['doc_count']//total}%)")
    print(f"  (no value): {a['missing_bs']['doc_count']} ({100*a['missing_bs']['doc_count']//total}%)")

    print(f"\nis_teaser=true: {a['is_teaser_true']['doc_count']} ({100*a['is_teaser_true']['doc_count']//total}%)")

    # Sample: content_html lengths via _source on a small batch
    SAMPLE = 300
    sample = await client.search(index="news_articles", body={
        "size": SAMPLE,
        "_source": ["source_name", "content_html"],
        "sort": [{"published_at": {"order": "desc"}}]
    })
    hits = sample["hits"]["hits"]
    n = len(hits)
    sizes = []
    by_src_sizes: dict[str, list] = {}
    no_html = 0
    for h in hits:
        src = h["_source"]
        sname = src.get("source_name", "?")
        ch = src.get("content_html") or ""
        L = len(ch)
        if not ch:
            no_html += 1
        sizes.append(L)
        by_src_sizes.setdefault(sname, []).append(L)
    sizes.sort()
    print(f"\nSampled {n} most-recent articles for content_html length:")
    print(f"  No content_html at all: {no_html} ({100*no_html//n}%)")
    if n:
        for p in (10, 25, 50, 75, 90, 95):
            idx = min(int(p * n / 100), n - 1)
            print(f"  p{p}: {sizes[idx]} chars")
        for thresh in (500, 1500, 3000, 5000):
            ct = sum(1 for s in sizes if s >= thresh)
            print(f"  >={thresh} chars: {ct} ({100*ct//n}%)")

    # Per-source: total + body_source breakdown
    print("\n\nPer-source body_source breakdown (top 25 sources):")
    by_src = await client.search(index="news_articles", body={
        "size": 0,
        "aggs": {
            "by_source": {
                "terms": {"field": "source_name.keyword", "size": 25},
                "aggs": {
                    "body_sources": {"terms": {"field": "body_source", "size": 10}},
                    "teasers": {"filter": {"term": {"is_teaser": True}}},
                }
            }
        }
    })
    for sb in by_src["aggregations"]["by_source"]["buckets"]:
        sname = sb["key"]
        n = sb["doc_count"]
        bsrcs = ", ".join(f"{b['key']}={b['doc_count']}" for b in sb["body_sources"]["buckets"])
        teasers = sb["teasers"]["doc_count"]
        print(f"  {sname[:35]:35} | n={n:5} | teaser={teasers:4} | body_sources: {bsrcs}")

    await client.close()

asyncio.run(main())
