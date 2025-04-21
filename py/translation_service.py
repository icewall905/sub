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

        # Add TMDB API key
        self.tmdb_api_key = config.get("tmdb", "api_key", fallback=None)
        self.use_tmdb = config.getboolean("tmdb", "enabled", fallback=False)
        self.tmdb_language = config.get("tmdb", "language", fallback="en-US")
    
    def get_iso_code(self, language_name: str) -> str:
        """Convert a language name to its ISO code."""
        language_name = language_name.lower().strip('"\' ')
        return self.language_mapping.get(language_name, language_name)
    
    def translate(self, text: str, source_lang: str, target_lang: str, context=None, media_info=None) -> Dict[str, Any]:
        """
        Translate text from source language to target language.
        Uses the configured translation services in order of priority.
        
        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            context: Optional context text (surrounding subtitles)
            media_info: Optional media information from TMDB
            
        Returns:
            Dictionary containing:
            - 'final_text': The final translated text (str)
            - 'collected_translations': Dictionary of translations from each service (Dict[str, str])
            - 'first_pass_text': The result after the first pass (e.g., from Ollama final) (Optional[str])
        """
        if not text.strip():
            return {"final_text": text, "collected_translations": {}, "first_pass_text": None}
        
        # Default return structure
        result_details = {
            "final_text": text, # Default to original if all fails
            "collected_translations": {},
            "first_pass_text": None
        }

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
                ollama_final_result = self._translate_with_ollama_as_final(text, source_lang, target_lang, collected_translations)
                end_time = time.time()
                
                if ollama_final_result:
                    self.logger.info(f"Ollama successfully provided the final translation in {end_time - start_time:.2f} seconds")
                    result_details["final_text"] = ollama_final_result
                    result_details["first_pass_text"] = ollama_final_result # In this flow, Ollama's result is the first pass
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
                (service == "ollama" and self.config.getboolean("ollama", "enabled", fallback=True)) or
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
                    return result_details # Return on first success

            except Exception as e:
                self.logger.error(f"Error using {service} translation service: {str(e)}")

        # If all services fail
        self.logger.warning("All translation services failed, returning original text")
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
    
    def _translate_with_ollama(self, text: str, source_lang: str, target_lang: str, context=None, media_info=None) -> str:
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
        
        # Create prompt for translation with clear instructions
        if media_info:
            prompt = (
                f"You are an expert translator from {source_full} to {target_full}.\n"
                f"These subtitles are for: {media_info['title']}\n"
                f"Plot summary: {media_info['overview']}\n"
                f"Genre: {media_info['genres']}\n"
                f"Main cast: {media_info['cast']}\n\n"
                f"Consider this context from surrounding subtitles:\n\n"
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
        if context:
            prompt = (
                f"You are an expert translator from {source_full} to {target_full}.\n"
                f"Consider this context from surrounding subtitles:\n\n"
                f"CONTEXT:\n{context}\n\n"
                f"Translate this text: {text}\n\n"
                f"Maintain the same formatting, tone, and meaning. Return ONLY the translated text."
            )
        
        # --- Revert to reading endpoint from config, fallback to /api/generate --- 
        endpoint = self.config.get("ollama", "endpoint", fallback="/api/generate") 
        url = f"{server_url.rstrip('/')}{endpoint}"
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
        self.logger.info("---- Ollama Options Debug ----")
        self.logger.info(f"Direct .get('ollama', 'num_gpu') = '{self.config.get('ollama', 'num_gpu', fallback='NOT_FOUND')}'")
        self.logger.info(f"has_option('ollama', 'num_gpu') = {self.config.has_option('ollama', 'num_gpu')}")
        self.logger.info(f"All options in [ollama] section: {[opt for opt in self.config['ollama']]}")
        self.logger.info("----------------------------")

        # Now modify the options handling for even stricter checking:
        options = {}
        for option in ["num_gpu", "num_thread"]:
            try:
                # Extra-strict check - only proceed if option exists AND has a non-empty value
                if self.config.has_option("ollama", option):
                    raw_value = self.config.get("ollama", option, fallback=None)
                    if raw_value is not None and str(raw_value).strip() != "" and not str(raw_value).strip().startswith('#'):
                        try:
                            options[option] = int(raw_value)
                            self.logger.info(f"✓ Adding {option}={raw_value} to Ollama request")
                        except ValueError:
                            self.logger.warning(f"× Invalid value for ollama.{option}, skipping")
                    else:
                        self.logger.info(f"× Not adding {option} - empty or commented out value: '{raw_value}'")
                else:
                    self.logger.info(f"× Option {option} not found in config")
            except Exception as e:
                self.logger.error(f"Error processing option {option}: {e}")
        for option in ["use_mmap", "use_mlock"]:
            if self.config.has_option("ollama", option):
                value = self.config.get("ollama", option, fallback=None)
                if value is not None and str(value).strip() != "":
                    try:
                        options[option] = self.config.getboolean("ollama", option)
                        self.logger.debug(f"Adding {option}={value} to Ollama request")
                    except ValueError:
                        self.logger.warning(f"Invalid value for ollama.{option}, skipping")
        if options:
            data["options"].update(options)
        
        # Make request with retries
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Calling Ollama API with model {model} at URL {url} (attempt {attempt+1}/{max_retries})")
                self.logger.debug(f"Request data: {json.dumps(data)}")
                
                # Increase timeout for large or complex translations
                timeout = 180  # 3 minutes should be sufficient for most translations
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
                # --- End /api/generate response parsing ---
                
                if translated_text:
                    # Clean up response - Ollama sometimes adds extra quotes or markdown
                    translated_text = translated_text.strip(' "\'\n`')
                    
                    # Additional cleanup to remove common patterns that Ollama might add
                    # Remove quotes, backticks, and markdown formatting
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
                    
                    self.logger.debug(f"Ollama translation successful: {translated_text[:50]}...")
                    return translated_text
                else:
                    self.logger.warning(f"Ollama API returned no translatable content in attempt {attempt+1}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return ""
                
            except requests.exceptions.Timeout:
                self.logger.warning(f"Ollama API request timed out on attempt {attempt+1}")
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

    def _translate_with_ollama_as_final(self, text: str, source_lang: str, target_lang: str, translations: dict) -> Optional[str]:
        """
        Use Ollama as the final translator by presenting it with translations from other services.
        Returns the final translated text chosen or refined by Ollama, or None if failed.
        """
        if not translations:
            self.logger.warning("No translations provided to Ollama as final translator, attempting direct Ollama translation.")
            return self._translate_with_ollama(text, source_lang, target_lang)
        
        try:
            server_url = self.config.get("ollama", "server_url", fallback="http://localhost:11434")
            model = self.config.get("ollama", "model", fallback="") # Ensure model is fetched
            if not model:
                self.logger.error("Ollama model not configured for final translation.")
                return None

            source_full = self._get_language_full_name(source_lang)
            target_full = self._get_language_full_name(target_lang)
            
            prompt = f"""You are a subtitle translation expert. Your task is to choose the best translation or create an improved one.

Original {source_full} text: {text}

Available translations to {target_full}:
"""
            for service_name, translation in translations.items():
                prompt += f"\n{service_name.upper()}: {translation}"
            
            prompt += f"""

Please analyze these translations and provide the best possible {target_full} translation.
Consider fluency, accuracy, and natural tone in the target language.
Return ONLY the final translation without any explanations or other text.
"""
            
            # --- Revert to reading endpoint from config, fallback to /api/generate --- 
            endpoint = self.config.get("ollama", "endpoint", fallback="/api/generate") 
            url = f"{server_url.rstrip('/')}{endpoint}"
            # --- End endpoint change ---
            
            temperature = self.config.getfloat("general", "temperature", fallback=0.3)
            
            # --- Use /api/generate payload structure --- 
            # Combine system-like instructions with the main prompt
            system_instructions = "You are a professional translator. Choose or improve from the provided translations. Return only the final translation."
            full_prompt = f"{system_instructions}\n\n{prompt}" # Use the prompt built earlier
            
            data = {
                "model": model,
                "prompt": full_prompt, # Use 'prompt' key
                "stream": False,
                "options": {"temperature": temperature}
            }
            # --- End /api/generate payload ---

            # Add additional Ollama options if configured
            options = {}
            for option in ["num_gpu", "num_thread"]:
                if self.config.has_option("ollama", option):
                    value = self.config.get("ollama", option, fallback=None)
                    if value is not None and str(value).strip() != "":
                        try:
                            options[option] = int(value)
                        except ValueError:
                            self.logger.warning(f"Invalid value for ollama.{option}, skipping")
            for option in ["use_mmap", "use_mlock"]:
                if self.config.has_option("ollama", option):
                    value = self.config.get("ollama", option, fallback=None)
                    if value is not None and str(value).strip() != "":
                        try:
                            options[option] = self.config.getboolean("ollama", option)
                        except ValueError:
                            self.logger.warning(f"Invalid value for ollama.{option}, skipping")
            if options:
                data["options"].update(options)

            self.logger.debug(f"Sending request to Ollama final translator with prompt: {prompt[:200]}...") # Log truncated prompt
            
            max_retries = 3
            retry_delay = 2
            timeout = 180
            
            for attempt in range(max_retries):
                try:
                    # Log waiting message *once* per attempt using logger
                    self.logger.info(f"Waiting for Ollama final response (attempt {attempt+1}/{max_retries})...")
                    
                    response = requests.post(url, json=data, timeout=timeout)
                    self.logger.debug(f"Ollama final translator response status: {response.status_code}")
                    response.raise_for_status()
                    result = response.json()
                    
                    # --- Parse /api/generate response structure --- 
                    final_translation = ""
                    if "response" in result:
                        final_translation = result["response"].strip(' "\'\n`')
                    # --- End /api/generate response parsing ---

                    if final_translation:
                        # Remove potential prefixes that the model might add
                        prefixes_to_remove = ["Translation:", "Translated text:", "Here's the translation:", "Final translation:"]
                        for prefix in prefixes_to_remove:
                            if final_translation.startswith(prefix):
                                final_translation = final_translation[len(prefix):].strip()
                        # Success log is handled in the main translate method
                        return final_translation
                    else:
                        self.logger.warning(f"Ollama API (final) returned no translatable content in attempt {attempt+1}")
                        if attempt < max_retries - 1: time.sleep(retry_delay)
                        else: return None # Failed after retries
                    
                except requests.exceptions.Timeout:
                    self.logger.warning(f"Ollama API (final) request timed out on attempt {attempt+1}, retrying...")
                    if attempt < max_retries - 1: time.sleep(retry_delay * (attempt + 1)) # Exponential backoff
                    else: return None # Failed after retries
                except requests.exceptions.RequestException as e:
                    self.logger.error(f"Ollama API (final) request failed on attempt {attempt+1}: {str(e)}")
                    if attempt < max_retries - 1: time.sleep(retry_delay)
                    else: return None # Failed after retries
                except json.JSONDecodeError as e:
                    self.logger.error(f"Error parsing Ollama (final) response on attempt {attempt+1}: {str(e)}")
                    if attempt < max_retries - 1: time.sleep(retry_delay)
                    else: return None # Failed after retries
            
            self.logger.error("All Ollama final translation attempts failed")
            return None
                
        except Exception as e:
            self.logger.error(f"Error using Ollama as final translator: {str(e)}")
            return None

    def get_media_info(self, title, year=None, type="movie"):
        """Get movie or TV show information from TMDB API."""
        if not self.use_tmdb or not self.tmdb_api_key:
            return None
            
        try:
            # Search for the movie or TV show
            search_url = f"https://api.themoviedb.org/3/search/{type}"
            params = {
                "api_key": self.tmdb_api_key,
                "query": title,
                "language": self.tmdb_language
            }
            if year:
                params["year"] = year
                
            response = requests.get(search_url, params=params)
            search_results = response.json()
            
            if not search_results.get("results"):
                return None
                
            # Get the first result
            media_id = search_results["results"][0]["id"]
            
            # Get detailed information
            details_url = f"https://api.themoviedb.org/3/{type}/{media_id}"
            details_params = {
                "api_key": self.tmdb_api_key,
                "language": self.tmdb_language,
                "append_to_response": "credits"
            }
            
            details_response = requests.get(details_url, params=details_params)
            details = details_response.json()
            
            # Build summary
            info = {
                "title": details.get("title", details.get("name", "")),
                "overview": details.get("overview", ""),
                "genres": ", ".join([genre["name"] for genre in details.get("genres", [])]),
                "release_date": details.get("release_date", details.get("first_air_date", "")),
                "cast": ", ".join([cast["name"] for cast in details.get("credits", {}).get("cast", [])[:5]])
            }
            
            return info
        except Exception as e:
            self.logger.error(f"Error getting media info from TMDB: {str(e)}")
            return None