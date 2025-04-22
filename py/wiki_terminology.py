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
        self.max_terms = config.getint("wiki_terminology", "max_terms", fallback=10)
        
        # How long to cache wiki results (in seconds)
        self.cache_expiry = self.cache_expiry_days * 24 * 60 * 60
        
        self.headers = {
            "User-Agent": "SubtitleTranslator/1.1 (https://github.com/hnyg/sub)"
        }
        
        # New Fandom search endpoints with fallbacks
        self.fandom_search_endpoints = [
            "https://www.fandom.com/api/v1/Search/List",        # new (2023-)
            "https://community.fandom.com/api/v1/Search/List",  # legacy (deprecated)
        ]
        
        self.category_names = ["Glossary", "Terminology", "Slang", "Dictionary", "Lexicon"]
        
        # Regular expression to match terminology entries in wiki pages
        self.bold_rx = re.compile(
            r"^[\*\#]\s*'''+\s*([^':\n]+?)\s*'''+\s*[:\-–]\s*(.+)", re.IGNORECASE
        )
        
        self.logger.info("Wiki terminology service initialized")
    
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
            
            # Get candidate pages
            pages = self.candidate_pages(base)
            self.logger.info(f"Found {len(pages)} candidate pages")
            
            # Extract terminology
            full_glossary = OrderedDict()
            for page in pages:
                try:
                    page_terms = self.extract_terms(base, page)
                    self.logger.debug(f"Extracted {len(page_terms)} terms from page '{page}'")
                    for term, definition in page_terms.items():
                        full_glossary.setdefault(term, definition)
                except Exception as e:
                    self.logger.debug(f"Error extracting from page '{page}': {e}")
            
            if not full_glossary:
                self.logger.warning(f"No terminology found for {title}")
                return self._save_empty_cache(cache_file)
            
            # Successfully found terminology, prepare result
            self.logger.info(f"Found {len(full_glossary)} terms for {title}")
            
            terminology = {
                "wiki_url": base,
                "terms": [{"term": term, "definition": definition} for term, definition in full_glossary.items()],
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
                r = requests.get(base, params={"query": title, "limit": 8},
                                headers=self.headers, timeout=10)
                if r.status_code != 200:
                    self.logger.debug(f"Endpoint {base} returned status code {r.status_code}")
                    continue
                    
                for item in r.json().get("items", []):
                    m = re.match(r"https?://([^.]+\.fandom\.com)/", item["url"])
                    if m:
                        return f"https://{m.group(1)}"
            except requests.RequestException as e:
                self.logger.debug(f"Error searching with Fandom API at {base}: {e}")
        
        return None
    
    def _search_duckduckgo(self, title):
        """Fall back to searching with DuckDuckGo."""
        try:
            self.logger.debug(f"Falling back to DuckDuckGo search for {title}")
            q = f'{title} site:fandom.com "wiki"'
            
            response = requests.get(
                "https://duckduckgo.com/html/",
                params={"q": q}, 
                headers=self.headers, 
                timeout=10
            )
            
            if response.status_code != 200:
                self.logger.debug(f"DuckDuckGo returned status code {response.status_code}")
                return None
                
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            
            for a in soup.select("a.result__a"):
                href = a.get("href")
                m = re.match(r"https?://([^.]+\.fandom\.com)/", href)
                if m:
                    return f"https://{m.group(1)}"
                    
        except Exception as e:
            self.logger.debug(f"Error searching with DuckDuckGo: {e}")
            
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
        # 1) Categories like Terminology, Glossary, Slang…
        for cat in self.category_names:
            try:
                data = self.mediawiki_api(
                    base,
                    action="query",
                    list="categorymembers",
                    cmtitle=f"Category:{cat}",
                    cmlimit="500",
                )
                pages.update(p["title"] for p in data.get("query", {}).get("categorymembers", []))
            except Exception as e:
                self.logger.debug(f"Error getting category members for {cat}: {e}")
                continue
        # 2) Full‑text search hits
        search_terms = "|".join(self.category_names)
        try:
            data = self.mediawiki_api(
                base,
                action="query",
                list="search",
                srsearch=search_terms,
                srlimit="20",
            )
            pages.update(hit["title"] for hit in data.get("query", {}).get("search", []))
        except Exception as e:
            self.logger.debug(f"Error searching for terminology pages: {e}")
        
        return pages
    
    def extract_terms(self, base, title):
        """Extract terminology from a wiki page."""
        data = self.mediawiki_api(base, action="parse", page=title, prop="wikitext")
        wikitext = data["parse"]["wikitext"]["*"]
        glossary = OrderedDict()
        for line in wikitext.splitlines():
            m = self.bold_rx.match(line)
            if m:
                term = re.sub(r"\[\[|\]\]", "", m.group(1)).strip()
                definition = re.sub(r"\[\[|\]\]", "", m.group(2)).strip()
                glossary.setdefault(term, definition)
        return glossary
        
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
