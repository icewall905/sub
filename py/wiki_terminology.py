#!/usr/bin/env python3
"""
Wiki Terminology Service

Automatically extracts show-specific terminology from fan wikis to improve translations.
Provides context about special terms, slang, and concepts unique to TV shows and movies.
"""

import requests
import re
import json
import os
import time
import logging
import urllib.parse
import argparse
from collections import OrderedDict
from bs4 import BeautifulSoup  # pip install beautifulsoup4
import mwparserfromhell as mw

DDG_LITE = "https://duckduckgo.com/lite/"
THEMED_CATEGORIES = ["Mutes", "Packs", "Events", "Locations"]

class WikiTerminologyService:
    """Service to extract show-specific terminology from fan wikis."""
    
    def __init__(self, config, logger=None):
        """Initialize the wiki terminology service."""
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # Check if feature is enabled
        self.enabled = config.getboolean("wiki_terminology", "enabled", fallback=True)
        if not self.enabled:
            self.logger.info("Wiki terminology feature is disabled")
            return
            
        # Create cache directory if it doesn't exist
        self.cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache', 'wikis')
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Get configuration settings
        self.cache_expiry_days = config.getint("wiki_terminology", "cache_expiry_days", fallback=7)
        self.max_terms = config.getint("wiki_terminology", "max_terms", fallback=15)  # Default to 15 terms
        
        # How long to cache wiki results (in seconds)
        self.cache_expiry = self.cache_expiry_days * 24 * 60 * 60
        
        self.headers = {
            "User-Agent": "SubtitleTranslator/1.1 (https://github.com/hnyg/sub)"
        }
        
        # Updated Fandom search endpoints with fallbacks
        self.fandom_search_endpoints = [
            # new 2024‑present endpoint
            "https://services.fandom.com/unified-search/community-search",
            # fallback to the legacy hosts
            "https://www.fandom.com/api/v1/Search/List",
            "https://community.fandom.com/api/v1/Search/List",
        ]
        
        # Basic category names to search for
        self.category_names = [
            "Glossary", "Terminology", "Slang", "Dictionary"
        ]
        
        # Simple regular expressions to match terminology entries in wiki pages
        self.term_patterns = [
            # Bold term with colon/dash definitions
            re.compile(r"^[\*\#]\s*'''+\s*([^':\n]+?)\s*'''+\s*[:\-–]\s*(.+)", re.IGNORECASE),
            # Terms in definition lists
            re.compile(r";\s*([^:\n]+?)\s*:\s*(.+)", re.IGNORECASE),
            # Terms in infobox templates
            re.compile(r"\|\s*([^=\n]+?)\s*=\s*(.+)", re.IGNORECASE),
        ]
        
        self.logger.info(f"Wiki terminology service initialized (max terms: {self.max_terms})")
    
    def get_terminology(self, media_info):
        """Get terminology for a specific show or movie."""
        if not self.enabled:
            return None
            
        if not media_info:
            return None
            
        title = media_info.get('title', '')
        media_type = media_info.get('type', 'tv')
        tmdb_id = media_info.get('id') or media_info.get('tmdb_id')
        
        if not title or not tmdb_id:
            self.logger.warning("Cannot get terminology: missing title or ID")
            return None
        
        # Check cache first
        cache_file = os.path.join(self.cache_dir, f"{media_type}_{tmdb_id}_terminology.json")
        
        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            
            # Use cached data if it's not too old
            if file_age < self.cache_expiry:
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        self.logger.info(f"Loading cached terminology for {title} from {cache_file}")
                        return json.load(f)
                except Exception as e:
                    self.logger.warning(f"Error reading cache file: {e}")
        
        # No valid cache, fetch new data
        self.logger.info(f"Fetching terminology for {title} (ID: {tmdb_id})")
        
        try:
            # Check if there's a manual wiki override in config
            wiki_override = self.config.get("wiki_terminology", "manual_wiki_override", fallback=None)
            
            # Find the wiki
            base = self.find_wiki_base(title, wiki_override)
            self.logger.info(f"Found wiki base: {base}")
            
            # Get candidate pages from the wiki
            pages = self.candidate_pages(base)
            self.logger.info(f"Found {len(pages)} candidate pages")
            
            # Extract terminology from wiki pages
            full_glossary = OrderedDict()
            
            # Look for a good summary first
            summary = None
            wiki_name = base.split("//")[1].split(".")[0]
            
            # Try to get summary from the main series page
            for page in pages:
                # Prioritize pages that are likely to be the main series page
                if page == wiki_name or page == wiki_name.title() or page.replace("_", " ") == wiki_name.replace("_", " "):
                    summary_data = self.extract_summary(base, page)
                    if summary_data and "Summary" in summary_data:
                        summary = summary_data["Summary"]
                        self.logger.info(f"Found summary from page '{page}'")
                        # Add this as our first term
                        full_glossary["Series Overview"] = summary
                        break
            
            # If we don't have a summary yet, try other pages
            if not summary and pages:
                summary_data = self.extract_summary(base, pages[0])
                if summary_data and "Summary" in summary_data:
                    summary = summary_data["Summary"]
                    self.logger.info(f"Found summary from page '{pages[0]}'")
                    # Add this as our first term
                    full_glossary["Series Overview"] = summary
            
            # Extract actual terminology terms (limited to avoid flooding)
            terms_count = 0
            
            # First try to find a dedicated terminology page
            for page in pages:
                # Check if page name suggests it's a terminology/glossary page
                if any(term.lower() in page.lower() for term in ["glossary", "terminology", "dictionary", "terms"]):
                    page_terms = self.extract_terms(base, page)
                    self.logger.debug(f"Extracted {len(page_terms)} terms from dedicated page '{page}'")
                    
                    # Add terms up to the limit
                    for term, definition in page_terms.items():
                        if term not in full_glossary and terms_count < self.max_terms - 1:
                            full_glossary[term] = definition
                            terms_count += 1
                            self.logger.debug(f"Added wiki term {terms_count}/{self.max_terms-1}: {term}")
            
            # If we still need more terms, try other pages
            for page in pages:
                # Stop if we've already found enough terms
                if terms_count >= self.max_terms - 1:  # -1 to account for the summary
                    break
                    
                # Skip pages we've already processed
                if any(term.lower() in page.lower() for term in ["glossary", "terminology", "dictionary", "terms"]):
                    continue
                    
                try:
                    page_terms = self.extract_terms(base, page)
                    self.logger.debug(f"Extracted {len(page_terms)} terms from page '{page}'")
                    
                    # Add terms up to the limit
                    for term, definition in page_terms.items():
                        if term not in full_glossary and terms_count < self.max_terms - 1:
                            full_glossary[term] = definition
                            terms_count += 1
                            self.logger.debug(f"Added wiki term {terms_count}/{self.max_terms-1}: {term}")
                except Exception as e:
                    self.logger.debug(f"Error extracting from page '{page}': {e}")
            
            if not full_glossary:
                self.logger.warning(f"No terminology found for {title}")
                return self._save_empty_cache(cache_file)
            
            # Successfully found terminology, prepare result
            self.logger.info(f"Found {len(full_glossary)} terms for {title}")
            
            # Use at most self.max_terms from the glossary
            term_list = list(full_glossary.items())[:self.max_terms]
            
            terminology = {
                "wiki_url": base,
                "terms": [{"term": term, "definition": definition} for term, definition in term_list],
                "last_updated": time.time()
            }
            
            # Cache the result
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(terminology, f, ensure_ascii=False, indent=2)
            
            return terminology
            
        except Exception as e:
            self.logger.error(f"Error fetching terminology: {e}")
            return self._save_empty_cache(cache_file)
    
    def _save_empty_cache(self, cache_file):
        """Save an empty result to cache to avoid repeated lookups."""
        empty_result = {
            "wiki_url": None,
            "terms": [],
            "last_updated": time.time()
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(empty_result, f)
        return empty_result
    
    def _search_fandom_api(self, title):
        """Search for the wiki using the Fandom API endpoints."""
        for base in self.fandom_search_endpoints:
            try:
                self.logger.debug(f"Trying Fandom API endpoint: {base}")
                
                if "unified-search" in base:
                    # Use the new unified search endpoint format
                    self.logger.debug(f"Using unified search API with query: {title}")
                    r = requests.get(
                        base,
                        params={"query": title, "lang": "en"},
                        headers=self.headers,
                        timeout=10
                    )
                    
                    if r.ok:
                        data = r.json()
                        self.logger.debug(f"Unified search response: {data.keys()}")
                        for item in data.get("results", []):
                            url = item.get("url")
                            if url:
                                self.logger.debug(f"Found wiki URL in unified search: {url}")
                                return url.rstrip("/")
                    else:
                        self.logger.debug(f"Unified search endpoint returned status code {r.status_code}")
                else:
                    # Legacy API format
                    r = requests.get(
                        base,
                        params={"query": title, "limit": 8},
                        headers=self.headers,
                        timeout=10
                    )
                    
                    if r.status_code != 200:
                        self.logger.debug(f"Endpoint {base} returned status code {r.status_code}")
                        continue
                        
                    for item in r.json().get("items", []):
                        m = re.match(r"https?://([^.]+\.fandom\.com)/", item["url"])
                        if m:
                            return f"https://{m.group(1)}"
            except requests.RequestException as e:
                self.logger.debug(f"Error searching with Fandom API at {base}: {e}")
            except Exception as e:
                self.logger.debug(f"Unexpected error with {base}: {e}")
        
        return None
    
    def safe_get(self, *args, **kwargs):
        for attempt in range(3):
            try:
                return requests.get(*args, **kwargs)
            except requests.RequestException:
                time.sleep(0.1 * 2**attempt)
        raise RuntimeError("Failed after 3 attempts")

    def _search_duckduckgo(self, title):
        q = f'{title} site:fandom.com "wiki"'
        for attempt in range(3):
            try:
                r = self.safe_get(DDG_LITE, params={'q': q}, headers=self.headers, timeout=10)
                if r.ok:
                    for link in re.findall(r'href="(https://[^"\s]+\.fandom\.com/[^"\s]+)"', r.text):
                        return link.split("/wiki")[0]
            except Exception:
                time.sleep(0.1 * 2**attempt)
        return None
    
    def find_wiki_base(self, title, explicit=None):
        """Return the base {subdomain}.fandom.com for the given show/movie title."""
        if explicit:
            self.logger.info(f"Using manually specified wiki URL: {explicit}")
            return explicit.rstrip("/")
            
        wiki = self._search_fandom_api(title) or self._search_duckduckgo(title)
        
        if wiki:
            return wiki
            
        raise RuntimeError(f"Could not locate a Fandom wiki for '{title}' - consider adding a manual_wiki_override in config.ini")
    
    def mediawiki_api(self, base, **params):
        """Make an API call to the MediaWiki API."""
        params.setdefault("format", "json")
        url = f"{base}/api.php"
        r = requests.get(url, params=params, headers=self.headers, timeout=20)
        r.raise_for_status()
        return r.json()
    
    def candidate_pages(self, base):
        """Find potential pages that might contain terminology."""
        pages = set()
        
        # Try to get the main series page first
        try:
            # Get the wiki name from the base URL (e.g., "kipo" from "kipo.fandom.com")
            wiki_name = base.split("//")[1].split(".")[0]
            self.logger.debug(f"Looking for series page with name: {wiki_name}")
            
            # Try to find the series page by the wiki name
            try:
                data = self.mediawiki_api(base, action="parse", page=wiki_name, prop="wikitext")
                pages.add(wiki_name)
                self.logger.debug(f"Found series page with name: {wiki_name}")
            except Exception:
                # Try with common variations
                variations = [
                    wiki_name.title(),  # capitalize first letter
                    wiki_name.replace("-", " ").title(),  # replace hyphens with spaces
                    wiki_name.replace("_", " ").title(),  # replace underscores with spaces
                ]
                
                for variation in variations:
                    try:
                        data = self.mediawiki_api(base, action="parse", page=variation, prop="wikitext")
                        pages.add(variation)
                        self.logger.debug(f"Found series page with variation: {variation}")
                        break
                    except Exception:
                        continue
        except Exception as e:
            self.logger.debug(f"Error getting series page: {e}")
        
        # Add glossary/terminology and themed categories
        for cat in self.category_names + THEMED_CATEGORIES:
            try:
                data = self.mediawiki_api(
                    base,
                    action="query",
                    list="categorymembers",
                    cmtitle=f"Category:{cat}",
                    cmlimit="3",  # Reduced to just 3 for each category
                )
                pages.update(p["title"] for p in data.get("query", {}).get("categorymembers", []))
            except Exception as e:
                self.logger.debug(f"Error getting category members for {cat}: {e}")
                continue
                
        # Generic search for common terms (limited results)
        search_terms = "glossary OR terminology OR dictionary"
        try:
            data = self.mediawiki_api(
                base,
                action="query",
                list="search",
                srsearch=search_terms,
                srlimit="5",  # Reduced to just 5
            )
            pages.update(hit["title"] for hit in data.get("query", {}).get("search", []))
        except Exception as e:
            self.logger.debug(f"Error searching for terminology pages: {e}")
            
        # Try to find the main page as fallback
        try:
            main_page_candidates = ["Main_Page", "Wiki"]
            for title in main_page_candidates:
                try:
                    data = self.mediawiki_api(base, action="parse", page=title, prop="wikitext")
                    pages.add(title)
                    break
                except Exception:
                    continue
        except Exception as e:
            self.logger.debug(f"Error getting main page: {e}")
        
        # Convert to list and limit to a reasonable number
        return list(pages)[:10]  # Limit to 10 pages maximum
    
    def extract_terms(self, base, title):
        """Extract terminology from a wiki page."""
        try:
            data = self.mediawiki_api(base, action="parse", page=title, prop="wikitext")
            wikitext = data["parse"]["wikitext"]["*"]
            code = mw.parse(wikitext)
            glossary = OrderedDict()
            
            # Extract from templates (infoboxes, etc.)
            for template in code.filter_templates():
                for param in template.params:
                    key = str(param.name).strip().lower()
                    val = str(param.value).strip()
                    if key in ("name", "term", "title") and val:
                        for def_key in ("desc", "definition", "about", "description"):
                            if template.has(def_key):
                                glossary[val] = str(template.get(def_key).value).strip()
            
            # Extract from bullet/definition lists
            for node in code.filter_text():
                for line in str(node).splitlines():
                    if ":" in line and len(line) < 100:
                        term, definition = line.split(":", 1)
                        if len(definition.strip()) < 100:
                            glossary[term.strip()] = definition.strip()
                    if len(glossary) >= self.max_terms:
                        return glossary
            
            return glossary
        except Exception as e:
            self.logger.debug(f"Error extracting terms from '{title}': {e}")
            return OrderedDict()
    
    def extract_summary(self, base, title):
        """Extract a concise summary from a wiki page."""
        try:
            data = self.mediawiki_api(base, action="parse", page=title, prop="text", formatversion=2)
            html = data["parse"]["text"]
            intro = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            
            # If intro is too long, get first 2-3 sentences only
            if len(intro) > 400:
                sentences = re.split(r'(?<=[.!?])\s+', intro)
                intro = " ".join(sentences[:3]) if sentences else intro[:400]
            
            if intro:
                return {"Summary": intro}
                
            return {}
            
        except Exception as e:
            self.logger.debug(f"Error extracting summary from '{title}': {e}")
            return {}
        
# Command-line interface for direct script usage
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Extract terminology from fan wikis")
    ap.add_argument("title", help='Show or movie title, e.g. "Kipo and the Age of the Wonderbeasts"')
    ap.add_argument("--wiki", help="Override wiki base URL, e.g. https://kipo.fandom.com")
    args = ap.parse_args()
    
    # Simple configuration for CLI mode
    import configparser
    config = configparser.ConfigParser()
    config['wiki_terminology'] = {
        'enabled': 'true',
        'cache_expiry_days': '7',
        'max_terms': '10'
    }
    
    if args.wiki:
        config['wiki_terminology']['manual_wiki_override'] = args.wiki
    
    # Setup basic logging
    logging.basicConfig(level=logging.INFO, 
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('wiki_terminology')
    
    # Create service and run
    service = WikiTerminologyService(config, logger)
    
    # Create a simple media_info structure for the CLI mode
    media_info = {
        'title': args.title,
        'type': 'tv',
        'id': 'cli_mode',
        'tmdb_id': 'cli_mode'
    }
    
    # Get and print terminology
    terminology = service.get_terminology(media_info)
    
    if terminology and terminology.get('terms'):
        print(f"✓ Found {len(terminology['terms'])} terms from {terminology['wiki_url']}")
        
        # Write glossary files
        glossary = OrderedDict()
        for term_data in terminology['terms']:
            glossary[term_data['term']] = term_data['definition']
            
        pathlib.Path("glossary.json").write_text(
            json.dumps(glossary, indent=2, ensure_ascii=False)
        )
        
        with open("glossary_prompt.txt", "w", encoding="utf-8") as fp:
            fp.write("# Glossary of in‑universe terms\n")
            for term, definition in glossary.items():
                fp.write(f"- **{term}**: {definition}\n")
                
        print(f"✓ Saved to glossary.json and glossary_prompt.txt")
    else:
        print("✗ No terminology found for this title")
