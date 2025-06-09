import os
import re
import time
import json
import logging
import requests
from typing import Dict, List, Optional, Tuple, Any, Callable # Add Callable
import sys
import importlib.util
import copy # Add copy for deepcopy

# Import live_translation_viewer if available
try:
    # First, try to import directly (if module is in path)
    try:
        from live_translation_viewer import display_translation_status
    except ImportError:
        # If that fails, try to import from parent directory
        spec = importlib.util.spec_from_file_location(
            "live_translation_viewer", 
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "live_translation_viewer.py")
        )
        if spec and spec.loader:
            live_translation_viewer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(live_translation_viewer)
            display_translation_status = live_translation_viewer.display_translation_status
        else:
            # Fallback display function if module can't be imported
            def display_translation_status(line_number, original, translations, current_result=None, first_pass=None, critic=None, final=None):
                print(f"Line {line_number}: \"{original}\" -> \"{final or current_result or ''}\"")
except Exception as e:
    # Fallback display function if any error occurs
    def display_translation_status(line_number, original, translations, current_result=None, first_pass=None, critic=None, final=None):
        print(f"Line {line_number}: \"{original}\" -> \"{final or current_result or ''}\"")

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
        self.config = None
        
    def set_config(self, config):
        """Set the configuration object for this processor."""
        self.config = config
        
    def get_iso_code(self, language_name: str) -> str:
        """Convert a language name to its ISO code."""
        language_name = language_name.lower().strip('"\' ')
        return LANGUAGE_MAPPING.get(language_name, language_name)

    def detect_and_extract_embedded_subtitles(self, video_file_path: str, output_dir: str, 
                                              source_lang_code: Optional[str] = None) -> List[str]:
        """
        Detect and extract embedded subtitles from a video file.
        
        Args:
            video_file_path: Path to the video file
            output_dir: Directory to save extracted subtitle files
            source_lang_code: Source language code to filter subtitles (optional)
            
        Returns:
            List of paths to extracted subtitle files
        """
        import subprocess
        import shlex
        import re
        
        self.logger.info(f"Detecting embedded subtitles in: {os.path.basename(video_file_path)}")
        self.logger.info(f"Source language code to match: '{source_lang_code}'")
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Get setting for extraction mode from config if provided, otherwise default to False
        extract_all_for_debug = False
        if self.config:
            extract_all_for_debug = self.config.getboolean('extraction', 'extract_all_subtitles', fallback=False)
        if self.config:
            extract_all_for_debug = self.config.getboolean('extraction', 'extract_all_subtitles', fallback=False)
        
        # Use a more detailed ffprobe command to get complete information
        try:
            # Get a detailed dump of the video file structure for debugging
            ffprobe_debug_cmd = f"ffprobe -v error -show_format -show_streams {shlex.quote(video_file_path)}"
            self.logger.debug(f"Running debug ffprobe command: {ffprobe_debug_cmd}")
            
            try:
                debug_output = subprocess.check_output(
                    ffprobe_debug_cmd, 
                    shell=True, 
                    stderr=subprocess.STDOUT
                ).decode('utf-8')
                
                # Log full file structure for debugging
                self.logger.debug(f"Full video file structure:\n{debug_output}")
            except Exception as e:
                self.logger.warning(f"Debug probe failed: {e}")
            
            # Now run the actual subtitle detection command
            ffprobe_all_cmd = f"ffprobe -v quiet -print_format json -show_streams -select_streams s {shlex.quote(video_file_path)}"
            self.logger.debug(f"Running ffprobe command: {ffprobe_all_cmd}")
            
            ffprobe_all_output = subprocess.check_output(
                ffprobe_all_cmd, 
                shell=True, 
                stderr=subprocess.STDOUT
            ).decode('utf-8')
            
            # Log raw ffprobe output for debugging
            self.logger.debug(f"Raw ffprobe output: {ffprobe_all_output}")
            
            # Parse the output to get all subtitle streams
            all_subtitles_info = json.loads(ffprobe_all_output)
            
            # Log total number of subtitle streams found
            if 'streams' in all_subtitles_info and all_subtitles_info['streams']:
                stream_count = len(all_subtitles_info['streams'])
                self.logger.info(f"Found {stream_count} subtitle streams in the video file")
                
                # Log detail about each stream for diagnostic purposes
                for idx, stream in enumerate(all_subtitles_info['streams']):
                    stream_idx = stream.get('index')
                    stream_lang = stream.get('tags', {}).get('language', 'und')
                    codec_name = stream.get('codec_name', 'unknown')
                    title = stream.get('tags', {}).get('title', 'untitled')
                    
                    # ENHANCEMENT: Log complete stream info as JSON for debugging
                    self.logger.info(f"Stream {stream_idx} details: lang={stream_lang}, codec={codec_name}, title={title}")
                    self.logger.debug(f"FULL STREAM DATA: {json.dumps(stream, indent=2)}")
                
                # If we found streams but no source language provided, or it's empty, extract all
                extract_all = source_lang_code is None or source_lang_code == "" or extract_all_for_debug
                
                # Process each subtitle stream
                extracted_files = []
                
                for stream in all_subtitles_info['streams']:
                    stream_idx = stream.get('index')
                    stream_lang = stream.get('tags', {}).get('language', 'und')
                    codec_name = stream.get('codec_name', 'unknown')
                    codec_type = stream.get('codec_type')
                    title = stream.get('tags', {}).get('title', 'No title')
                    
                    # Skip if not a subtitle stream
                    if codec_type != 'subtitle':
                        self.logger.debug(f"Stream {stream_idx} is not a subtitle stream, type: {codec_type}")
                        continue
                    
                    # ENHANCEMENT: For debugging, print whether this stream would be skipped normally
                    if not extract_all and stream_lang != source_lang_code and stream_lang != 'und':
                        self.logger.info(f"Stream {stream_idx} language '{stream_lang}' doesn't match source '{source_lang_code}', but extracting anyway for debug")
                    
                    # Format output filename
                    video_basename = os.path.basename(video_file_path)
                    video_name = os.path.splitext(video_basename)[0]
                    title_suffix = f".{re.sub(r'[^\w\-\.]', '_', title)}" if title and title != "No title" else ""
                    out_filename = f"{video_name}.{stream_lang}.stream{stream_idx}{title_suffix}.srt"
                    out_path = os.path.join(output_dir, out_filename)
                    
                    # Choose extraction method based on codec
                    extraction_methods = []
                    
                    # Method 1: Standard extraction (works for most text-based formats)
                    extraction_methods.append({
                        'name': 'standard',
                        'cmd': f"ffmpeg -i {shlex.quote(video_file_path)} -map 0:{stream_idx} -c:s srt {shlex.quote(out_path)} -y"
                    })
                    
                    # Method 2: Extract with format specified (sometimes helps)
                    extraction_methods.append({
                        'name': 'format_specified',
                        'cmd': f"ffmpeg -i {shlex.quote(video_file_path)} -map 0:{stream_idx} -f srt {shlex.quote(out_path)} -y"
                    })
                    
                    # Method 3: Extract to ASS then convert (for some complex formats)
                    temp_ass_path = out_path.replace('.srt', '.ass')
                    extraction_methods.append({
                        'name': 'ass_conversion',
                        'cmd': f"ffmpeg -i {shlex.quote(video_file_path)} -map 0:{stream_idx} {shlex.quote(temp_ass_path)} -y && ffmpeg -i {shlex.quote(temp_ass_path)} {shlex.quote(out_path)} -y && rm {shlex.quote(temp_ass_path)}"
                    })
                    
                    # Try each method until one works
                    success = False
                    for method in extraction_methods:
                        if success:
                            break
                            
                        self.logger.info(f"Trying extraction method '{method['name']}' for stream {stream_idx} ({stream_lang}/{codec_name})")
                        self.logger.debug(f"Command: {method['cmd']}")
                        
                        try:
                            # Run the extraction command with increased verbosity
                            process = subprocess.run(
                                method['cmd'],
                                shell=True,
                                check=False,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                timeout=60
                            )
                            
                            # Log complete output for debugging
                            stdout = process.stdout.decode('utf-8', errors='ignore')
                            stderr = process.stderr.decode('utf-8', errors='ignore')
                            
                            self.logger.debug(f"STDOUT: {stdout}")
                            self.logger.debug(f"STDERR: {stderr}")
                            
                            if process.returncode != 0:
                                self.logger.warning(f"Method '{method['name']}' failed with return code {process.returncode}")
                                continue
                            
                            # Check if file was created and has content
                            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                                # Validate SRT file by checking for timestamps
                                with open(out_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read()
                                    # Log the first few lines of content for debugging
                                    self.logger.debug(f"First 200 chars of extracted content: {content[:200]}")
                                    
                                    # Basic check for SRT format: contains timestamps like 00:00:00,000 --> 00:00:00,000
                                    if re.search(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}', content):
                                        self.logger.info(f"Successfully extracted subtitles to {out_filename} using method '{method['name']}'")
                                        extracted_files.append(out_path)
                                        success = True
                                    else:
                                        self.logger.warning(f"Extracted file doesn't have valid SRT format. Content starts with: {content[:100]}")
                                        # Clean up invalid file
                                        os.remove(out_path)
                            else:
                                if os.path.exists(out_path):
                                    self.logger.warning(f"Extraction produced empty file ({os.path.getsize(out_path)} bytes)")
                                    os.remove(out_path)
                                else:
                                    self.logger.warning(f"Extraction did not produce a file at {out_path}")
                                
                        except subprocess.TimeoutExpired:
                            self.logger.error(f"Method '{method['name']}' timed out")
                            continue
                        except Exception as e:
                            self.logger.error(f"Method '{method['name']}' failed with error: {e}")
                            continue
                    
                    if not success:
                        self.logger.warning(f"All extraction methods failed for stream {stream_idx}")
                
                # If we extracted some files, return them
                if extracted_files:
                    return extracted_files
                
                # If we get here, no streams were successfully extracted
                # Try a simplified method as last resort
                self.logger.info("Trying simplified extraction approach")
                simplified_extract_cmd = f"ffmpeg -i {shlex.quote(video_file_path)} -map 0:s -c:s srt {shlex.quote(os.path.join(output_dir, os.path.splitext(os.path.basename(video_file_path))[0]))}_%d.srt -y"
                self.logger.debug(f"Simplified command: {simplified_extract_cmd}")
                
                try:
                    process = subprocess.run(
                        simplified_extract_cmd,
                        shell=True,
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=120
                    )
                    
                    # Log complete output
                    stdout = process.stdout.decode('utf-8', errors='ignore')
                    stderr = process.stderr.decode('utf-8', errors='ignore')
                    self.logger.debug(f"Simplified STDOUT: {stdout}")
                    self.logger.debug(f"Simplified STDERR: {stderr}")
                    
                    # Check for created files
                    import glob
                    pattern = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(video_file_path))[0]}_*.srt")
                    extracted_files = []
                    
                    for file_path in glob.glob(pattern):
                        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                            # Validate file
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                                if re.search(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}', content):
                                    self.logger.info(f"Simplified extraction success: {os.path.basename(file_path)}")
                                    extracted_files.append(file_path)
                                else:
                                    self.logger.warning(f"Simplified extraction produced invalid file: {os.path.basename(file_path)}")
                                    os.remove(file_path)
                    
                    if extracted_files:
                        return extracted_files
                    
                except Exception as e:
                    self.logger.error(f"Simplified extraction failed: {e}")
                
                # Last resort: Try direct copy of subtitle stream
                self.logger.info("Trying direct stream copy as last resort")
                for stream in all_subtitles_info['streams']:
                    stream_idx = stream.get('index')
                    if stream.get('codec_type') == 'subtitle':
                        direct_copy_cmd = f"ffmpeg -v verbose -i {shlex.quote(video_file_path)} -map 0:{stream_idx} -c copy {shlex.quote(os.path.join(output_dir, f'direct_copy_{stream_idx}.{stream.get('codec_name')}'))} -y"
                        self.logger.debug(f"Direct copy command: {direct_copy_cmd}")
                        
                        try:
                            process = subprocess.run(
                                direct_copy_cmd,
                                shell=True,
                                check=False,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                timeout=30
                            )
                            
                            # Log output
                            stderr = process.stderr.decode('utf-8', errors='ignore')
                            self.logger.debug(f"Direct copy STDERR: {stderr}")
                        except Exception as e:
                            self.logger.error(f"Direct copy failed: {e}")
                
                self.logger.warning(f"All extraction methods failed for all streams. No subtitles extracted.")
                self.logger.info(f"No matching subtitles found in {os.path.basename(video_file_path)}")
                return []
            else:
                self.logger.info(f"No subtitle streams found in {os.path.basename(video_file_path)}")
                return []
        except Exception as e:
            self.logger.error(f"Error detecting subtitle streams: {e}")
            import traceback
            self.logger.error(f"Stack trace: {traceback.format_exc()}")
            return []
    
    def is_video_file(self, file_path: str) -> bool:
        """
        Check if a file is a video file based on its extension.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if it's a video file, False otherwise
        """
        video_extensions = {
            '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', 
            '.m4v', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts'
        }
        
        _, ext = os.path.splitext(file_path.lower())
        return ext in video_extensions

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
        url = f"{server_url.rstrip('/')}/{endpoint_path.lstrip('/')}"

        # Start payload with mandatory fields
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {} # Initialize options dictionary
        }

        # Set default temperature initially
        current_temperature = temperature

        # Load options from config if available
        if cfg is not None and cfg.has_section("ollama"):
            # Override temperature if specified in config
            current_temperature = cfg.getfloat("ollama", "temperature", fallback=temperature)

            # Initialize options dict with the determined temperature
            payload["options"]["temperature"] = current_temperature

            # Add other parameters ONLY if they exist and have valid values in the config
            optional_params = {
                "num_gpu": "getint",
                "num_thread": "getint",
                "num_ctx": "getint",
                "use_mmap": "getboolean",
                "use_mlock": "getboolean"
            }

            for param, getter_method in optional_params.items():
                if cfg.has_option("ollama", param):
                    try:
                        # Check if the value is actually set and not commented out
                        value = cfg.get("ollama", param, fallback=None)
                        if value is not None and str(value).strip() != "":
                            # Use the appropriate getter method (getint or getboolean)
                            getter = getattr(cfg, getter_method)
                            value = getter("ollama", param)
                            payload["options"][param] = value
                            self.logger.debug(f"Adding Ollama option from config: {param} = {value}")
                    except ValueError:
                        self.logger.warning(f"Invalid value for '{param}' in config. Ignoring.")
                    except Exception as e:
                         self.logger.warning(f"Could not read Ollama option '{param}' from config: {e}. Ignoring.")
        else:
             # If no config or no [ollama] section, just set the default temperature
             payload["options"]["temperature"] = current_temperature


        # Log the final options being sent
        options_list = ', '.join([f"{k}: {v}" for k, v in payload["options"].items()])
        self.logger.debug(f"Calling Ollama: POST {url} | Model: {model} | Options: {options_list}")


        try:
            # Increased timeout to 120 seconds to allow for longer processing times
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()

            # Ollama response has a 'response' field with the generated text
            if "response" in result:
                return result["response"].strip()
            return ""
        except requests.exceptions.Timeout:
            self.logger.error(f"Ollama API request timed out after 120 seconds")
            return ""
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Ollama API error: {e}")
            # Log the response body if available for more details
            try:
                error_details = response.text
                self.logger.error(f"Ollama API response body: {error_details}")
            except:
                pass # Ignore if response object doesn't exist or has no text
            return ""
    
    def sanitize_text(self, text: str) -> str:
        """Clean subtitle text by removing HTML tags and standardizing special content."""
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        text = re.sub(r'<[^>]*>', '', text)
        text = re.sub(r'\[(.*?)\]', r'#BRACKET_OPEN#\1#BRACKET_CLOSE#', text)
        text = re.sub(r' +', ' ', text)
        text.replace('\r\n', '\n').replace('\r', '\n')
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
        # Restore brackets (case-insensitive)
        text = re.sub(r'#BRACKET_OPEN#', '[', text, flags=re.IGNORECASE)
        text = re.sub(r'#BRACKET_CLOSE#', ']', text, flags=re.IGNORECASE)
        
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
    
    def translate_srt(self, input_path: str, output_path: str, cfg: Any, 
                      progress_dict: Optional[Dict[str, Any]] = None, 
                      save_progress_state_func: Optional[Callable[[], None]] = None):
        """Translate subtitle file with proper Ollama waiting and live status."""
        import pysrt
        from py.translation_service import TranslationService
        
        # Store config for use in other methods
        self.set_config(cfg)
        
        start_time = time.time() # Initialize overall start time
        
        # Initialize progress history if needed
        if progress_dict is not None and "processed_lines" not in progress_dict:
            progress_dict["processed_lines"] = []
            
        # Make sure current dict is initialized
        if progress_dict is not None and "current" not in progress_dict:
            progress_dict["current"] = {}
            
        # Log the progress dictionary structure at start
        if progress_dict is not None:
            self.logger.debug(f"Progress dict initialized: {json.dumps(progress_dict, default=str)}")

        try:
            # Import display function - ensure this works first
            try:
                from live_translation_viewer import display_translation_status
                has_display = True
                self.logger.info("Live translation viewer imported successfully.")
            except ImportError as e:
                has_display = False
                self.logger.warning(f"Could not import live_translation_viewer - live display disabled: {e}")

            # Start processing
            self.logger.info(f"Parsing subtitle file: {os.path.basename(input_path)}")
            subs = pysrt.open(input_path, encoding='utf-8')
            total_lines = len(subs)
            self.logger.info(f"Parsed {total_lines} subtitle entries")
            
            # Setup translation service
            translation_service = TranslationService(cfg, self.logger)
            
            # Get languages
            source_lang = cfg.get("general", "source_language", fallback="en")
            target_lang = cfg.get("general", "target_language", fallback="da")
            
            # Initialize critics if enabled
            agent_critic_enabled = cfg.getboolean("agent_critic", "enabled", fallback=False)
            critic_service = None
            if agent_critic_enabled:
                try:
                    from py.critic_service import CriticService
                    critic_service = CriticService(cfg, self.logger)
                    self.logger.info("Agent Critic enabled and initialized")
                except Exception as e:
                    self.logger.error(f"Failed to initialize critic service: {e}")
            
            # Get context size from config
            context_size_before = cfg.getint("general", "context_size_before", fallback=15)
            context_size_after = cfg.getint("general", "context_size_after", fallback=15)
            
            # Get TMDB information if enabled
            media_info = None
            if cfg.getboolean("tmdb", "enabled", fallback=False):
                try:
                    # Extract show/movie name from filename
                    file_basename = os.path.basename(input_path)
                    media_title = self.extract_item_name(file_basename)
                    self.logger.info(f"Fetching TMDB data for: {media_title}")
                    
                    # Extract season and episode numbers if present
                    season_num, episode_num = self.extract_season_episode(file_basename)
                    if season_num and episode_num:
                        self.logger.info(f"Detected S{season_num:02d}E{episode_num:02d} in filename")
                    
                    # Get media information from TMDB, passing the filename as well
                    media_info = translation_service.get_media_info(media_title, original_filename=file_basename, 
                                                                  season=season_num, episode=episode_num)
                    if media_info:
                        self.logger.info(f"TMDB data found for: {media_info.get('title', '')}")
                        if media_info.get('has_episode_data', False):
                            self.logger.info(f"Episode info found: {media_info.get('episode_title', 'Unknown')}")
                    else:
                        self.logger.warning(f"No TMDB data found for: {media_title}")
                except Exception as e:
                    self.logger.error(f"Error fetching TMDB data: {str(e)}")
            
            # Update global progress information before starting the line-by-line translation
            if progress_dict is not None:
                progress_dict["status"] = "translating"
                progress_dict["current_file"] = os.path.basename(input_path)
                progress_dict["total_lines"] = total_lines
                progress_dict["current_line"] = 0
                # Save progress state if there's a save function
                if save_progress_state_func:
                    save_progress_state_func()
                # Manually log the progress dict structure
                self.logger.debug(f"Progress dict before translation: {json.dumps(progress_dict, default=str)}")
            
            # Initialize line history in progress_dict
            if progress_dict is not None:
                progress_dict["line_history"] = []

            # Process each subtitle line
            for i, sub in enumerate(subs):
                line_number = i + 1
                line_start_time = time.time()  # Track per-line timing
                
                # Skip empty lines
                if not sub.text.strip() or sub.text.strip() == '&nbsp;':
                    continue
                
                original_text = self.preprocess_subtitle(sub.text)
                
                # Initialize data for this line
                translations = {}
                first_pass = None
                critic_feedback_for_display: Optional[str] = None 
                critic_revised_text_for_display: Optional[str] = None
                critic_made_change_for_display: bool = False
                final_result = None
                
                # Initialize timing dict for this line
                timing = {
                    "start": time.time(),
                    "preprocessing": 0,
                    "first_pass": 0,
                    "critic": 0,
                    "total": 0
                }
                
                # Update progress dictionary *before* translation starts for this line
                if progress_dict is not None:
                    # ... (existing progress_dict updates for "current") ...
                    # Ensure 'current' is initialized
                    if "current" not in progress_dict:
                        progress_dict["current"] = {}
                    progress_dict["current"].update({
                        "line_number": line_number,
                        "original": original_text,
                        "suggestions": {},
                        "first_pass": None,
                        "standard_critic": None,
                        "final": None,
                        "timing": timing # Make sure timing is initialized before this
                    })
                    # ... (existing logging and save_progress_state_func call) ...

                # Build context from surrounding subtitles
                context_before = []
                for j in range(max(0, i - context_size_before), i):
                    context_before.append(f"Line {j+1}: {subs[j].text}")
                
                context_after = []
                for j in range(i + 1, min(len(subs), i + 1 + context_size_after)):
                    context_after.append(f"Line {j+1}: {subs[j].text}")
                
                context_text = ""
                if context_before:
                    context_text += "PREVIOUS LINES:\n" + "\n".join(context_before) + "\n\n"
                if context_after:
                    context_text += "FOLLOWING LINES:\n" + "\n".join(context_after)
                
                # Get special meanings from progress_dict if available
                special_meanings = None
                if progress_dict is not None and "special_meanings" in progress_dict:
                    special_meanings = progress_dict["special_meanings"]
                    if special_meanings:
                        self.logger.info(f"Using {len(special_meanings)} special word meanings for translation")
                
                # Record time before first pass translation
                first_pass_start = time.time()
                
                # Pass context, media_info, and special meanings to translation service
                translation_details = translation_service.translate(
                    original_text, 
                    source_lang, 
                    target_lang,
                    context=context_text,
                    media_info=media_info,
                    special_meanings=special_meanings  # Pass special meanings to translation service
                )
                
                # Calculate first pass timing
                timing["first_pass"] = time.time() - first_pass_start
                
                # Extract results
                translations = translation_details.get("collected_translations", {})
                first_pass = translation_details.get("first_pass_text")
                current_result = translation_details.get("final_text") # This is the result after the main translation logic
                
                # Update progress dict with collected translations and first pass
                if progress_dict is not None:
                    progress_dict["current"]["suggestions"] = translations
                    progress_dict["current"]["first_pass"] = first_pass
                    progress_dict["current"]["timing"]["first_pass"] = timing["first_pass"]
                    # Save progress state after first pass if there's a save function
                    if save_progress_state_func:
                        save_progress_state_func()
                    # Manually log the translation status after first pass
                    self.logger.debug(f"Line {line_number} after first pass: {first_pass}")

                # Display initial status (original, suggestions, first pass) - Only if using live viewer
                if has_display:
                    display_translation_status(
                        line_number, 
                        original_text, 
                        translations, 
                        None, # current_result not shown yet
                        first_pass
                    )
                # Fallback console print is moved to the end
                
                # Apply critic if enabled and we have a result
                critic_result_str = None # Store critic's string result or None
                critic_feedback = None # Store critic's feedback if available
                critic_changed = False
                
                # Record critic start time
                critic_start_time = time.time()
                
                if current_result and agent_critic_enabled and critic_service:
                    self.logger.info("Applying critic to translation")
                    critic_eval_result = critic_service.evaluate_translation(
                        original_text, current_result, source_lang, target_lang
                    )
                    
                    # Check if critic returned a dict with score and feedback
                    if isinstance(critic_eval_result, dict):
                        critic_feedback_for_display = critic_eval_result.get('feedback', 'No feedback provided.')
                        if 'revised_translation' in critic_eval_result and critic_eval_result['revised_translation'] is not None:
                             critic_revised_text_for_display = critic_eval_result['revised_translation']
                             critic_made_change_for_display = critic_revised_text_for_display != current_result
                             self.logger.info(f"Critic suggested revision: {critic_revised_text_for_display}")
                        else:
                             # Corrected logger call with proper string formatting for score
                             self.logger.info(f"Critic evaluation: Score {critic_eval_result.get('score', 'N/A')}, Feedback: {critic_feedback_for_display}")
                             critic_revised_text_for_display = None 
                             critic_made_change_for_display = False
                    else:
                        self.logger.warning(f"Critic returned unexpected result format: {critic_eval_result}")
                        critic_revised_text_for_display = None
                        critic_made_change_for_display = False
                        critic_feedback_for_display = f"Unexpected result: {critic_eval_result}"
                    
                    timing["critic"] = time.time() - critic_start_time

                    if progress_dict is not None:
                        progress_dict["current"]["standard_critic"] = {
                            "feedback": critic_feedback_for_display,
                            "changed_translation": critic_made_change_for_display,
                            "result_after_critic": critic_revised_text_for_display
                        }
                        progress_dict["current"]["timing"]["critic"] = timing["critic"]
                        # Save progress state after critic evaluation if there's a save function
                        if save_progress_state_func:
                            save_progress_state_func()
                    
                    # Display status after critic - Only if using live viewer
                    if has_display:
                        display_translation_status(
                            line_number,
                            original_text,
                            translations,
                            None, # current_result not shown yet
                            first_pass,
                            critic_revised_text_for_display # Display the revised text or None
                        )
                    
                    if critic_revised_text_for_display and critic_made_change_for_display:
                        current_result = critic_revised_text_for_display 
                        self.logger.info("Using critic's revised translation.")
                    elif critic_revised_text_for_display:
                         self.logger.info("Critic agreed or provided same translation.")
                
                # Final result is the current result after all processing
                final_result = current_result
                
                # Calculate total time for this line
                timing["total"] = time.time() - line_start_time
                
                # Update progress dict with final result and timing
                if progress_dict is not None:
                    progress_dict["current"]["final"] = final_result
                    progress_dict["current"]["timing"]["total"] = timing["total"]
                    # Save progress state after final result if there's a save function
                    if save_progress_state_func:
                        save_progress_state_func()

                # Fallback console print for this line (if not using live viewer or as an addition)
                separator = "-" * 60
                print(separator)
                print(f"Line {line_number}:")
                print(f"  Original: \"{original_text}\"")
                # Print collected translations
                for service_name, translation_text in translations.items():
                    print(f"  {service_name}: \"{translation_text}\"")
                # Print first pass result (e.g., from Ollama final)
                if first_pass:
                    print(f"  First pass: \"{first_pass}\" ({timing['first_pass']:.2f}s)")
                # Print critic feedback if available
                if critic_feedback_for_display: 
                    change_indicator = " (REVISED)" if critic_made_change_for_display and critic_revised_text_for_display else ""
                    print(f"  Critic: \"{critic_feedback_for_display}\"{change_indicator} ({timing['critic']:.2f}s)")
                    if critic_revised_text_for_display and critic_made_change_for_display:
                        print(f"    -> Revision: \"{critic_revised_text_for_display}\"")
                # Print final result
                if final_result:
                    print(f"  Final: \"{final_result}\" (Total: {timing['total']:.2f}s)")
                print(separator) # Print separator at the end for fallback
                
                # Update subtitle text
                if final_result:
                    sub.text = self.postprocess_translation(final_result)
                else:
                    # If translation failed completely, keep original but log warning
                    self.logger.warning(f"Translation failed for line {line_number}, keeping original text: {original_text}")
                    sub.text = original_text # Keep original if final_result is None or empty

                # Accumulate history for the current line
                if progress_dict is not None:
                    current_line_snapshot = copy.deepcopy(progress_dict.get("current", {}))
                    # Ensure all data is present; 'current' should already have most of it
                    current_line_snapshot['line_number'] = line_number # Redundant if already in 'current' but safe
                    current_line_snapshot['original'] = original_text
                    current_line_snapshot['suggestions'] = translations # From translation_service.translate
                    current_line_snapshot['first_pass'] = first_pass # From translation_service.translate
                    # Store detailed critic info for history
                    current_line_snapshot['standard_critic'] = {
                        "feedback": critic_feedback_for_display,
                        "revised_text": critic_revised_text_for_display,
                        "made_change": critic_made_change_for_display
                    } if agent_critic_enabled and critic_service else None
                    current_line_snapshot['final'] = final_result
                    
                    if "line_history" not in progress_dict: 
                        progress_dict["line_history"] = []
                    progress_dict["line_history"].append(current_line_snapshot)
                    
                    # Optionally, save progress more frequently if desired, or rely on existing saves
                    if save_progress_state_func:
                        save_progress_state_func()

            # After loop, update overall status to completed (or error if applicable)
            total_process_time = time.time() - start_time # Define total_process_time
            if progress_dict is not None:
                progress_dict["status"] = "completed"
                progress_dict["message"] = f"Translation completed for {os.path.basename(input_path)} in {total_process_time:.2f}s"
                progress_dict["current_line"] = total_lines
                progress_dict["total_process_time"] = total_process_time
                # Store output path for reference
                progress_dict["output_path"] = output_path
                # Save final progress state if there's a save function
                if save_progress_state_func:
                    save_progress_state_func()
                # Log final progress state
                self.logger.debug(f"Translation complete. Final progress state: {json.dumps(progress_dict, default=str)}")

            subs.save(output_path, encoding='utf-8')
            self.logger.info(f"Successfully translated and saved: {output_path}")

        except Exception as e:
            self.logger.error(f"Error translating subtitle file {input_path}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # Update progress status on error
            if progress_dict is not None:
                progress_dict["status"] = "failed"
                progress_dict["message"] = f"Error translating subtitle: {e}"
                if save_progress_state_func:
                    save_progress_state_func()
        finally:
            end_time = time.time()
            # Check if start_time was defined (it should be now)
            if 'start_time' in locals():
                time_taken = f"{end_time - start_time:.2f}s"
                self.logger.info(f"Translation process for {os.path.basename(input_path)} finished in {time_taken}.")
            else:
                self.logger.warning(f"Translation process for {os.path.basename(input_path)} finished, but start_time was not recorded.")
    
    def _get_language_full_name(self, language_code):
        """Get the full language name from a language code."""
        # Reverse language mapping
        reverse_mapping = {v: k.capitalize() for k, v in LANGUAGE_MAPPING.items()}
        return reverse_mapping.get(language_code, language_code)

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
        """Extract a clean name from a subtitle filename.
        
        This function attempts to extract show or movie names 
        from common subtitle filename patterns.
        
        Args:
            filename: The subtitle filename
            
        Returns:
            A cleaned name suitable for TMDB search
        """
        # Common patterns for TV shows (S01E01) and movies (YEAR)
        _SERIES_RE = re.compile(r"^(?P<title>.+?)\.S\d{2}E\d{2}", re.I)
        _MOVIE_RE = re.compile(r"^(?P<title>.+?)\.(19|20)\d{2}")
        
        # Strip file extension and directory path
        base = os.path.basename(filename)
        base = os.path.splitext(base)[0]
        
        # Try to match as a TV show first, then as a movie
        m = _SERIES_RE.match(base) or _MOVIE_RE.match(base)
        
        if m:
            # Get the title from the match and clean it
            # Replace both dots AND underscores with spaces
            clean_name = m.group("title").replace('.', ' ').replace('_', ' ').strip()
            self.logger.debug(f"Extracted media name '{clean_name}' from filename '{filename}'")
            return clean_name
        
        # Fallback: just clean up the filename as best we can
        # Replace both dots AND underscores with spaces
        clean_name = base.replace('.', ' ').replace('_', ' ').split(' ')[0].strip()
        self.logger.debug(f"No pattern match - using cleaned name '{clean_name}' from filename '{filename}'")
        return clean_name

    def extract_season_episode(self, filename: str) -> tuple:
        """Extract season and episode numbers from a subtitle filename.
        
        Args:
            filename: The subtitle filename
            
        Returns:
            Tuple of (season_num, episode_num) or (None, None) if not found
        """
        # Look for common S01E01 pattern
        season_episode_match = re.search(r'S(\d{1,2})E(\d{1,2})', filename, re.IGNORECASE)
        
        if season_episode_match:
            season_num = int(season_episode_match.group(1))
            episode_num = int(season_episode_match.group(2))
            self.logger.debug(f"Extracted S{season_num:02d}E{episode_num:02d} from filename '{filename}'")
            return (season_num, episode_num)
        
        # Alternative formats could be added here (e.g., "1x01", "Season 1 Episode 1")
        
        self.logger.debug(f"No season/episode pattern found in filename '{filename}'")
        return (None, None)
        
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