"""Public source adapters. Parse only each page's project/article, never global image URLs."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urljoin, unquote

from bs4 import BeautifulSoup

WAF_AWARDS = {2025: "https://worldarchitecturefestival.com/WAF2026/en/page/winners-2025"}
SOURCE_INFO = [
    {"id": "archdaily", "catalog": "https://www.archdaily.com/search/projects",
     "capabilities": ["project_read", "project_gallery", "image_in_memory"],
     "search": "Discovery through host web/browser tools; public project cards and galleries can be read here."},
    {"id": "frame", "catalog": "https://frameweb.com/awards",
     "capabilities": ["public_project_read", "project_gallery", "image_in_memory"],
     "search": "Host web/browser discovery; server reads public /project/ cards, not subscription articles."},
    {"id": "architizer", "catalog": "https://winners.architizer.com/2025/",
     "catalog_year_range": [2013, 2026], "verified_editions": [2013, 2018, 2020, 2024, 2025, 2026],
     "capabilities": ["journal_keyword_search", "journal_read", "annual_award_catalog", "award_entry_read", "image_in_memory"],
     "search": "Journal search. Use browse_reference_catalog for award editions; /projects/ can block requests."},
    {"id": "waf", "catalog": WAF_AWARDS[2025], "configured_editions": [2025],
     "capabilities": ["award_catalog_2025", "award_entry_read", "award_board_in_memory"],
     "search": "Keyword filter of the 2025 winners page. Images may be award boards, not project photos."},
]
DISCOVERY = [
    {"id": "officesnapshots", "catalog": "https://officesnapshots.com/photos/",
     "reason": "Direct HTTP returned 403; use the host's web/browser tools. No native adapter."},
    {"id": "dezeen", "catalog": "https://www.dezeen.com/awards/",
     "reason": "Direct HTTP returned protection page; no bypass or native adapter."},
]
IMAGE_HOSTS = {
    "archdaily": {"images.adsttc.com": "/media/images/"},
    "frame": {"d1tm14lrsghf7q.cloudfront.net": "/public/media/"},
    "architizer": {"images.architizer.com": "/media/", "blog.architizer.com": "/wp-content/uploads/"},
    "waf": {"evessio.s3.amazonaws.com": "/customer/b4289942-d924-4d3d-9044-2b4131d4ae91/"},
}


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def identify(url: str, kind: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Expected an allowlisted public HTTPS source URL.")
    # Encoded separators and dot segments have no place in these fixed public routes.
    decoded = unquote(p.path)
    if decoded != p.path or "\\" in decoded or any(x in (".", "..") for x in decoded.split("/")):
        raise ValueError("Encoded or traversing source paths are unsupported.")
    if kind == "image":
        for source, hosts in IMAGE_HOSTS.items():
            if p.hostname in hosts and p.path.startswith(hosts[p.hostname]) and re.search(r"\.(?:jpg|jpeg|png|webp)$", p.path, re.I):
                return source
    elif kind in ("project", "search", "catalog"):
        if p.hostname in {"www.archdaily.com", "archdaily.com"}:
            if re.fullmatch(r"/\d+/[\w-]+/?", p.path):
                return "archdaily"
        if p.hostname == "frameweb.com" and re.fullmatch(r"/project/[\w-]+/?", p.path):
            return "frame"
        if p.hostname == "winners.architizer.com" and re.fullmatch(r"/20\d{2}/(?:Plus/)?", p.path):
            return "architizer"
        if p.hostname in {"architizer.com", "www.architizer.com"} and re.fullmatch(r"/blog/(?:[\w-]+/)*", p.path):
            return "architizer"
        if p.hostname in {"worldarchitecturefestival.com", "www.worldarchitecturefestival.com"} and p.path in {
            "/WAF2026/en/page/winners-2025", "/WAF2026/en/page/winners-brochures"}:
            return "waf"
    raise ValueError("Unsupported source or route. Consult reference_sources for capabilities.")


def safe_url(url: str, kind: str) -> str:
    identify(url, kind)
    return urlsplit(url)._replace(fragment="").geturl()


def discovery(source: str, query: str) -> dict:
    domains = {"archdaily": "archdaily.com", "frame": "frameweb.com/project/",
               "officesnapshots": "officesnapshots.com", "dezeen": "dezeen.com/awards/"}
    return {"source": source, "status": "host_search_required", "results": [],
            "suggested_web_query": f"site:{domains[source]} {query}",
            "note": "Discovery instruction only, not executed search results. Use the host web/browser tool."}


def excerpts(paragraphs: list[str], focus: str) -> tuple[list[str], bool | None]:
    words = [w.casefold() for w in re.findall(r"[\w-]+", focus) if len(w) > 2]
    paragraphs = list(dict.fromkeys(clean(p) for p in paragraphs if clean(p)))
    matching = [p for p in paragraphs if any(w in p.casefold() for w in words)]
    out, remaining = [], 6500
    for p in (matching or paragraphs)[:12]:
        if not remaining:
            break
        out.append(p[:min(1800, remaining)])
        remaining -= len(out[-1])
    return out, bool(matching) if words else None


def image_entry(url: str, source: str, title: str = "", credit: str | None = None, kind: str = "unclassified") -> dict | None:
    try:
        if identify(url, "image") != source:
            return None
    except ValueError:
        return None
    return {"url": url, "alt": clean(title), "credit": credit, "kind": kind, "visual_reviewed": False}


def result(source: str, url: str, title: str, paragraphs: list[str], images: list, focus: str,
           architect=None, credit=None, metadata=None, record_type="project") -> dict:
    gallery, seen = [], set()
    for entry in images:
        if entry and entry["url"] not in seen:
            seen.add(entry["url"])
            gallery.append({"index": len(gallery), **entry})
    text, matched = excerpts(paragraphs, focus)
    return {"status": "ok", "source": source, "url": url, "title": clean(title),
            "record_type": record_type, "architect": architect, "photography_credit": credit,
            "source_metadata": metadata or {}, "source_excerpts": text,
            "focus": focus or None, "focus_match_found": matched,
            "images": gallery, "image_count": len(gallery),
            "evidence_note": "Source statements, not independent verification. Image URLs are not visual review. "
                             "Award edition, publication and completion dates are distinct. Missing facts stay unknown."}


def parse_archdaily(soup: BeautifulSoup, url: str, focus: str) -> dict:
    article, h1 = soup.select_one("article#single-content"), soup.select_one("h1")
    if not article or not h1 or not soup.select_one(".afd-specs"):
        raise ValueError("Expected a public ArchDaily project card, not a subscriber article or search shell.")
    facts = [clean(x.get_text(" ", strip=True)) for x in soup.select(".afd-specs__item")]
    architects = next((v for v in facts if v.startswith("Architects:")), None)
    credits = next((v for v in facts if "Photographs:" in v), None)
    images = [image_entry(img.get("src", ""), "archdaily", img.get("alt", ""), credits)
              for img in soup.select("#gallery-thumbs img[src]")]
    return result("archdaily", url, h1.text, [p.get_text(" ", strip=True) for p in article.select("p")],
                  images, focus, architects, credits, {"specifications": facts,
                  "publication": clean(soup.time.text) if soup.time else None})


def parse_frame(soup: BeautifulSoup, url: str, focus: str) -> dict:
    app = soup.select_one("[data-page]")
    if not app:
        raise ValueError("FRAME public project data missing.")
    payload = json.loads(app["data-page"])
    if not isinstance(payload, dict) or not isinstance(payload.get("props"), dict):
        raise ValueError("FRAME project payload is malformed.")
    content = payload["props"].get("content")
    if not isinstance(content, dict):
        raise ValueError("FRAME selected project is missing.")
    if content.get("type") != "project" or not content.get("title") or content.get("status") != 1:
        raise ValueError("Expected a published FRAME project, not an article or restricted page.")
    if urlsplit(url).path.rstrip("/") != str(content.get("url", "")).rstrip("/"):
        raise ValueError("FRAME project identity mismatch.")
    # Only the selected project's gallery; props.latest, users and globals are not content.
    images = []
    for kind, group in (("unclassified", "images"), ("plan", "floorplan")):
        for item in content.get(group) or []:
            properties = (item.get("data") or {}).get("custom_properties") or {}
            # Use an actual advertised rendition; do not invent CDN resize URLs.
            srcset = (item.get("cover") or {}).get("srcset", "")
            candidates = [p.strip().split()[0] for p in srcset.split(",") if p.strip().startswith("https://")]
            img = image_entry(candidates[0] if candidates else item.get("url", ""), "frame",
                              properties.get("description") or "", properties.get("credits"), kind)
            images.append(img)
    body = BeautifulSoup(content.get("description") or "", "html.parser")
    credit = list(dict.fromkeys(i["credit"] for i in images if i and i["credit"] and i["kind"] != "plan")) or None
    metadata = {k: content.get(k) for k in ("published_at", "completion", "size", "classification", "client")}
    metadata["location"] = (content.get("location") or {}).get("location_formatted_address")
    return result("frame", url, content["title"], [p.text for p in body.select("p")] or [body.text],
                  images, focus, content.get("company"), credit, metadata)


def architizer_entries(soup: BeautifulSoup, url: str) -> list[dict]:
    entries = []
    for card in soup.select(".winner.card[data-id][data-name]"):
        parent = card.find_parent(class_="winner-container")
        category = card.find_previous("h4")
        authors = [a.get_text(" ", strip=True) for a in card.select('.card-body a[href*="/firms/"]')]
        entries.append({"entry_id": card["data-id"], "title": card["data-name"], "url": url,
                        "project_url": urljoin("https://architizer.com", card.get("data-url", "")),
                        "architect": ", ".join(authors) or None,
                        "award_status": parent.get("data-types") if parent else None,
                        "category": clean(category.text) if category else None,
                        "description": clean(card.get("data-description", "")),
                        "image": image_entry(card.get("data-image", ""), "architizer", card["data-name"]),
                        "source": "architizer"})
    if not entries:
        raise ValueError("Architizer award cards not recognized.")
    return entries


def waf_entries(soup: BeautifulSoup, url: str) -> list[dict]:
    entries = []
    for panel in soup.select(".image-panel-content"):
        a = panel.select_one('a[href*="worldbuildingsdirectory.com/entries/"]')
        desc = panel.select_one(".image-panel-description p")
        img = panel.select_one(".panel-image")
        if not a or not desc:
            continue
        text = clean(desc.text)
        name, sep, author = text.rpartition(" by ")
        path = urlsplit(a["href"]).path
        src = re.search(r"url\(['\"]?([^)'\"]+)", img.get("style", "")) if img else None
        entries.append({"entry_id": path.rstrip("/").split("/")[-1] + "::" + clean(img.get("title", "") if img else ""), "title": name if sep else text,
                        "url": url, "project_url": a["href"], "architect": author if sep else None,
                        "award_status": img.get("title") if img else None, "category": None,
                        "description": text, "source": "waf",
                        "image": image_entry(src[1], "waf", img.get("title", ""), kind="award_board") if src else None})
    if not entries:
        raise ValueError("WAF winner panels not recognized.")
    return entries


def parse_award_entry(soup: BeautifulSoup, url: str, source: str, entry_id: str | None, focus: str) -> dict:
    entries = architizer_entries(soup, url) if source == "architizer" else waf_entries(soup, url)
    if not entry_id:
        raise ValueError("Award page contains multiple projects. Supply entry_id from browse_reference_catalog.")
    matching = [e for e in entries if e["entry_id"] == entry_id]
    if len(matching) != 1:
        raise ValueError("Award entry ID missing or ambiguous; browse the catalog again.")
    e = matching[0]
    doc = result(source, url, e["title"], [e["description"]], [e["image"]], focus, e["architect"],
                 metadata={"award_status": e["award_status"], "category": e["category"],
                           "project_url": e["project_url"], "entry_id": entry_id}, record_type="award_entry")
    doc["entry_id"] = entry_id
    return doc


def journal_search(soup: BeautifulSoup, url: str) -> list[dict]:
    entries = []
    for h in soup.select("h2, h3"):
        a = h.select_one('a[href]')
        if a:
            target = urljoin(url, a["href"])
            try:
                if identify(target, "project") == "architizer":
                    entries.append({"title": clean(a.text), "url": target, "source": "architizer",
                                    "record_type": "editorial_article", "match_status": "native_search_candidate_unverified"})
            except ValueError:
                continue
    return list({e["url"]: e for e in entries}.values())


def parse_journal(soup: BeautifulSoup, url: str, focus: str) -> dict:
    h1, body = soup.select_one("h1"), soup.select_one("article.l-article .c-post-content")
    if not h1 or not body:
        raise ValueError("Public Architizer Journal article not recognized.")
    images = [image_entry(i.get("data-src") or i.get("src", ""), "architizer", i.get("alt", "")) for i in body.select("img")]
    return result("architizer", url, h1.text, [p.text for p in body.select("p")], images, focus,
                  record_type="editorial_article")


def catalog_url(source: str, year: int) -> str:
    if source == "architizer" and 2013 <= year <= 2026:
        return f"https://winners.architizer.com/{year}/"
    if source == "waf" and year in WAF_AWARDS:
        return WAF_AWARDS[year]
    raise ValueError("No configured catalog for this source/year. WAF: 2025; Architizer: 2013..2026 (availability varies).")
