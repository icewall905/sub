import requests
import json
import time
import logging
from typing import Dict, Optional, Any

class TranslationService:
    """
    Service class for handling translations using various translation APIs.
    Acts as a facade for multiple translation providers.
    """
    
    def __init__(self, config, logger=None):
        """
        Initialize the translation service with configuration.
        
        Args:
            config: Configuration object (configparser.ConfigParser)
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # Language mapping for reference
        self.language_mapping = {
            "english": "en",
            "danish": "da",
            "spanish": "es",
            "german": "de",
            "french": "fr",
            "italian": "it",
            "portuguese": "pt",
            "dutch": "nl",
            "swedish": "sv",
            "norwegian": "no",
            "finnish": "fi",
            "polish": "pl",
            "russian": "ru",
            "japanese": "ja",
            "chinese": "zh",
            "korean": "ko",
            "arabic": "ar",
            "hindi": "hi",
            "turkish": "tr",
        }
    
    def get_iso_code(self, language_name: str) -> str:
        """Convert a language name to its ISO code."""
        language_name = language_name.lower().strip('"\' ')
        return self.language_mapping.get(language_name, language_name)
    
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate text from source language to target language.
        Uses the configured translation services in order of priority.
        
        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            
        Returns:
            Translated text
        """
        if not text.strip():
            return text
        
        # Get list of enabled translation services in priority order
        service_priority = self.config.get("translation", "service_priority", 
                                          fallback="deepl,openai,google").split(",")
        
        # Try services in order until one succeeds
        for service in service_priority:
            service = service.strip().lower()
            
            try:
                self.logger.info(f"Attempting translation with {service} service")
                
                if service == "deepl" and self.config.getboolean("general", "use_deepl", fallback=False):
                    result = self._translate_with_deepl(text, source_lang, target_lang)
                    if result:
                        return result
                        
                elif service == "openai" and self.config.getboolean("general", "use_openai", fallback=False):
                    result = self._translate_with_openai(text, source_lang, target_lang)
                    if result:
                        return result
                        
                elif service == "ollama" and self.config.getboolean("ollama", "enabled", fallback=False):
                    result = self._translate_with_ollama(text, source_lang, target_lang)
                    if result:
                        return result
                        
                elif service == "google":
                    result = self._translate_with_google(text, source_lang, target_lang)
                    if result:
                        return result
                
            except Exception as e:
                self.logger.error(f"Error using {service} translation service: {str(e)}")
        
        # If all services fail, return original text
        self.logger.warning("All translation services failed, returning original text")
        return text
    
    def _translate_with_deepl(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using DeepL API."""
        if not self.config.has_section("deepl_api"):
            self.logger.warning("DeepL API configuration not found")
            return ""
        
        api_key = self.config.get("deepl_api", "api_key", fallback="")
        if not api_key:
            self.logger.warning("DeepL API key not configured")
            return ""
        
        # Determine API URL based on account type
        is_pro = self.config.getboolean("deepl_api", "use_pro", fallback=False)
        api_url = "https://api.deepl.com/v2/translate" if is_pro else "https://api-free.deepl.com/v2/translate"
        
        # Convert language codes to DeepL format
        source_iso = self.get_iso_code(source_lang).upper()
        target_iso = self.get_iso_code(target_lang).upper()
        
        # Prepare request
        params = {
            "auth_key": api_key,
            "text": text,
            "source_lang": source_iso,
            "target_lang": target_iso,
        }
        
        # Make request
        try:
            self.logger.debug(f"Calling DeepL API: {source_iso} -> {target_iso}")
            response = requests.post(api_url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            if "translations" in result and len(result["translations"]) > 0:
                return result["translations"][0]["text"]
            
            self.logger.warning("DeepL API returned no translations")
            return ""
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"DeepL API request failed: {str(e)}")
            return ""
    
    def _translate_with_openai(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using OpenAI API."""
        if not self.config.has_section("openai_api"):
            self.logger.warning("OpenAI API configuration not found")
            return ""
        
        api_key = self.config.get("openai_api", "api_key", fallback="")
        if not api_key:
            self.logger.warning("OpenAI API key not configured")
            return ""
        
        model = self.config.get("openai_api", "model", fallback="gpt-4")
        api_base_url = self.config.get("openai_api", "api_base_url", 
                                      fallback="https://api.openai.com/v1")
        
        # Get full language names for clearer prompt
        source_full = self._get_language_full_name(source_lang)
        target_full = self._get_language_full_name(target_lang)
        
        # Create prompt for translation
        prompt = (
            f"Translate the following text from {source_full} to {target_full}. "
            f"Maintain the same formatting, tone, and meaning as closely as possible. "
            f"Return ONLY the translated text without explanations or quotation marks.\n\n"
            f"Text to translate: {text}"
        )
        
        # Prepare request
        url = f"{api_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3
        }
        
        # Make request
        try:
            self.logger.debug(f"Calling OpenAI API with model {model}")
            response = requests.post(url, headers=headers, json=data, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"].strip()
            
            self.logger.warning("OpenAI API returned no choices")
            return ""
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"OpenAI API request failed: {str(e)}")
            return ""
    
    def _translate_with_ollama(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using local Ollama service."""
        if not self.config.has_section("ollama"):
            self.logger.warning("Ollama configuration not found")
            return ""
        
        server_url = self.config.get("ollama", "server_url", fallback="http://localhost:11434")
        model = self.config.get("ollama", "model", fallback="")
        
        if not model:
            self.logger.warning("Ollama model not configured")
            return ""
        
        # Get full language names for clearer prompt
        source_full = self._get_language_full_name(source_lang)
        target_full = self._get_language_full_name(target_lang)
        
        # Create prompt for translation
        prompt = (
            f"Translate the following text from {source_full} to {target_full}. "
            f"Maintain the same formatting, tone, and meaning as closely as possible. "
            f"Return ONLY the translated text without explanations or quotation marks.\n\n"
            f"Text to translate: {text}"
        )
        
        # Prepare request
        url = f"{server_url.rstrip('/')}/api/generate"
        data = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.config.getfloat("ollama", "temperature", fallback=0.3)
            }
        }
        
        # Add additional Ollama options if configured
        options = {}
        for option in ["num_gpu", "num_thread", "num_ctx"]:
            if self.config.has_option("ollama", option):
                options[option] = self.config.getint("ollama", option)
        
        for option in ["use_mmap", "use_mlock"]:
            if self.config.has_option("ollama", option):
                options[option] = self.config.getboolean("ollama", option)
        
        if options:
            data["options"].update(options)
        
        # Make request
        try:
            self.logger.debug(f"Calling Ollama API with model {model}")
            response = requests.post(url, json=data, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            if "response" in result:
                # Clean up response - Ollama sometimes adds extra quotes or markdown
                return result["response"].strip(' "\'\n`')
            
            self.logger.warning("Ollama API returned no response")
            return ""
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ollama API request failed: {str(e)}")
            return ""
    
    def _translate_with_google(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate text using Google Translate API (free web API).
        Note: This uses the unofficial API and may be rate limited or blocked.
        """
        # Convert language codes
        source_iso = self.get_iso_code(source_lang)
        target_iso = self.get_iso_code(target_lang)
        
        # Prepare request
        import urllib.parse
        base_url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": source_iso,
            "tl": target_iso,
            "dt": "t",  # return translated text
            "q": text
        }
        
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        
        # Make request
        try:
            self.logger.debug(f"Calling Google Translate API: {source_iso} -> {target_iso}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            # Extract translation from Google's response format
            if result and isinstance(result, list) and len(result) > 0:
                translation = ""
                for sentence_data in result[0]:
                    if sentence_data and isinstance(sentence_data, list) and len(sentence_data) > 0:
                        translation += sentence_data[0]
                
                return translation
            
            self.logger.warning("Google Translate API returned unexpected format")
            return ""
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Google Translate API request failed: {str(e)}")
            return ""
        except (ValueError, KeyError, IndexError) as e:
            self.logger.error(f"Error parsing Google Translate response: {str(e)}")
            return ""
    
    def _get_language_full_name(self, language_code: str) -> str:
        """Convert language code to full name."""
        # Reverse mapping from code to name
        reverse_mapping = {v: k for k, v in self.language_mapping.items()}
        return reverse_mapping.get(language_code.lower(), language_code)