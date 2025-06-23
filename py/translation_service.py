import requests
import json
import time
import logging
import re
import os
import difflib
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
        
        # Initialize wiki terminology service if enabled
        self.wiki_terminology = None
        try:
            if config.has_section("wiki_terminology") and config.getboolean("wiki_terminology", "enabled", fallback=False):
                from py.wiki_terminology import WikiTerminologyService
                self.wiki_terminology = WikiTerminologyService(config, logger)
                self.logger.info("Wiki terminology service initialized")
            else:
                self.logger.info("Wiki terminology service disabled or not configured")
        except Exception as e:
            self.logger.warning(f"Failed to initialize wiki terminology service: {str(e)}")
        
        # Initialize special meanings from file
        self.special_meanings = self.load_special_meanings()
        
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

        # Add TMDB API key
        self.tmdb_api_key = config.get("tmdb", "api_key", fallback=None)
        self.use_tmdb = config.getboolean("tmdb", "enabled", fallback=False)
        self.tmdb_language = config.get("tmdb", "language", fallback="en-US")
        
        # Pre/Post-processing feature flags
        self.freeze_speaker_labels = config.getboolean('preprocessing', 'freeze_speaker_labels', fallback=False)
        self.enforce_special_tokens = config.getboolean('translation', 'enforce_special_tokens', fallback=False)
        self.glossary_post_replace = config.getboolean('translation', 'glossary_post_replace', fallback=False)
        
        self.logger.info(f"Feature flags – freeze_speaker_labels: {self.freeze_speaker_labels}, "
                         f"enforce_special_tokens: {self.enforce_special_tokens}, "
                         f"glossary_post_replace: {self.glossary_post_replace}")
    
    def get_iso_code(self, language_name: str) -> str:
        """Convert a language name to its ISO code."""
        language_name = language_name.lower().strip('"\' ')
        return self.language_mapping.get(language_name, language_name)
    
    def translate(self, text: str, source_lang: str, target_lang: str, context=None, media_info=None, special_meanings=None) -> Dict[str, Any]:
        """
        Translate text from source language to target language.
        Uses the configured translation services in order of priority.
        
        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            context: Optional context text (surrounding subtitles)
            media_info: Optional media information from TMDB
            special_meanings: Optional list of special word meanings defined by the user
            
        Returns:
            Dictionary containing:
            - 'final_text': The final translated text (str)
            - 'collected_translations': Dictionary of translations from each service (Dict[str, str])
            - 'first_pass_text': The result after the first pass (e.g., from Ollama final) (Optional[str])
        """
        if not text.strip():
            return {"final_text": text, "collected_translations": {}, "first_pass_text": None}
        
        # ----------------------------------------------------
        # Pre-processing (speaker label freeze, store original)
        # ----------------------------------------------------
        original_text = text  # keep full original for validation later
        prefix = ""
        if self.freeze_speaker_labels:
            prefix_match = re.match(r"^([A-Za-z0-9_,'\- ]+:\s*)(.*)$", text)
            if prefix_match:
                prefix = prefix_match.group(1)
                text = prefix_match.group(2)  # only translate payload
        # ----------------------------------------------------
        
        # Default return structure
        result_details = {
            "final_text": text, # Default to original if all fails
            "collected_translations": {},
            "first_pass_text": None
        }

        # If special_meanings weren't explicitly provided, use the ones loaded from file
        if special_meanings is None:
            special_meanings = self.special_meanings
            if special_meanings:
                self.logger.info(f"Using {len(special_meanings)} special meanings from file")

        # Check if Ollama is enabled and should be used as final translator
        ollama_enabled = self.config.getboolean("ollama", "enabled", fallback=False)
        use_ollama_as_final = self.config.getboolean("ollama", "use_as_final_translator", fallback=True) if ollama_enabled else False
        
        # Get service priority from config
        service_priority = []
        # Get configured priority if available
        if self.config.has_option("translation", "service_priority"):
            priority_string = self.config.get("translation", "service_priority")
            # Split by comma and filter empty strings
            all_services = [s.strip() for s in priority_string.split(",") if s.strip()]
            
            # Only include enabled services in the priority list
            for service in all_services:
                if ((service == "deepl" and self.config.getboolean("general", "use_deepl", fallback=False)) or
                    (service == "openai" and self.config.getboolean("openai", "enabled", fallback=False)) or
                    (service == "ollama" and self.config.getboolean("ollama", "enabled", fallback=True)) or
                    (service == "google" and self.config.getboolean("general", "use_google", fallback=True)) or
                    (service == "libretranslate" and self.config.getboolean("general", "use_libretranslate", fallback=False)) or
                    (service == "mymemory" and self.config.getboolean("general", "use_mymemory", fallback=False))):
                    service_priority.append(service)
        
        # Default priority if not specified or empty
        if not service_priority:
            default_priority = "google,ollama"
            self.logger.warning(f"No valid service priority configured, using default: {default_priority}")
            service_priority = [s.strip() for s in default_priority.split(",")]
            
        self.logger.info(f"Using translation service priority: {service_priority}")
        
        # --- Ollama as Final Translator Logic ---
        if use_ollama_as_final:
            self.logger.info("Ollama will be used as final translator. Collecting translations from all services.")
            collected_translations = {}
            
            # Collect translations from online services
            for service in service_priority:
                if service == "ollama": continue # Skip Ollama itself in collection phase
                
                try:
                    translation = None
                    if service == "deepl" and self.config.getboolean("deepl", "enabled", fallback=False):
                        self.logger.info(f"Collecting translation from {service} service")
                        translation = self._translate_with_deepl(text, source_lang, target_lang)
                    elif service == "openai" and self.config.getboolean("openai", "enabled", fallback=False):
                        self.logger.info(f"Collecting translation from {service} service")
                        translation = self._translate_with_openai(text, source_lang, target_lang)
                    elif service == "google" and self.config.getboolean("general", "use_google", fallback=True):
                        self.logger.info(f"Collecting translation from {service} service")
                        translation = self._translate_with_google(text, source_lang, target_lang)
                    
                    if translation:
                        collected_translations[service.capitalize()] = translation # Use capitalized name for display
                        
                except Exception as e:
                    self.logger.error(f"Error collecting translation from {service}: {str(e)}")

            result_details["collected_translations"] = collected_translations

            # If we collected any translations, use Ollama to make final decision
            if collected_translations:
                self.logger.info(f"Collected {len(collected_translations)} translations. Using Ollama to make final decision.")
                start_time = time.time()
                ollama_final_result = self._translate_with_ollama_as_final(text, source_lang, target_lang, 
                                                                          collected_translations,
                                                                          context_before=context, 
                                                                          context_after=context, 
                                                                          media_info=media_info,
                                                                          special_meanings=special_meanings)
                end_time = time.time()
                
                if ollama_final_result:
                    self.logger.info(f"Ollama successfully provided the final translation in {end_time - start_time:.2f} seconds")
                    
                    # Log if DeepL translation was preserved or modified
                    if "Deepl" in collected_translations:
                        deepl_translation = collected_translations["Deepl"]
                        if deepl_translation == ollama_final_result:
                            self.logger.info("✓ Ollama preserved the DeepL translation")
                        else:
                            similarity_ratio = difflib.SequenceMatcher(None, deepl_translation, ollama_final_result).ratio()
                            self.logger.info(f"⚠ Ollama modified the DeepL translation (similarity: {similarity_ratio:.2f})")
                            self.logger.info(f"  DeepL: '{deepl_translation}'")
                            self.logger.info(f"  Final: '{ollama_final_result}'")
                            
                            # If in debug mode, log more details about the changes
                            if self.config.getboolean('general', 'debug_mode', fallback=False):
                                diff = list(difflib.ndiff(deepl_translation, ollama_final_result))
                                self.logger.debug(f"  Diff: {''.join(diff)}")
                            
                    # Build result_details and apply post-processing before returning
                    result_details["final_text"] = ollama_final_result
                    result_details["first_pass_text"] = ollama_final_result
                    result_details = self._apply_postprocessing(original_text, prefix, result_details)
                    return result_details
                else:
                     self.logger.warning("Ollama final translation failed. Falling back to priority list.")
                     # Fall through to the standard priority logic below
            else:
                self.logger.warning("No translations collected from online services. Falling back to regular translation flow.")
                # Fall through to the standard priority logic below

        # --- Standard Priority Logic (Fallback or if Ollama not final) ---
        self.logger.info("Attempting translation using service priority list.")
        for service in service_priority:
            if ((service == "deepl" and self.config.getboolean("general", "use_deepl", fallback=False)) or
                (service == "openai" and self.config.getboolean("openai", "enabled", fallback=False)) or
                (service == "ollama" and ollama_enabled) or
                (service == "google" and self.config.getboolean("general", "use_google", fallback=True)) or
                (service == "libretranslate" and self.config.getboolean("general", "use_libretranslate", fallback=False)) or
                (service == "mymemory" and self.config.getboolean("general", "use_mymemory", fallback=False))):
                self.logger.info(f"Attempting translation with {service} service")
            else:
                self.logger.debug(f"Skipping disabled service: {service}")
                continue

            try:
                translation = None
                
                if service == "deepl" and self.config.getboolean("deepl", "enabled", fallback=False):
                    translation = self._translate_with_deepl(text, source_lang, target_lang)
                elif service == "openai" and self.config.getboolean("openai", "enabled", fallback=False):
                    translation = self._translate_with_openai(text, source_lang, target_lang)
                elif service == "ollama" and ollama_enabled:
                     # If Ollama is used here, it's the primary translation, not the final decision maker
                    translation = self._translate_with_ollama(text, source_lang, target_lang, context=context, media_info=media_info)
                elif service == "google" and self.config.getboolean("general", "use_google", fallback=True):
                    translation = self._translate_with_google(text, source_lang, target_lang)

                if translation:
                    self.logger.info(f"Successfully translated using {service}.")
                    # Store the first successful translation
                    result_details["final_text"] = translation
                    result_details["first_pass_text"] = translation # This is the first successful result
                    # Add this successful translation to collected_translations if not already there
                    if service.capitalize() not in result_details["collected_translations"]:
                         result_details["collected_translations"][service.capitalize()] = translation
                    result_details = self._apply_postprocessing(original_text, prefix, result_details)
                    return result_details # Return on first success

            except Exception as e:
                self.logger.error(f"Error using {service} translation service: {str(e)}")

        # If all services fail
        self.logger.warning("All translation services failed, returning original text")
        result_details = self._apply_postprocessing(original_text, prefix, result_details)
        return result_details # Return default structure with original text

    def _translate_with_deepl(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using DeepL API."""
        if not self.config.has_section("deepl"):
            self.logger.warning("DeepL API configuration not found")
            return ""
        
        api_key = self.config.get("deepl", "api_key", fallback="")
        if not api_key:
            self.logger.warning("DeepL API key not configured")
            return ""
        
        # Get API URL from config
        api_url = self.config.get("deepl", "api_url", fallback="https://api-free.deepl.com/v2/translate")
        
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
        if not self.config.has_section("openai"):
            self.logger.warning("OpenAI API configuration not found")
            return ""
        
        api_key = self.config.get("openai", "api_key", fallback="")
        if not api_key:
            self.logger.warning("OpenAI API key not configured")
            return ""
        
        model = self.config.get("openai", "model", fallback="gpt-3.5-turbo")
        api_base_url = self.config.get("openai", "api_base_url", 
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
        
        temperature = self.config.getfloat("general", "temperature", fallback=0.3)
        
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature
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
    
    def _translate_with_ollama(self, text: str, source_lang: str, target_lang: str, context=None, media_info=None, special_meanings=None) -> str:
        """Translate text using local Ollama service or LM Studio."""
        # Check if LM Studio is enabled
        lmstudio_enabled = self.config.has_section("lmstudio") and self.config.getboolean("lmstudio", "enabled", fallback=False)
        
        if lmstudio_enabled:
            self.logger.info("Using LM Studio for translation")
            return self._translate_with_lmstudio(text, source_lang, target_lang, context, media_info, special_meanings)
        else:
            # Check if Ollama is enabled
            if not self.config.has_section("ollama"):
                self.logger.warning("Neither Ollama nor LM Studio configuration found")
                return ""
                
            ollama_enabled = self.config.getboolean("ollama", "enabled", fallback=True)
            if not ollama_enabled:
                self.logger.warning("Both Ollama and LM Studio are disabled")
                return ""
                
            self.logger.info("Using Ollama for translation")
            return self._translate_with_ollama_original(text, source_lang, target_lang, context, media_info, special_meanings)
            
    def _translate_with_lmstudio(self, text: str, source_lang: str, target_lang: str, context=None, media_info=None, special_meanings=None) -> str:
        """Translate text using LM Studio's OpenAI-compatible API."""
        server_url = self.config.get("lmstudio", "server_url", fallback="http://localhost:1234")
        model = self.config.get("lmstudio", "model", fallback="")
        
        if not model:
            self.logger.warning("LM Studio model not configured")
            return ""
        
        # Get full language names for clearer prompt
        source_full = self._get_language_full_name(source_lang)
        target_full = self._get_language_full_name(target_lang)
        
        # Create system message with instructions
        system_message = f"You are an expert translator from {source_full} to {target_full}. "
        
        # Add media info if available
        if media_info:
            system_message += f"These subtitles are for: {media_info['title']}. "
            system_message += f"Plot summary: {media_info['overview']}. "
            system_message += f"Genre: {media_info['genres']}. "
            system_message += f"Main cast: {media_info['cast']}. "
        
        # Add wiki terminology to system message if available
        try:
            if self.wiki_terminology and media_info:
                self.logger.info(f"Attempting to get wiki terminology for: {media_info.get('title', 'Unknown title')}")
                terminology = self.wiki_terminology.get_terminology(media_info)
                
                if terminology and terminology.get('terms'):
                    terms = terminology['terms']
                    max_terms = self.config.getint("wiki_terminology", "max_terms", fallback=10)
                    
                    if terms:
                        system_message += "IMPORTANT SHOW-SPECIFIC TERMINOLOGY: "
                        for term in terms[:max_terms]:
                            system_message += f"'{term['term']}' means '{term['definition']}'. "
                        
                        self.logger.info(f"Added {min(len(terms), max_terms)} wiki terminology entries to LM Studio system message")
        except Exception as e:
            self.logger.error(f"Error adding wiki terminology to LM Studio prompt: {str(e)}", exc_info=True)
        
        # Add user-defined special meanings if available
        if special_meanings and len(special_meanings) > 0:
            try:
                system_message += "USER-DEFINED SPECIAL MEANINGS: "
                for meaning in special_meanings:
                    if 'word' in meaning and 'meaning' in meaning:
                        system_message += f"'{meaning['word']}' means '{meaning['meaning']}'. "
                
                self.logger.info(f"Added {len(special_meanings)} user-defined special meanings to LM Studio system message")
            except Exception as e:
                self.logger.error(f"Error adding user-defined special meanings to LM Studio prompt: {str(e)}")
        
        # Create user message with text to translate and context
        user_message = f"Translate this text from {source_full} to {target_full}: {text}"
        if context:
            user_message += f"\n\nContext from surrounding subtitles:\n{context}"
        
        # Prepare request payload in OpenAI Chat Completions format
        url = f"{server_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json"
        }
        
        temperature = self.config.getfloat("lmstudio", "temperature", fallback=0.7)
        
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            "temperature": temperature,
            "max_tokens": self.config.getint("lmstudio", "context_length", fallback=4096),
            "stream": False
        }
        
        # Make request with retries
        max_retries = self.config.getint("translation", "max_retries", fallback=3)
        retry_delay = self.config.getint("translation", "base_delay", fallback=2)
        
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Calling LM Studio API with model {model} (attempt {attempt+1}/{max_retries})")
                
                # Increase timeout for large or complex translations (300 seconds = 5 minutes)
                timeout = 300
                response = requests.post(url, json=data, headers=headers, timeout=timeout)
                
                # Log response details for debugging
                self.logger.debug(f"LM Studio response status: {response.status_code}")
                
                response.raise_for_status()
                result = response.json()
                
                # Extract translation from the response
                if "choices" in result and len(result["choices"]) > 0:
                    translated_text = result["choices"][0]["message"]["content"].strip()
                    
                    self.logger.debug(f"Received LM Studio translation response (len={len(translated_text)})")
                    
                    if translated_text:
                        # Clean up response - remove extra quotes or markdown
                        translated_text = translated_text.strip(' "\'\n`')
                        
                        # Remove potential prefixes that the model might add
                        prefixes_to_remove = [
                            "Translation:", 
                            "Translated text:", 
                            "Here's the translation:"
                        ]
                        for prefix in prefixes_to_remove:
                            if translated_text.startswith(prefix):
                                translated_text = translated_text[len(prefix):].strip()
                        
                        self.logger.debug(f"LM Studio translation successful: {translated_text[:50]}...")
                        return translated_text
                
                self.logger.warning(f"LM Studio API returned no translatable content in attempt {attempt+1}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
                
            except requests.exceptions.Timeout:
                self.logger.warning(f"LM Studio API request timed out after {timeout} seconds on attempt {attempt+1}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
            except requests.exceptions.RequestException as e:
                self.logger.error(f"LM Studio API request failed on attempt {attempt+1}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
            except json.JSONDecodeError as e:
                self.logger.error(f"Error parsing LM Studio response on attempt {attempt+1}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
        
        self.logger.error("All LM Studio translation attempts failed")
        return ""
        
    def _translate_with_ollama_original(self, text: str, source_lang: str, target_lang: str, context=None, media_info=None, special_meanings=None) -> str:
        """Original method to translate text using local Ollama service."""
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
        
        # Create prompt for translation with clear instructions
        if media_info:
            prompt = (
                f"You are an expert translator from {source_full} to {target_full}.\n"
                f"These subtitles are for: {media_info['title']}\n"
                f"Plot summary: {media_info['overview']}\n"
                f"Genre: {media_info['genres']}\n"
                f"Main cast: {media_info['cast']}\n"
            )
            
            # Get and add wiki terminology if available
            try:
                if self.wiki_terminology:
                    self.logger.info(f"Attempting to get wiki terminology for: {media_info.get('title', 'Unknown title')}")
                    terminology = self.wiki_terminology.get_terminology(media_info)
                    
                    if not terminology:
                        self.logger.warning("Wiki terminology returned None - feature may be disabled or wiki not found")
                    elif not terminology.get('terms'):
                        self.logger.warning(f"Wiki terminology found but no terms were extracted. Wiki URL: {terminology.get('wiki_url', 'Unknown')}")
                    else:
                        terms = terminology['terms']
                        max_terms = self.config.getint("wiki_terminology", "max_terms", fallback=10)
                        
                        if terms:
                            self.logger.info(f"Found {len(terms)} wiki terminology entries from {terminology.get('wiki_url', 'Unknown')}")
                            prompt += f"\nIMPORTANT SHOW-SPECIFIC TERMINOLOGY:\n"
                            prompt += f"The following terms have special meanings in this show and must be translated appropriately:\n"
                            # Add up to max_terms terms
                            for term in terms[:max_terms]:
                                prompt += f"- {term['term']}: {term['definition']}\n"
                            
                            self.logger.info(f"Added {min(len(terms), max_terms)} wiki terminology entries to Ollama translation prompt")
                        else:
                            self.logger.warning("Wiki terminology returned empty terms list")
                else:
                    self.logger.debug("Wiki terminology service not initialized, skipping terminology lookup")
            except Exception as e:
                self.logger.error(f"Error adding wiki terminology to Ollama prompt: {str(e)}", exc_info=True)
            
            # Add user-defined special meanings if available
            if special_meanings and len(special_meanings) > 0:
                try:
                    prompt += f"\nUSER-DEFINED SPECIAL MEANINGS:\n"
                    prompt += f"The following terms have special meanings defined by the user and must be translated appropriately:\n"
                    
                    for meaning in special_meanings:
                        if 'word' in meaning and 'meaning' in meaning:
                            prompt += f"- {meaning['word']}: {meaning['meaning']}\n"
                    
                    self.logger.info(f"Added {len(special_meanings)} user-defined special meanings to Ollama translation prompt")
                except Exception as e:
                    self.logger.error(f"Error adding user-defined special meanings to Ollama prompt: {str(e)}")
            
            prompt += (
                f"\nConsider this context from surrounding subtitles to make sure you understand the translation correctly:\n\n"
                f"CONTEXT:\n{context or 'No context available'}\n\n"
                f"Translate this text: {text}\n\n"
                f"Maintain the same formatting, tone, and meaning. Return ONLY the translated text."
            )
        else:
            prompt = (
                f"You are an expert translator from {source_full} to {target_full}.\n"
                f"Maintain the same formatting, tone, and meaning as closely as possible.\n"
                f"Return ONLY the translated text without explanations, quotation marks, or additional commentary.\n\n"
                f"Text to translate: {text}"
            )

        # If context is available, add it:
        if context and not media_info:
            prompt = (
                f"You are an expert translator from {source_full} to {target_full}.\n"
                f"Consider this context from surrounding subtitles to make sure you understand the translation correctly:\n\n"
                f"CONTEXT:\n{context}\n\n"
                f"Translate this text: {text}\n\n"
                f"Maintain the same formatting, tone, and meaning. Return ONLY the translated text."
            )
        
        # --- Revert to reading endpoint from config, fallback to /api/generate --- 
        endpoint = self.config.get("ollama", "endpoint", fallback="/api/generate") 
        url = f"{server_url.rstrip('/')}/{endpoint.lstrip('/')}"
        # --- End endpoint change ---
        
        temperature = self.config.getfloat("general", "temperature", fallback=0.3)
        
        # --- Use /api/generate payload structure --- 
        # Combine system-like instructions with the main prompt
        system_instructions = "You are a professional translator. Translate the text accurately and return only the translation."
        full_prompt = f"{system_instructions}\n\n{prompt}"
        
        data = {
            "model": model,
            "prompt": full_prompt, # Use 'prompt' key
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        # --- End /api/generate payload ---
        
        # Add additional Ollama options if configured
        options = {}
        for option_name in ["num_gpu", "num_thread", "num_ctx"]:
            if self.config.has_option("ollama", option_name):
                # Extra-strict check - only proceed if option exists AND has a non-empty value
                raw_value = self.config.get("ollama", option_name, fallback=None)
                if raw_value is not None and str(raw_value).strip() != "" and not str(raw_value).strip().startswith('#'):
                    try:
                        options[option_name] = int(raw_value)
                        self.logger.debug(f"Adding {option_name}={raw_value} to Ollama request")
                    except ValueError:
                        self.logger.warning(f"Invalid value for ollama.{option_name}, skipping")
                else:
                    self.logger.debug(f"Not adding {option_name} - empty or commented out value: '{raw_value}'")
            else:
                self.logger.debug(f"Option {option_name} not found in config")
                
        for option_name in ["use_mmap", "use_mlock"]:
            if self.config.has_option("ollama", option_name):
                value = self.config.get("ollama", option_name, fallback=None)
                if value is not None and str(value).strip() != "":
                    try:
                        options[option_name] = self.config.getboolean("ollama", option_name)
                        self.logger.debug(f"Adding {option_name}={value} to Ollama request")
                    except ValueError:
                        self.logger.warning(f"Invalid value for ollama.{option_name}, skipping")
        if options:
            data["options"].update(options)
            self.logger.debug(f"Sending Ollama options: {json.dumps(options)}")
        
        # Make request with retries
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Calling Ollama API with model {model} at URL {url} (attempt {attempt+1}/{max_retries})")
                self.logger.debug(f"Request data: {json.dumps(data)}")
                
                # Increase timeout for large or complex translations (300 seconds = 5 minutes)
                timeout = 300
                self.logger.debug(f"Setting Ollama request timeout to {timeout} seconds")
                response = requests.post(url, json=data, timeout=timeout)
                
                # Log response details for debugging
                self.logger.debug(f"Ollama response status: {response.status_code}")
                self.logger.debug(f"Ollama response content: {response.text[:500]}...")
                
                response.raise_for_status()
                result = response.json()
                
                # --- Parse /api/generate response structure --- 
                translated_text = ""
                if "response" in result:
                    translated_text = result["response"].strip()
                    self.logger.debug(f"Received Ollama translation response (len={len(translated_text)})")
                    
                    # Apply think tags filter to remove thinking content
                    translated_text = self.remove_think_tags(translated_text)
                # --- End /api/generate response parsing ---
                
                if translated_text:
                    # Clean up response - Ollama sometimes adds extra quotes or markdown
                    translated_text = translated_text.strip(' "\'\n`')
                    
                    # Remove potential prefixes that the model might add
                    prefixes_to_remove = [
                        "Translation:", 
                        "Translated text:", 
                        "Here's the translation:",
                        "Final translation:"
                    ]
                    for prefix in prefixes_to_remove:
                        if translated_text.lower().startswith(prefix.lower()):
                            translated_text = translated_text[len(prefix):].strip()
                    
                    self.logger.debug(f"Ollama translation successful: {translated_text[:50]}...")
                    return translated_text
                else:
                    self.logger.warning(f"Ollama API returned no translatable content in attempt {attempt+1}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return ""
                
            except requests.exceptions.Timeout:
                self.logger.warning(f"Ollama API request timed out after {timeout} seconds on attempt {attempt+1}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Ollama API request failed on attempt {attempt+1}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
            except json.JSONDecodeError as e:
                self.logger.error(f"Error parsing Ollama response on attempt {attempt+1}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return ""
        
        self.logger.error("All Ollama translation attempts failed")
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

    def _translate_with_ollama_as_final(self, text: str, source_lang: str, target_lang: str, translations: dict, context_before=None, context_after=None, media_info=None, special_meanings=None) -> Optional[str]:
        try:
            # Get conservativeness level from config
            conservativeness = self.config.getint("translation", "translation_conservativeness", fallback=3)
            
            # Log the conservativeness level being used
            conservativeness_labels = {
                1: "Very Conservative",
                2: "Conservative", 
                3: "Balanced",
                4: "Context-Aware",
                5: "Aggressive"
            }
            self.logger.info(f"Using translation conservativeness level: {conservativeness} ({conservativeness_labels.get(conservativeness, 'Unknown')})")
            
            # Improved prompt with clearer instructions and structure
            prompt = f"""You are a subtitle translation expert. Your task is to translate ONLY the line marked as "TEXT TO TRANSLATE" below.

IMPORTANT INSTRUCTIONS:
- Translate ONLY the text marked "TEXT TO TRANSLATE" from {self._get_language_full_name(source_lang)} to {self._get_language_full_name(target_lang)}
- Do NOT translate any of the context lines - they are for understanding the scene only
- Return ONLY your final translation, without quotes, explanations, or notes
- Maintain formatting (especially HTML tags if present)
- When choosing between translations from different services, ALWAYS prioritize professional services:
  1. DeepL translations should be used unchanged in 99% of cases (treat as gold standard)
  2. Only modify DeepL translations when you have definitive contextual information that DeepL could not access
  3. Be extremely conservative - when in doubt, keep the professional translation
  4. Your role is to be a careful reviewer, not an aggressive editor

THINKING PROCESS:
Before providing your final translation, carefully consider:
1. What is the literal meaning of each word and phrase?
2. What is the intended meaning in this specific context?
3. Are there any cultural nuances or idioms that need special attention?
4. Does the context provide information that might affect the translation?
5. Are there any character names, proper nouns, or show-specific terms?
6. Which translation service provides the most accurate result for this specific case?

Take your time to think through each aspect before deciding on the final translation.
"""

            # Add media info from TMDB if available
            if media_info:
                prompt += f"""
MOVIE/SHOW INFORMATION:
Title: {media_info.get('title', media_info.get('name', 'Unknown'))}
Overview: {media_info.get('overview', 'No description available')}
Genres: {media_info.get('genres', 'Unknown')}
Cast: {media_info.get('cast', 'Unknown')}
"""
                # Add episode-specific information if available
                if media_info.get('has_episode_data', False):
                    prompt += f"""
EPISODE INFORMATION:
Title: {media_info.get('episode_title', 'Unknown')}
Season/Episode: S{media_info.get('season_number', 0):02d}E{media_info.get('episode_number', 0):02d}
Overview: {media_info.get('episode_overview', 'No description available')}
Air Date: {media_info.get('air_date', 'Unknown')}
"""
                
                # Get and add wiki terminology if available
                try:
                    self.logger.info(f"Attempting to get wiki terminology for: {media_info.get('title', 'Unknown title')}")
                    
                    if self.wiki_terminology:
                        terminology = self.wiki_terminology.get_terminology(media_info)
                        
                        if terminology:
                            # Always add wiki summary if available
                            if terminology.get('wiki_summary'):
                                wiki_summary = terminology.get('wiki_summary')
                                prompt += f"\nSHOW WIKI SUMMARY:\n{wiki_summary}\n"
                                self.logger.info(f"Added wiki summary from {terminology.get('wiki_url', 'Unknown')}")
                            
                            # Add terms if available
                            if terminology.get('terms') and len(terminology.get('terms', [])) > 0:
                                terms = terminology['terms']
                                max_terms = self.config.getint("wiki_terminology", "max_terms", fallback=10)
                                
                                prompt += f"\nIMPORTANT SHOW-SPECIFIC TERMINOLOGY:\n"
                                prompt += f"The following terms have special meanings in this show and must be translated appropriately:\n"
                                
                                # Add up to max_terms terms
                                for term in terms[:max_terms]:
                                    if isinstance(term, dict) and 'term' in term and 'definition' in term:
                                        prompt += f"- {term['term']}: {term['definition']}\n"
                                
                                self.logger.info(f"Added {min(len(terms), max_terms)} wiki terminology entries to prompt")
                            else:
                                self.logger.warning(f"Wiki terminology found but no terms were extracted. Wiki URL: {terminology.get('wiki_url', 'Unknown')}")
                        else:
                            self.logger.warning("No wiki terminology found for this media")
                    else:
                        self.logger.debug("Wiki terminology service not initialized, skipping terminology lookup")
                except Exception as e:
                    self.logger.error(f"Error adding wiki terminology to prompt: {str(e)}", exc_info=True)
                    
            # Add user-defined special meanings if provided
            # Check if special_meanings was explicitly passed as a parameter
            if special_meanings:
                if isinstance(special_meanings, list) and len(special_meanings) > 0:
                    prompt += f"""
USER-DEFINED SPECIAL MEANINGS:
The following terms have special meanings defined by the user and must be translated appropriately:
"""
                    for meaning in special_meanings:
                        if isinstance(meaning, dict) and 'word' in meaning and 'meaning' in meaning:
                            prompt += f"- {meaning['word']}: {meaning['meaning']}\n"
                    
                    self.logger.info(f"Added {len(special_meanings)} user-defined special meanings to Ollama prompt")
            # Legacy format check - in case we still receive specialMeanings through the translations dictionary
            elif isinstance(translations, dict) and isinstance(translations.get('specialMeanings'), list):
                special_meanings = translations.get('specialMeanings')
                if special_meanings and len(special_meanings) > 0:
                    prompt += f"""
USER-DEFINED SPECIAL MEANINGS:
The following terms have special meanings defined by the user and must be translated appropriately:
"""
                    for meaning in special_meanings:
                        if isinstance(meaning, dict) and 'word' in meaning and 'meaning' in meaning:
                            prompt += f"- {meaning['word']}: {meaning['meaning']}\n"
                    
                    self.logger.info(f"Added {len(special_meanings)} user-defined special meanings to Ollama prompt (from translations dict)")

            # Add context lines before if available
            if context_before is not None and len(context_before) > 0:
                prompt += f"""
CONTEXT (PREVIOUS LINES):
{context_before}
"""

            # Add the line being translated with clear marking
            prompt += f"""
-----------------------------------------------------
TEXT TO TRANSLATE: {text}
-----------------------------------------------------
"""

            # Add context lines after if available
            if context_after is not None and len(context_after) > 0:
                prompt += f"""
CONTEXT (FOLLOWING LINES):
{context_after}
"""

            # Add available translations for reference
            prompt += f"""
AVAILABLE TRANSLATIONS:
"""
            
            # First check if a DeepL translation is available
            deepl_translation = None
            if "Deepl" in translations:
                deepl_translation = translations["Deepl"]
            
            # Display translations with DeepL highlighted as the professional service
            for service, translation in translations.items():
                if service != 'specialMeanings':  # Skip the special meanings entry if it exists
                    if service == "Deepl":
                        prompt += f"PROFESSIONAL TRANSLATION - {service.upper()}: {translation}\n"
                    else:
                        prompt += f"{service.upper()}: {translation}\n"

            # Add special instructions for handling DeepL translations
            if deepl_translation:
                # Adjust guidelines based on conservativeness level
                if conservativeness <= 2:
                    # Most conservative
                    deepl_guidelines = f"""
CRITICAL: DeepL Translation Review Guidelines (CONSERVATIVE MODE)

DeepL is a professional translation service with exceptional accuracy. You should ONLY modify DeepL translations in extremely rare cases where you have definitive contextual information that DeepL could not possibly have access to.

STRICT RULES FOR MODIFYING DEEPL TRANSLATIONS:

1. PRESUMPTION OF CORRECTNESS (99.5% of cases):
   - DeepL's translation is correct by default
   - Only intervene if you are 100% certain of an error
   - When in doubt, keep DeepL's translation unchanged

2. ALLOWED CHANGES ONLY when ALL of these conditions are met:
   a) CONTEXTUAL ADVANTAGE: You have specific information that DeepL cannot see:
      - Character names from the show/movie that have established translations
      - Technical terms with show-specific meanings (e.g., "bending" in Avatar)
      - Proper nouns that are consistently translated in the show
      - Cultural references that require show-specific knowledge
   
   b) CLEAR ERROR: The DeepL translation is factually wrong, not just stylistically different
   
   c) HIGH CERTAINTY: You are completely confident based on the provided context
   
   d) MEANING IMPACT: The error significantly changes the intended meaning

3. NEVER CHANGE for:
   - Stylistic preferences
   - Alternative but correct word choices
   - Formal vs informal tone differences
   - Minor phrasing variations
   - Valid idiom translations
   - Any uncertainty about the correct translation

4. CONTEXT EVALUATION:
   - Only use context if it provides definitive information about proper nouns, character names, or show-specific terminology
   - Ignore context that doesn't provide clear factual corrections
   - If context is ambiguous or could be interpreted multiple ways, keep DeepL's translation

5. CONFIDENCE THRESHOLD:
   - You must be 99%+ confident that the change is necessary
   - If you have any doubt, preserve DeepL's translation
   - Remember: DeepL is trained on massive amounts of professional content

EXPECTED BEHAVIOR:
- Modify DeepL translations in less than 0.5% of cases
- Most of your work should be choosing between different service translations when DeepL is not available
- When DeepL is available, it should almost always be the final choice

Remember: Your role is to be a very conservative reviewer. DeepL's professional quality should be respected.
"""
                elif conservativeness == 3:
                    # Balanced (default)
                    deepl_guidelines = f"""
CRITICAL: DeepL Translation Review Guidelines

DeepL is a professional translation service with exceptional accuracy. You should ONLY modify DeepL translations in extremely rare cases where you have definitive contextual information that DeepL could not possibly have access to.

STRICT RULES FOR MODIFYING DEEPL TRANSLATIONS:

1. PRESUMPTION OF CORRECTNESS (99% of cases):
   - DeepL's translation is correct by default
   - Only intervene if you are 100% certain of an error
   - When in doubt, keep DeepL's translation unchanged

2. ALLOWED CHANGES ONLY when ALL of these conditions are met:
   a) CONTEXTUAL ADVANTAGE: You have specific information that DeepL cannot see:
      - Character names from the show/movie that have established translations
      - Technical terms with show-specific meanings (e.g., "bending" in Avatar)
      - Proper nouns that are consistently translated in the show
      - Cultural references that require show-specific knowledge
   
   b) CLEAR ERROR: The DeepL translation is factually wrong, not just stylistically different
   
   c) HIGH CERTAINTY: You are completely confident based on the provided context
   
   d) MEANING IMPACT: The error significantly changes the intended meaning

3. NEVER CHANGE for:
   - Stylistic preferences
   - Alternative but correct word choices
   - Formal vs informal tone differences
   - Minor phrasing variations
   - Valid idiom translations
   - Any uncertainty about the correct translation

4. CONTEXT EVALUATION:
   - Only use context if it provides definitive information about proper nouns, character names, or show-specific terminology
   - Ignore context that doesn't provide clear factual corrections
   - If context is ambiguous or could be interpreted multiple ways, keep DeepL's translation

5. CONFIDENCE THRESHOLD:
   - You must be 95%+ confident that the change is necessary
   - If you have any doubt, preserve DeepL's translation
   - Remember: DeepL is trained on massive amounts of professional content

EXPECTED BEHAVIOR:
- Modify DeepL translations in less than 1% of cases
- Most of your work should be choosing between different service translations when DeepL is not available
- When DeepL is available, it should almost always be the final choice

Remember: Your role is to be a conservative reviewer, not an aggressive editor. DeepL's professional quality should be respected.
"""
                else:
                    # More aggressive (4-5)
                    deepl_guidelines = f"""
DeepL Translation Review Guidelines (CONTEXT-AWARE MODE)

DeepL is a professional translation service with excellent accuracy. You should generally trust DeepL translations, but you may modify them when you have clear contextual information that provides a significant advantage.

RULES FOR MODIFYING DEEPL TRANSLATIONS:

1. PRESUMPTION OF CORRECTNESS (95% of cases):
   - DeepL's translation is usually correct
   - Intervene when you have clear contextual advantages
   - When in doubt, keep DeepL's translation unchanged

2. ALLOWED CHANGES when you have:
   a) CONTEXTUAL ADVANTAGE: Specific information that DeepL cannot see:
      - Character names from the show/movie that have established translations
      - Technical terms with show-specific meanings
      - Proper nouns that are consistently translated in the show
      - Cultural references that require show-specific knowledge
   
   b) CLEAR IMPROVEMENT: The change makes the translation more accurate or contextually appropriate
   
   c) REASONABLE CERTAINTY: You are confident based on the provided context

3. AVOID CHANGES for:
   - Minor stylistic preferences
   - Valid alternative translations
   - When context is ambiguous

4. CONTEXT EVALUATION:
   - Use context to improve translations when it provides clear advantages
   - Be careful not to over-interpret ambiguous context

EXPECTED BEHAVIOR:
- Modify DeepL translations in about 5% of cases when context provides clear advantages
- Trust DeepL's professional quality while using context when beneficial
"""
                
                prompt += deepl_guidelines

            # Add final reminder
            prompt += """
ANALYSIS STEPS:
1. First, analyze the source text for any ambiguous terms or cultural references
2. Check if the context provides additional information that affects meaning
3. Compare the available translations for accuracy and nuance
4. Consider the target language's grammar and natural expression patterns
5. Evaluate whether any show-specific terminology needs special handling
6. Make your final decision based on the most contextually appropriate translation

IMPORTANT: Return ONLY your translation of the text between the dotted lines. Do not include explanations, notes, or the original text.
"""

            # Debug output
            debug_mode = self.config.getboolean('general', 'debug_mode', fallback=False)
            if debug_mode:
                self.logger.debug(f"Sending request to Ollama final translator with prompt: {prompt}")
            else:
                self.logger.debug(f"Sending request to Ollama final translator with prompt: {prompt[:100]}...") # Log truncated prompt

            # Now add the actual API call to Ollama (copying from _translate_with_ollama method)
            server_url = self.config.get("ollama", "server_url", fallback="http://localhost:11434")
            model = self.config.get("ollama", "model", fallback="")
            endpoint = self.config.get("ollama", "endpoint", fallback="/api/generate")
            url = f"{server_url.rstrip('/')}/{endpoint.lstrip('/')}"
            temperature = self.config.getfloat("general", "temperature", fallback=0.3)
            
            # Create request data with only the essential parameters
            data = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature
                }
            }
            
            # Add additional Ollama options if configured
            options = {}
            for option_name in ["num_gpu", "num_thread", "num_ctx"]:
                if self.config.has_option("ollama", option_name):
                    # Verify the option is actually set and not commented out
                    raw_value = self.config.get("ollama", option_name, fallback=None)
                    if raw_value is not None and str(raw_value).strip() and not str(raw_value).strip().startswith('#'):
                        try:
                            # Only include numeric options with valid integer values
                            options[option_name] = self.config.getint("ollama", option_name)
                            self.logger.debug(f"Including Ollama option from config: {option_name}={options[option_name]}")
                        except ValueError:
                            self.logger.warning(f"Invalid value for Ollama option '{option_name}': {raw_value}")
            
            # Add boolean options with the same careful checking
            for option_name in ["use_mmap", "use_mlock"]:
                if self.config.has_option("ollama", option_name):
                    raw_value = self.config.get("ollama", option_name, fallback=None)
                    if raw_value is not None and str(raw_value).strip() and not str(raw_value).strip().startswith('#'):
                        try:
                            # Only include boolean options with valid values
                            options[option_name] = self.config.getboolean("ollama", option_name)
                            self.logger.debug(f"Including Ollama option from config: {option_name}={options[option_name]}")
                        except ValueError:
                            self.logger.warning(f"Invalid value for Ollama option '{option_name}': {raw_value}")
            
            # Only update the options in the request if we have valid options
            if options:
                data["options"].update(options)
                self.logger.debug(f"Sending Ollama options: {json.dumps(options)}")
            
            # Make request with retry logic
            max_retries = 3
            
            for attempt in range(max_retries):
                self.logger.info(f"Waiting for Ollama final response (attempt {attempt+1}/{max_retries})...")
                try:
                    response = requests.post(url, json=data, timeout=180)
                    self.logger.debug(f"Ollama final translator response status: {response.status_code}")
                    
                    response.raise_for_status()
                    result = response.json()
                    
                    if "response" in result:
                        translated_text = result["response"].strip()
                        
                        # Apply think tags filter to remove thinking content
                        translated_text = self.remove_think_tags(translated_text)
                        
                        # Clean up response - removing quotes, prefixes, etc.
                        translated_text = translated_text.strip(' "\'\n`')
                        
                        # Remove potential prefixes the model might add
                        prefixes_to_remove = [
                            "Translation:", 
                            "Translated text:", 
                            "Here's the translation:",
                            "Final translation:"
                        ]
                        for prefix in prefixes_to_remove:
                            if translated_text.lower().startswith(prefix.lower()):
                                translated_text = translated_text[len(prefix):].strip()
                        
                        # Fix one-character-per-line issue with HTML tags
                        if '\n' in translated_text and '<' in translated_text and '>' in translated_text:
                            # More robust HTML tag detection 
                            lines = translated_text.split('\n')
                            # Check if a significant number of lines are single characters
                            single_char_lines = sum(1 for line in lines if len(line.strip()) == 1)
                            
                            # If more than 30% of lines are single characters, or we detect a broken HTML tag
                            if (single_char_lines / len(lines) > 0.3) or any('<' in ''.join(lines[:5]) and '>' in ''.join(lines) for i in range(len(lines))):
                                translated_text = translated_text.replace('\n', '')
                                self.logger.debug("Fixed multi-line HTML tag in translation")
                        
                        return translated_text
                    
                    self.logger.warning(f"Ollama final translator returned no translatable content in attempt {attempt+1}")
                    time.sleep(2)  # Wait before retrying
                    
                except Exception as e:
                    self.logger.error(f"Error in Ollama final translator attempt {attempt+1}: {str(e)}")
                    if attempt < max_retries - 1:
                        time.sleep(2)
            
            return None
        except Exception as e:
            self.logger.error(f"Error using Ollama as final translator: {str(e)}")
            return None

    def get_media_info(self, title, year=None, original_filename=None, season=None, episode=None):
        """Get movie or TV show information from TMDB API by trying both types.
        
        Args:
            title: The title of the media
            year: Optional year of release
            original_filename: Original filename with potential season/episode info
            season: Optional season number extracted from filename
            episode: Optional episode number extracted from filename
        """
        if not self.use_tmdb or not self.tmdb_api_key:
            self.logger.warning("TMDB is disabled or API key not set")
            return None
            
        try:
            self.logger.debug(f"Searching TMDB for: '{title}' (Year: {year or 'Any'})")
            
            # First try as a TV show
            tv_info = self._fetch_media_info(title, year, "tv")
            if tv_info:
                self.logger.info(f"Found TV show information for '{title}'")
                
                # Check if season/episode numbers were provided
                # Use the explicit season/episode parameters if provided
                if season and episode:
                    self.logger.debug(f"Using provided season/episode: S{season:02d}E{episode:02d}")
                    episode_info = self._fetch_episode_info(tv_info["tmdb_id"], season, episode)
                    if episode_info:
                        # Combine show and episode information
                        tv_info.update(episode_info)
                        self.logger.info(f"Enhanced TV info with episode data: S{season:02d}E{episode:02d} - {episode_info.get('episode_title', 'Unknown')}")
                
                return tv_info
                
            # If no TV show found, try as a movie
            movie_info = self._fetch_media_info(title, year, "movie")
            if movie_info:
                self.logger.info(f"Found movie information for '{title}'")
                return movie_info
                
            self.logger.warning(f"No TMDB results found for '{title}' as either TV show or movie")
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting media info from TMDB: {str(e)}")
            import traceback
            self.logger.debug(f"TMDB error details: {traceback.format_exc()}")
            return None
            
    def _fetch_media_info(self, title, year=None, media_type="movie"):
        """Internal method to fetch media info from TMDB for a specific type."""
        try:
            self.logger.debug(f"Searching TMDB for '{title}' as {media_type}")
            
            # Search for the media item
            search_url = f"https://api.themoviedb.org/3/search/{media_type}"
            params = {
                "api_key": self.tmdb_api_key,
                "query": title,
                "language": self.tmdb_language
            }
            if year:
                params["year" if media_type == "movie" else "first_air_date_year"] = year
            
            self.logger.debug(f"TMDB API call: GET {search_url} with params: {params}")
            response = requests.get(search_url, params=params)
            
            # Log response status
            self.logger.debug(f"TMDB {media_type} search response status: {response.status_code}")
            
            if response.status_code != 200:
                self.logger.warning(f"TMDB {media_type} search failed: {response.status_code} - {response.text}")
                return None
                
            search_results = response.json()
            
            # Log search results summary
            result_count = len(search_results.get("results", []))
            self.logger.debug(f"TMDB {media_type} search found {result_count} results")
            
            if not search_results.get("results"):
                self.logger.debug(f"No TMDB {media_type} results found for: {title}")
                return None
                
            # Get the first result
            media_id = search_results["results"][0]["id"]
            result_title = search_results["results"][0].get("title" if media_type == "movie" else "name", "Unknown")
            
            # Log the selected result
            self.logger.debug(f"Selected TMDB {media_type} result: ID {media_id}, Title: {result_title}")
            
            # Get detailed information
            details_url = f"https://api.themoviedb.org/3/{media_type}/{media_id}"
            details_params = {
                "api_key": self.tmdb_api_key,
                "language": self.tmdb_language,
                "append_to_response": "credits"
            }
            
            self.logger.debug(f"TMDB {media_type} details API call: GET {details_url}")
            details_response = requests.get(details_url, params=details_params)
            
            # Log details response status
            self.logger.debug(f"TMDB {media_type} details response status: {details_response.status_code}")
            
            if details_response.status_code != 200:
                self.logger.warning(f"TMDB {media_type} details fetch failed: {details_response.status_code} - {details_response.text}")
                return None
                
            details = details_response.json()
            
            # Build summary
            info = {
                "type": media_type,
                "title": details.get("title", details.get("name", "")),
                "overview": details.get("overview", ""),
                "genres": ", ".join([genre["name"] for genre in details.get("genres", [])]),
                "release_date": details.get("release_date", details.get("first_air_date", "")),
                "cast": ", ".join([cast["name"] for cast in details.get("credits", {}).get("cast", [])[:5]]),
                "id": media_id,  # Store the media ID with a consistent key name
                "tmdb_id": media_id  # Also include the original key name for backward compatibility
            }
            
            self.logger.info(f"Successfully retrieved TMDB data for '{info['title']}' ({media_type})")
            self.logger.debug(f"TMDB data details: {json.dumps(info)}")
            return info
            
        except Exception as e:
            self.logger.error(f"Error fetching {media_type} info from TMDB: {str(e)}")
            return None

    def _fetch_episode_info(self, tv_id, season_number, episode_number):
        """Fetch details for a specific episode of a TV show."""
        try:
            self.logger.debug(f"Fetching episode info for TV ID {tv_id}, S{season_number:02d}E{episode_number:02d}")
            
            # API URL for episode information
            url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season_number}/episode/{episode_number}"
            params = {
                "api_key": self.tmdb_api_key,
                "language": self.tmdb_language
            }
            
            self.logger.debug(f"TMDB episode API call: GET {url}")
            response = requests.get(url, params=params)
            
            # Log response status
            self.logger.debug(f"TMDB episode info response status: {response.status_code}")
            
            if response.status_code != 200:
                self.logger.warning(f"TMDB episode info fetch failed: {response.status_code} - {response.text}")
                return None
                
            episode_data = response.json()
            
            # Extract relevant episode information
            episode_info = {
                "episode_title": episode_data.get("name", ""),
                "episode_overview": episode_data.get("overview", ""),
                "episode_number": episode_data.get("episode_number", 0),
                "season_number": episode_data.get("season_number", 0),
                "air_date": episode_data.get("air_date", ""),
                "has_episode_data": True
            }
            
            self.logger.info(f"Successfully retrieved episode data: '{episode_info['episode_title']}'")
            return episode_info
            
        except Exception as e:
            self.logger.error(f"Error fetching episode info: {str(e)}")
            return None

    def load_special_meanings(self):
        """
        Load special word meanings from the JSON file.
        
        Returns:
            List of dictionaries containing word meanings or empty list if file doesn't exist
        """
        meanings_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                    'files', 'meaning.json')
        try:
            if os.path.exists(meanings_file):
                self.logger.info(f"Loading special meanings from {meanings_file}")
                with open(meanings_file, 'r', encoding='utf-8') as f:
                    meanings = json.load(f)
                self.logger.info(f"Loaded {len(meanings)} special meanings from file")
                return meanings
            else:
                self.logger.warning(f"Special meanings file not found: {meanings_file}")
                return []
        except Exception as e:
            self.logger.error(f"Error loading special meanings: {str(e)}")
            return []
            
    def save_special_meanings(self, meanings):
        """
        Save special word meanings to the JSON file.
        
        Args:
            meanings: List of dictionaries containing word meanings
        
        Returns:
            Boolean indicating success or failure
        """
        meanings_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                    'files', 'meaning.json')
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(meanings_file), exist_ok=True)
            
            with open(meanings_file, 'w', encoding='utf-8') as f:
                json.dump(meanings, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Saved {len(meanings)} special meanings to {meanings_file}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving special meanings: {str(e)}")
            return False

    def remove_think_tags(self, text: str) -> str:
        """
        Remove content between <think> and </think> tags.
        This allows models to include their thinking process without it showing up in the final output.
        
        Args:
            text: The text to process
            
        Returns:
            Text with the thinking content removed
        """
        if not text:
            return ""
            
        # Use regex to remove anything between <think> and </think> tags, including the tags
        cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        
        # If debug mode is enabled, log when thinking content was removed
        debug_mode = self.config.getboolean('general', 'debug_mode', fallback=False)
        if debug_mode and text != cleaned_text:
            self.logger.debug(f"Removed thinking content from response (original length: {len(text)}, new length: {len(cleaned_text)})")
            
        return cleaned_text.strip()

    def _extract_special_tokens(self, text: str):
        """Return a list of special punctuation / tag tokens to preserve."""
        if not text:
            return []
        # Match HTML tags, bracketed cues, ellipsis, musical notes, etc.
        pattern = r"(<[^>]+>|\.{3}|…|♪|\[|\]|\(|\)|--|—|–)"
        return re.findall(pattern, text)

    def _validate_tokens(self, source_text: str, target_text: str) -> bool:
        """Ensure every special token from source exists in target."""
        source_tokens = self._extract_special_tokens(source_text)
        for tok in source_tokens:
            if tok and tok not in target_text:
                return False
        return True

    def _apply_glossary_post_replace(self, translated_text: str) -> str:
        """Deterministically replace glossary terms inside translated text."""
        if not translated_text or not self.special_meanings:
            return translated_text
        new_text = translated_text
        for entry in self.special_meanings:
            # Expect format {"word": "airbender", "meaning": "luftbøjer"}
            src = entry.get('word')
            tgt = entry.get('meaning')
            if src and tgt and src.lower() in new_text.lower():
                pattern = re.compile(rf"\b{re.escape(src)}\b", flags=re.IGNORECASE)

                def _case_preserve(match):
                    word = match.group(0)
                    if word.isupper():
                        return tgt.upper()
                    if word[0].isupper():
                        return tgt.capitalize()
                    return tgt

                new_text = pattern.sub(_case_preserve, new_text)
        return new_text

    def _apply_postprocessing(self, original_text: str, prefix: str, result_details: dict) -> dict:
        """Apply glossary replacement, token validation and prefix re-attachment."""
        # Glossary deterministic replacement
        if self.glossary_post_replace:
            result_details["final_text"] = self._apply_glossary_post_replace(result_details["final_text"])
        # Reattach speaker prefix
        if self.freeze_speaker_labels and prefix:
            result_details["final_text"] = prefix + result_details["final_text"]
            if result_details.get("first_pass_text"):
                result_details["first_pass_text"] = prefix + result_details["first_pass_text"]
            for key in list(result_details["collected_translations"].keys()):
                result_details["collected_translations"][key] = prefix + result_details["collected_translations"][key]
        # Validate tokens and optionally fall back to DeepL
        if self.enforce_special_tokens and not self._validate_tokens(original_text, result_details["final_text"]):
            self.logger.warning("Special token validation failed; attempting DeepL fallback")
            deepl_txt = result_details["collected_translations"].get("Deepl")
            if deepl_txt and self._validate_tokens(original_text, deepl_txt):
                result_details["final_text"] = deepl_txt
        return result_details