import os
import re
import time
import json
import logging
import requests
from typing import Dict, List, Optional, Tuple, Any

# Language mapping dictionary
LANGUAGE_MAPPING = {
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

class SubtitleProcessor:
    """
    Class responsible for processing and translating subtitle files.
    """
    
    def __init__(self, logger=None):
        """Initialize the subtitle processor with optional custom logger."""
        self.logger = logger or logging.getLogger(__name__)
        
    def get_iso_code(self, language_name: str) -> str:
        """Convert a language name to its ISO code."""
        language_name = language_name.lower().strip('"\' ')
        return LANGUAGE_MAPPING.get(language_name, language_name)
    
    def call_translation_service_with_retry(self, translate_func, *args, max_retries=3, 
                                           base_delay=2, service_name=None, **kwargs) -> str:
        """
        Generic retry wrapper for translation service calls with exponential backoff.
        
        Args:
            translate_func: The translation function to call
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay in seconds (will be multiplied exponentially)
            service_name: Optional name of the service for better logging
            *args, **kwargs: Arguments to pass to the translation function
        
        Returns:
            The translation result or empty string if all retries fail
        """
        import random
        import time
        
        service_label = f"[{service_name}]" if service_name else ""
        
        for attempt in range(max_retries + 1):
            try:
                result = translate_func(*args, **kwargs)
                if result:  # If we got a valid result, return it
                    return result
                
                # If the result is empty but no exception occurred, we might still want to retry
                if attempt < max_retries:
                    self.logger.warning(f"{service_label} Empty result from translation service. Retrying ({attempt + 1}/{max_retries})...")
                else:
                    self.logger.warning(f"{service_label} Empty result from translation service after {max_retries} retries.")
                    return ""
                    
            except Exception as e:
                if "429" in str(e) or "Too Many Requests" in str(e):
                    if attempt < max_retries:
                        # Calculate delay with exponential backoff and jitter
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        self.logger.warning(f"{service_label} Rate limit exceeded. Retrying in {delay:.2f} seconds ({attempt + 1}/{max_retries})...")
                        time.sleep(delay)
                    else:
                        self.logger.error(f"{service_label} Rate limit exceeded after {max_retries} retries.")
                        return ""
                else:
                    # For other types of errors, we might still want to retry
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        self.logger.warning(f"{service_label} Translation error: {e}. Retrying in {delay:.2f} seconds ({attempt + 1}/{max_retries})...")
                        time.sleep(delay)
                    else:
                        self.logger.error(f"{service_label} Translation failed after {max_retries} retries: {e}")
                        return ""
        
        return ""
    
    def call_deepl(self, api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
        """Call DeepL translation API."""
        source_iso = self.get_iso_code(source_lang)
        target_iso = self.get_iso_code(target_lang)
        params = {
            "auth_key": api_key,
            "text": text,
            "source_lang": source_iso.upper(),
            "target_lang": target_iso.upper(),
        }
        self.logger.debug(f"Calling DeepL: {api_url} / {source_iso} -> {target_iso}")
        try:
            response = requests.post(api_url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            if "translations" in result and len(result["translations"]) > 0:
                return result["translations"][0]["text"]
            return ""
        except requests.exceptions.Timeout:
            self.logger.error("DeepL translation timed out")
            return ""
        except requests.exceptions.RequestException as e:
            self.logger.error(f"DeepL translation error: {e}")
            return ""
        except json.JSONDecodeError as e:
            self.logger.error(f"DeepL invalid JSON response: {e}")
            return ""
    
    def call_google_translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Uses the Google Translate API (free web API approach) for translation.
        """
        import urllib.parse
        source_iso = self.get_iso_code(source_lang)
        target_iso = self.get_iso_code(target_lang)
        
        self.logger.debug(f"Calling Google Translate: {source_iso} -> {target_iso}")
        
        base_url = "https://translate.googleapis.com/translate_a/single"
        
        params = {
            "client": "gtx",
            "sl": source_iso,
            "tl": target_iso,
            "dt": "t",
            "q": text
        }
        
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            # Extract translation from Google's response
            translation = ""
            if result and isinstance(result, list) and len(result) > 0:
                # Concatenate all translated parts
                for part in result[0]:
                    if part and isinstance(part, list) and len(part) > 0:
                        translation += part[0]
            
            return translation
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Google translation error: {e}")
            return ""
        except (ValueError, KeyError, IndexError) as e:
            self.logger.error(f"Google translation parsing error: {e}")
            return ""
    
    def call_openai(self, api_key: str, api_base_url: str, model: str, prompt: str, temperature: float = 0.2) -> str:
        """Call OpenAI API for text generation."""
        url = f"{api_base_url.rstrip('/')}/chat/completions"
        self.logger.debug(f"Calling OpenAI: POST {url} | Model: {model} | Temperature: {temperature}")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature
        }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"].strip()
            return ""
        except requests.exceptions.Timeout:
            self.logger.error("OpenAI request timed out")
            return ""
        except requests.exceptions.RequestException as e:
            self.logger.error(f"OpenAI API error: {e}")
            return ""
        except json.JSONDecodeError as e:
            self.logger.error(f"OpenAI invalid JSON response: {e}")
            return ""
    
    def call_ollama(self, server_url: str, endpoint_path: str, model: str, prompt: str, temperature: float = 0.2, cfg=None) -> str:
        """Call Ollama API for text generation."""
        url = f"{server_url.rstrip('/')}{endpoint_path}"

        # Create the basic payload without options first
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {}
        }

        # Only add parameters that are explicitly defined in the config
        if cfg is not None and cfg.has_section("ollama"):
            options = {}
            if cfg.has_option("ollama", "temperature"):
                temperature = cfg.getfloat("ollama", "temperature", fallback=temperature)
            
            # Model parameters
            if cfg.has_option("ollama", "num_gpu"):
                options["num_gpu"] = cfg.getint("ollama", "num_gpu", fallback=1)
            if cfg.has_option("ollama", "num_thread"):
                options["num_thread"] = cfg.getint("ollama", "num_thread", fallback=4)
            if cfg.has_option("ollama", "num_ctx"):
                options["num_ctx"] = cfg.getint("ollama", "num_ctx", fallback=2048)
            
            # Memory mapping options
            if cfg.has_option("ollama", "use_mmap"):
                options["use_mmap"] = cfg.getboolean("ollama", "use_mmap", fallback=True)
            if cfg.has_option("ollama", "use_mlock"):
                options["use_mlock"] = cfg.getboolean("ollama", "use_mlock", fallback=False)
                
            # Set temperature
            options["temperature"] = temperature
            
            # Add options to payload if any were set
            if options:
                payload["options"] = options
        else:
            # If no config, just set the temperature
            payload["options"] = {"temperature": temperature}
        
        try:
            self.logger.debug(f"Calling Ollama: POST {url} | Model: {model} | Temperature: {temperature}")
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            # Ollama response has a 'response' field with the generated text
            if "response" in result:
                return result["response"].strip()
            return ""
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ollama API error: {e}")
            return ""
    
    def sanitize_text(self, text: str) -> str:
        """Clean subtitle text by removing HTML tags and standardizing special content."""
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        text = re.sub(r'<[^>]*>', '', text)
        text = re.sub(r'\[(.*?)\]', r'#BRACKET_OPEN#\1#BRACKET_CLOSE#', text)
        text = re.sub(r' +', ' ', text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.strip()
    
    def preprocess_subtitle(self, text: str) -> str:
        """
        Pre-process subtitle text to normalize and protect special content
        before translation.
        """
        # Handle bracket content consistently
        text = re.sub(r'\[(.*?)\]', r'#BRACKET_OPEN#\1#BRACKET_CLOSE#', text)
        
        # Handle HTML tags properly
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        text = re.sub(r'<[^>]*>', '', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Handle special characters
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        return text.strip()

    def postprocess_translation(self, text: str) -> str:
        """
        Post-process translated text to restore formatting and fix common issues.
        """
        # Restore brackets
        text = text.replace('#BRACKET_OPEN#', '[').replace('#BRACKET_CLOSE#', ']')
        
        # Fix common Danish punctuation issues
        text = text.replace(' ,', ',').replace(' .', '.')
        text = text.replace(' !', '!').replace(' ?', '?')
        text = text.replace(' :', ':').replace(' ;', ';')
        
        # Ensure proper spacing after punctuation
        text = re.sub(r'([,.!?;:])([^\s])', r'\1 \2', text)
        
        # Fix common spacing issues with quotation marks
        text = re.sub(r'"\s+', '" ', text)
        text = re.sub(r'\s+"', ' "', text)
        
        # Fix capitalization issues
        text = re.sub(r'^([a-zæøå])', lambda m: m.group(1).upper(), text)
        
        # Fix common Danish specific issues
        text = text.replace("Jeg er", "Jeg er").replace("Du er", "Du er")
        
        return text.strip()
    
    def translate_srt(self, input_path, output_path, cfg, progress_dict=None):
        """
        Translate an SRT subtitle file using the configured translation services.
        
        Args:
            input_path: Path to the input SRT file
            output_path: Path to save the translated SRT file
            cfg: Configuration object
            progress_dict: Optional dictionary to track translation progress
        """
        import pysrt
        start_time = time.time()
        
        # Initialize translation statistics
        translation_stats = {
            'source_language': cfg.get("general", "source_language", fallback="en"),
            'target_language': cfg.get("general", "target_language", fallback="en"),
            'total_lines': 0,
            'standard_critic_enabled': cfg.has_section("agent_critic") and cfg.getboolean("agent_critic", "enabled", fallback=False),
            'standard_critic_changes': 0,
            'multi_critic_enabled': cfg.has_section("multi_critic") and cfg.getboolean("multi_critic", "enabled", fallback=False),
            'translations': []
        }
        
        # Load the subtitle file
        try:
            subs = pysrt.open(input_path, encoding='utf-8')
            self.logger.info(f"Loaded subtitle file '{os.path.basename(input_path)}' with {len(subs)} lines")
            translation_stats['total_lines'] = len(subs)
        except Exception as e:
            self.logger.error(f"Error loading subtitle file: {e}")
            if progress_dict is not None:
                progress_dict["status"] = "error"
                progress_dict["message"] = f"Error loading subtitle file: {e}"
            return
        
        # Get source and target languages from config
        src_lang = cfg.get("general", "source_language", fallback="en")
        tgt_lang = cfg.get("general", "target_language", fallback="en")
        
        # Update progress dictionary if provided
        if progress_dict is not None:
            progress_dict["status"] = "translating"
            progress_dict["total_lines"] = len(subs)
            progress_dict["current_line"] = 0
        
        # Process each subtitle line
        for i, sub in enumerate(subs):
            # Skip empty lines or lines with only formatting
            if not sub.text.strip() or sub.text.strip() == '&nbsp;':
                continue
            
            # Update progress
            if progress_dict is not None:
                progress_dict["current_line"] = i + 1
                progress_dict["current"]["line_number"] = i + 1
                progress_dict["current"]["original"] = sub.text
            
            # TODO: Implement the actual translation logic
            # This would use the various call_* methods to translate each line
            # For now, we'll just log the process
            
            self.logger.info(f"Processing line {i+1}/{len(subs)}: {sub.text[:30]}...")
            
            # In a real implementation, we would:
            # 1. Get translations from various services
            # 2. Apply critic passes if enabled
            # 3. Choose the best translation
            # 4. Update the subtitle text
            
            # For demo purposes, just add the original text with a target language marker
            sub.text = f"[{tgt_lang}] {sub.text}"
            
            # Update processed lines history in progress dict
            if progress_dict is not None:
                line_record = {
                    "line_number": i + 1,
                    "original": sub.text,
                    "final": sub.text  # In real implementation, this would be the translated text
                }
                progress_dict["processed_lines"].append(line_record)
        
        # Save the translated subtitle file
        subs.save(output_path, encoding='utf-8')
        self.logger.info(f"Saved translated SRT to '{os.path.basename(output_path)}'")
        
        # Generate a translation report
        report_path = os.path.join(os.path.dirname(output_path), "translation_report.txt")
        self.generate_translation_report(translation_stats, report_path)
        self.logger.info(f"Saved detailed translation report to {report_path}")
        
        # Calculate processing time
        end_time = time.time()
        translation_stats['processing_time'] = end_time - start_time
        processing_time_seconds = end_time - start_time
        processing_time_minutes = processing_time_seconds / 60
        
        # Print final statistics
        self.logger.info("\nTRANSLATION STATISTICS:")
        self.logger.info(f"- Total lines translated: {len(subs)}")
        self.logger.info(f"- Total processing time: {processing_time_minutes:.2f} minutes ({processing_time_seconds:.2f} seconds)")
        self.logger.info(f"- Average time per line: {processing_time_seconds/len(subs):.2f} seconds")
        
        # Update progress dictionary on completion
        if progress_dict is not None:
            progress_dict["status"] = "done"
            progress_dict["message"] = f"Translation complete! {len(subs)} lines translated in {processing_time_minutes:.2f} minutes."
    
    def generate_translation_report(self, stats, output_path):
        """Generate a detailed translation report with comprehensive statistics."""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=== Subtitle Translation Report ===\n\n")
            f.write(f"Source Language: {stats['source_language']}\n")
            f.write(f"Target Language: {stats['target_language']}\n")
            f.write(f"Total Lines: {stats['total_lines']}\n")
            f.write(f"Processing Time: {stats.get('processing_time', 0):.2f} seconds\n\n")
            
            f.write("=== Translation Services ===\n")
            # In a real implementation, we would include details about which services were used
            f.write("Services used: [This would show actual services used]\n\n")
            
            f.write("=== Critic Information ===\n")
            f.write(f"Standard Critic Enabled: {stats['standard_critic_enabled']}\n")
            if stats['standard_critic_enabled']:
                f.write(f"Standard Critic Changes: {stats['standard_critic_changes']}\n")
            f.write(f"Multi-Critic Enabled: {stats['multi_critic_enabled']}\n\n")
            
            f.write("=== Sample Translations ===\n")
            # In a real implementation, we would include sample translations
            f.write("[This would show sample translations from the process]\n")

    def extract_item_name(self, filename: str) -> str:
        """Extract a clean name from a subtitle filename."""
        _SERIES_RE = re.compile(r"^(?P<title>.+?)\.S\d{2}E\d{2}", re.I)
        _MOVIE_RE = re.compile(r"^(?P<title>.+?)\.(19|20)\d{2}")
        base = os.path.splitext(filename)[0]
        m = _SERIES_RE.match(base) or _MOVIE_RE.match(base)
        return (m.group("title") if m else base).replace('.', ' ').strip()
        
    def scan_and_translate_directory(self, root_path: str, cfg=None, progress_dict=None):
        """
        Scans a directory recursively for subtitle files matching the source language
        and translates them if a target language version doesn't exist.
        
        Args:
            root_path: Directory to scan
            cfg: Configuration object
            progress_dict: Optional dictionary to track scanning progress
        """
        # Log function defaults to the class logger
        log_func = self.logger.info
        
        if cfg is None:
            self.logger.error("Configuration object is required for scanning")
            return
        
        # Constants
        SUBS_FOLDER = "subs"  # Define folder for saved subtitles
        
        # Get source and target languages from config
        source_lang = cfg.get("general", "source_language", fallback="en")
        target_lang = cfg.get("general", "target_language", fallback="da")
        
        # Language markers in filenames (e.g., .en.srt, .da.srt)
        source_marker = f".{source_lang}."
        target_marker = f".{target_lang}."
        
        log_func(f"[SCANNER] Starting scan in '{root_path}' for source '{source_marker}' and target '{target_marker}'")
        
        found_files = 0
        translated_files = 0
        skipped_files = 0
        files_to_translate = []
        
        # Scan the directory recursively
        for subdir, _, files in os.walk(root_path):
            for file in files:
                # Check if it's a source language subtitle file
                if file.endswith(".srt") and source_marker in file:
                    found_files += 1
                    source_path = os.path.join(subdir, file)
                    
                    # Construct target filename
                    target_file = file.replace(source_marker, target_marker)
                    target_path = os.path.join(SUBS_FOLDER, target_file)
                    
                    # Skip if target already exists
                    if os.path.exists(target_path):
                        log_func(f"[SCANNER] Skipping '{file}' - target file already exists")
                        skipped_files += 1
                        continue
                    
                    # Add to translation queue
                    files_to_translate.append((source_path, target_path))
                    log_func(f"[SCANNER] Added '{file}' to translation queue")
        
        log_func(f"[SCANNER] Scan complete. Found {found_files} source files needing translation. {skipped_files} skipped (target exists or pattern mismatch).")
        
        if not files_to_translate:
            log_func("[SCANNER] No files need translation.")
            if progress_dict is not None:
                progress_dict["status"] = "done"
                progress_dict["message"] = "No subtitle files need translation."
            return
        
        # Update progress before starting translation
        if progress_dict is not None:
            progress_dict["status"] = "translating"
            progress_dict["mode"] = "bulk"
            progress_dict["total_files"] = len(files_to_translate)
            progress_dict["done_files"] = 0
        
        # Translate the files
        for i, (source_path, target_path) in enumerate(files_to_translate):
            try:
                # Update progress with current file
                if progress_dict is not None:
                    progress_dict["current_file"] = os.path.basename(source_path)
                
                log_func(f"[SCANNER] Translating {i+1}/{len(files_to_translate)}: {os.path.basename(source_path)}")
                
                # Make sure the output directory exists
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                
                # Call the translate_srt method
                self.translate_srt(source_path, target_path, cfg)
                
                translated_files += 1
                
                # Update progress after file completion
                if progress_dict is not None:
                    progress_dict["done_files"] = i + 1
                
            except Exception as e:
                log_func(f"[SCANNER] Error translating '{source_path}': {e}")
        
        log_func(f"[SCANNER] Translation process finished. Translated {translated_files} files.")
        
        if progress_dict is not None:
            progress_dict["status"] = "done"
            progress_dict["message"] = f"Translation process finished. Translated {translated_files} files."
        
        return

    def parse_file(self, file_path: str) -> list:
        """
        Parse a subtitle file and return a list of subtitle dictionaries.
        
        Args:
            file_path: Path to the subtitle file
            
        Returns:
            List of dictionaries with subtitle data
        """
        import pysrt
        
        self.logger.info(f"Parsing subtitle file: {os.path.basename(file_path)}")
        
        try:
            # Load subtitles using pysrt
            subs = pysrt.open(file_path, encoding='utf-8')
            
            # Convert to list of dictionaries
            subtitle_list = []
            for sub in subs:
                if not sub.text.strip() or sub.text.strip() == '&nbsp;':
                    continue
                    
                subtitle_dict = {
                    'index': sub.index,
                    'start': str(sub.start),
                    'end': str(sub.end),
                    'text': sub.text.strip(),
                    'position': sub.position,
                }
                subtitle_list.append(subtitle_dict)
            
            self.logger.info(f"Parsed {len(subtitle_list)} subtitle entries")
            return subtitle_list
            
        except Exception as e:
            self.logger.error(f"Error parsing subtitle file: {str(e)}")
            raise
    
    def write_file(self, file_path: str, subtitles: list) -> None:
        """
        Write subtitles to a file.
        
        Args:
            file_path: Path to save the subtitle file
            subtitles: List of subtitle dictionaries
        """
        import pysrt
        
        self.logger.info(f"Writing subtitle file: {os.path.basename(file_path)}")
        
        try:
            # Create a new SubRipFile
            subs = pysrt.SubRipFile()
            
            # Convert dictionaries back to SubRipItem objects
            for subtitle in subtitles:
                item = pysrt.SubRipItem(
                    index=subtitle['index'],
                    start=pysrt.SubRipTime.from_string(subtitle['start']),
                    end=pysrt.SubRipTime.from_string(subtitle['end']),
                    text=subtitle['text']
                )
                
                # Set position if available
                if 'position' in subtitle and subtitle['position']:
                    item.position = subtitle['position']
                
                subs.append(item)
            
            # Make sure the directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Save to file
            subs.save(file_path, encoding='utf-8')
            
            self.logger.info(f"Successfully wrote {len(subtitles)} subtitles to {os.path.basename(file_path)}")
            
        except Exception as e:
            self.logger.error(f"Error writing subtitle file: {str(e)}")
            raise