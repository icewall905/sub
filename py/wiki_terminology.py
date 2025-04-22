#!/usr/bin/env python3
"""
Wiki Terminology (and Summary) Service
--------------------------------------
Pulls *short lead‑section summaries* plus any glossary‑style terms
from Fandom / MediaWiki wikis to prime an LLM subtitle‑translator.
"""

import argparse, configparser, json, logging, os, re, time, urllib.parse
from collections import OrderedDict

import requests
from bs4 import BeautifulSoup           # pip install beautifulsoup4
import mwparserfromhell as mw           # pip install mwparserfromhell

DDG_LITE = "https://lite.duckduckgo.com/50x.html"   # no‑JS endpoint :contentReference[oaicite:3]{index=3}
# Expanded themed categories for better coverage of show-specific terminology
THEMED_CATEGORIES = ["Mutes", "Packs", "Events", "Locations", "Characters", "Species", "Powers", "Abilities", "Weapons", "Technology", "Factions", "Groups", "Organizations", "Places", "Items"]
HEADERS = {"User-Agent": "SubtitleTranslator/1.2 (https://github.com/you/sub)"}

class WikiTerminologyService:
    def __init__(self, config, logger=None):
        self.cfg = config["wiki_terminology"]
        self.enabled = self.cfg.getboolean("enabled", True)
        self.max_terms = self.cfg.getint("max_terms", 15)
        self.cache_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "cache", "wikis"
        )
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_expiry = self.cfg.getint("cache_expiry_days", 7) * 86400
        self.logger = logger or logging.getLogger(__name__)

        # search endpoints (unified‑search first, then legacy, then DDG)
        self.endpoints = [
            "https://services.fandom.com/unified-search/community-search",  # 
            "https://www.fandom.com/api/v1/Search/List",                    # legacy – 404s on new wikis :contentReference[oaicite:4]{index=4}
            "https://community.fandom.com/api/v1/Search/List",
        ]

        # regex patterns for bullet‑style glossary lines (kept for completeness)
        self.term_rx = re.compile(
            r"^[\*\#]\s*'''?\s*([^':\n]+?)\s*'''?\s*[:\-–]\s*(.+)", re.IGNORECASE
        )

    # ---------- public entry point ---------- #
    def get_terminology(self, media):
        if not self.enabled:
            return None

        title, tmdb_id = media["title"], media.get("id") or media.get("tmdb_id")
        cache_file = os.path.join(self.cache_dir, f"{tmdb_id}_terminology.json")

        # Check force_refresh option first
        force_refresh = self.cfg.getboolean("force_refresh", fallback=False)
        
        # use cache if fresh and not forcing refresh
        if not force_refresh and self._maybe_fresh(cache_file):
            self.logger.info("Using cached wiki summaries")
            return json.load(open(cache_file, encoding="utf-8"))

        base = self._locate_wiki(title,
                                 self.cfg.get("manual_wiki_override", fallback=None))
        
        # If no wiki found, return None
        if not base:
            self.logger.warning(f"No wiki found for {title}")
            return None
        
        # Extract wiki summary even if no specific terms are found
        wiki_summary = self._get_wiki_summary(base)
        
        pages = self._candidate_pages(base)
        self.logger.info("Pages considered: %s", pages[:10])

        glossary = OrderedDict()
        # -------- 1) short summaries via TextExtracts -------- #
        summaries = self._quick_extracts(base, pages[:self.max_terms * 2])
        for page, summ in summaries.items():
            if summ and len(glossary) < self.max_terms:
                glossary[page] = summ[:250]  # hard cap
                
        # -------- 2) bullet‑style terms -------- #
        # Process themed categories first to prioritize show-specific terms
        themed_pages = [p for p in pages if any(cat.lower() in p.lower() for cat in THEMED_CATEGORIES)]
        other_pages = [p for p in pages if p not in themed_pages]
        
        # Process themed pages first
        for page in themed_pages + other_pages:
            if len(glossary) >= self.max_terms:
                break
            glossary.update(self._bullet_terms(base, page))

        # Create and return payload with wiki summary included
        payload = {
            "wiki_url": base,
            "wiki_summary": wiki_summary,
            "terms": [{"term": k, "definition": v} for k, v in list(glossary.items())[:self.max_terms]],
            "last_updated": time.time(),
        }
        
        # Save to cache
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        
        return payload

    def _get_wiki_summary(self, wiki_url):
        """Extract a summary of the wiki itself"""
        try:
            self.logger.info(f"Fetching wiki summary from {wiki_url}")
            response = requests.get(wiki_url, headers=HEADERS, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Try to find the main description/summary
                # First check for wiki description
                desc = soup.select_one('meta[name="description"]')
                if desc and desc.get('content'):
                    return desc.get('content')
                    
                # Try main content area
                main_content = soup.select_one('.page-content')
                if main_content:
                    paragraphs = main_content.select('p')
                    if paragraphs:
                        # Get first 2-3 paragraphs
                        summary = ' '.join([p.text for p in paragraphs[:3]])
                        return summary[:500]  # Limit to 500 chars
                        
                # Try first paragraph as fallback
                first_p = soup.select_one('p')
                if first_p:
                    return first_p.text[:500]
                    
                return "No summary could be extracted from wiki."
            else:
                return f"Could not access wiki ({response.status_code})"
        except Exception as e:
            self.logger.error(f"Error fetching wiki summary: {str(e)}")
            return "Error fetching wiki summary."

    # ---------- helpers ---------- #
    def _maybe_fresh(self, path):
        return os.path.exists(path) and time.time() - os.path.getmtime(path) < self.cache_expiry

    # wiki discovery --------------- #
    def _locate_wiki(self, title, explicit=None):
        if explicit:
            return explicit.rstrip("/")
        # 1) unified‑search JSON
        for ep in self.endpoints:
            try:
                if "unified-search" in ep:
                    r = requests.get(ep, params={"query": title, "lang": "en"},
                                     headers=HEADERS, timeout=10)
                    if r.ok:
                        for res in r.json().get("results", []):
                            return res["url"].split("/wiki")[0]
                else:  # legacy
                    r = requests.get(ep, params={"query": title, "limit": 5},
                                     headers=HEADERS, timeout=10)
                    if r.ok:
                        for itm in r.json().get("items", []):
                            m = re.match(r"https?://([^.]+\.fandom\.com)/", itm["url"])
                            if m:
                                return f"https://{m.group(1)}"
            except requests.RequestException:
                continue
        # 2) DDG lite fallback
        q = f'{title} site:fandom.com "wiki"'
        r = requests.get(DDG_LITE, params={"q": q}, headers=HEADERS, timeout=10)
        for link in re.findall(r'href="(https://[^"]+?\.fandom\.com)(?:/|\?|\")', r.text):
            return link.rstrip("/")
        raise RuntimeError("Could not locate a Fandom wiki")

    # candidate page list ---------- #
    def _candidate_pages(self, base):
        pages = set()
        wiki_name = base.split("//")[1].split(".")[0]
        pages.add(wiki_name.replace("_", " ").title())

        # First prioritize themed (show-specific) categories
        for cat in THEMED_CATEGORIES:
            try:
                data = self._mw(base, action="query", list="categorymembers",
                                cmtitle=f"Category:{cat}", cmlimit="10")
                themed_pages = [p["title"] for p in data["query"]["categorymembers"]]
                # Prioritize these by adding them to the start of our pages list
                pages.update(themed_pages)
                self.logger.debug("Found %d pages in themed category %s", len(themed_pages), cat)
            except Exception:
                pass

        # Then add general glossary categories
        for cat in ["Glossary", "Terminology", "Slang", "Dictionary"]:
            try:
                data = self._mw(base, action="query", list="categorymembers",
                                cmtitle=f"Category:{cat}", cmlimit="5")
                pages.update(p["title"] for p in data["query"]["categorymembers"])
            except Exception:
                pass

        # Enhanced text search with more show-specific terms derived from themed categories
        search_terms = " OR ".join(["glossary", "terminology", "dictionary"] + THEMED_CATEGORIES)
        try:
            data = self._mw(base, action="query", list="search",
                            srsearch=search_terms,
                            srlimit="10")
            pages.update(hit["title"] for hit in data["query"]["search"])
        except Exception:
            pass

        return list(pages)[:30]  # Increased limit to capture more potential pages

    #  TextExtracts summaries ------- #
    def _quick_extracts(self, base, pages):
        titles = "|".join(pages[:20])  # API limit 20 :contentReference[oaicite:5]{index=5}
        try:
            data = self._mw(
                base,
                action="query",
                prop="extracts",
                explaintext=True,
                exintro=True,
                exlimit=len(pages),
                titles=titles,
            )
            extracts = {}
            for page in data["query"]["pages"].values():
                if "missing" in page:
                    continue
                txt = page.get("extract", "").strip()
                if txt:
                    extracts[page["title"]] = re.sub(r"\s+", " ", txt)
            return extracts
        except Exception as e:
            self.logger.debug("Extract API failed: %s", e)
            return {}

    #  bullet‑style term parser ----- #
    def _bullet_terms(self, base, page):
        try:
            data = self._mw(base, action="parse", page=page, prop="wikitext")
            text = data["parse"]["wikitext"]["*"]
            code = mw.parse(text)
            terms = OrderedDict()
            for line in code.strip_code().splitlines():
                m = self.term_rx.match(line)
                if m and len(terms) < self.max_terms:
                    term, defi = m.groups()
                    terms[term.strip()] = defi.strip()[:150]
            return terms
        except Exception:
            return {}

    # thin wrapper around API ------- #
    def _mw(self, base, **params):
        params.setdefault("format", "json")
        url = f"{base}/api.php"
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()

# ---------------- CLI driver ---------------- #
def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", help="Show or movie title")
    ap.add_argument("--wiki", help="Override wiki URL (e.g. https://kipo.fandom.com)")
    ap.add_argument("--max-terms", type=int, help="Number of summaries/terms to return")
    args = ap.parse_args()

    cfg = configparser.ConfigParser()
    cfg["wiki_terminology"] = {
        "enabled": "true",
        "cache_expiry_days": "7",
        "max_terms": str(args.max_terms or 10),
    }
    if args.wiki:
        cfg["wiki_terminology"]["manual_wiki_override"] = args.wiki

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    service = WikiTerminologyService(cfg, logging.getLogger("wiki"))

    media = {"title": args.title, "type": "tv", "id": "cli"}
    out = service.get_terminology(media)
    if out and out["terms"]:
        print(f"✓ {len(out['terms'])} items from {out['wiki_url']}")
        print(json.dumps(out["terms"], indent=2, ensure_ascii=False))
    else:
        print("✗ No data found")

if __name__ == "__main__":
    cli()
