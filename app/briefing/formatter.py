"""Format the WhatsApp brief message string."""
from datetime import date


def format_brief(clusters: list[dict], brief_date: date) -> str:
    """Return the full WhatsApp message string."""
    n = len(clusters)
    # Cross-platform no-leading-zero day: works on Linux and Windows
    date_str = brief_date.strftime("%B %d, %Y").lstrip("0").replace(" 0", " ")

    story_word = "story" if n == 1 else "stories"
    lines = [
        f"🔐 *Kiber Daily Brief* — {date_str}",
        "",
        f"{n} {story_word} trending today:",
        "",
        "━━━━━━━━━━",
    ]

    for i, cluster in enumerate(clusters, start=1):
        lines.append("")
        lines.append(f"{i}. *{cluster['label']}*")
        lines.append(cluster["summary_text"])

        meta = []
        cve_ids = cluster.get("cve_ids") or []
        if cve_ids:
            meta.append("📌 " + ", ".join(cve_ids[:3]))
        cvss = cluster.get("max_cvss")
        if cvss and cvss >= 7.0:
            meta.append(f"🔴 CVSS {cvss:.1f}")
        if cluster.get("cisa_kev"):
            meta.append("⚠️ CISA KEV")
        if meta:
            lines.append(" | ".join(meta))

    lines += [
        "",
        "━━━━━━━━━━",
        "🌐 news.avild.com",
    ]
    return "\n".join(lines)
