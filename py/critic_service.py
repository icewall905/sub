import os
import json
import requests
import time
import re
from typing import Dict, List, Any, Optional, Union

class CriticService:
    """
    Service for evaluating the quality of translations using local LLM services (Ollama or LM Studio).
    This service analyzes translation quality, provides scores, and generates
    quality reports for subtitle translations.
    """
    
    def __init__(self, config, logger):
        """
        Initialize the critic service.
        
        Args:
            config: The configuration object from ConfigManager
            logger: The application logger
        """
        self.config = config
        self.logger = logger
        
        # Get critic configuration
        # Read from 'agent_critic' section instead of 'critic'
        self.enabled = config.getboolean('agent_critic', 'enabled', fallback=False)
        self.service = config.get('agent_critic', 'service', fallback='ollama')  # Default to ollama instead of auto
        self.model = config.get('agent_critic', 'model', fallback=config.get('ollama', 'model', fallback='llama3')) # Read model from agent_critic first
        self.temperature = config.getfloat('agent_critic', 'temperature', fallback=0.1)
        self.min_score = config.getfloat('agent_critic', 'min_score', fallback=0.6) # Assuming min_score might be defined here
        self.generate_report = config.getboolean('agent_critic', 'generate_report', fallback=False) # Assuming generate_report might be defined here
        
        # Check for LM Studio configuration
        self.lmstudio_enabled = config.has_section('lmstudio') and config.getboolean('lmstudio', 'enabled', fallback=False)
        
        # Only auto-detect if explicitly set to 'auto'
        if self.service == 'auto':
            if self.lmstudio_enabled:
                self.service = 'lmstudio'
                self.logger.info("Auto-detected LM Studio as the critic service")
            else:
                self.service = 'ollama'
                self.logger.info("Auto-detected Ollama as the critic service")
        
        # Get LM Studio-specific configuration if enabled
        if self.lmstudio_enabled or self.service == 'lmstudio':
            self.lmstudio_server_url = config.get('lmstudio', 'server_url', fallback='http://localhost:1234')
            self.lmstudio_model = config.get('lmstudio', 'model', fallback=self.model)
            # Build the complete API URL for LM Studio
            self.lmstudio_api_url = f"{self.lmstudio_server_url.rstrip('/')}/v1/chat/completions"
        
        # Get Ollama-specific configuration
        self.ollama_server_url = config.get('ollama', 'server_url', fallback='http://localhost:11434')
        self.ollama_endpoint = config.get('ollama', 'endpoint', fallback='/api/generate')
        
        # Remove leading slash from endpoint if present to avoid double slashes
        if self.ollama_endpoint.startswith('/'):
            self.ollama_endpoint = self.ollama_endpoint[1:]
            
        # Build the complete API URL
        self.ollama_api_url = f"{self.ollama_server_url.rstrip('/')}/{self.ollama_endpoint}"
        
        # Performance parameters - properly handle commented-out options
        if config.has_option('ollama', 'num_gpu'):
            value = config.get('ollama', 'num_gpu', fallback=None)
            if value is not None and str(value).strip() != "":
                self.num_gpu = config.getint('ollama', 'num_gpu')
            else:
                self.num_gpu = None
        else:
            self.num_gpu = None

        if config.has_option('ollama', 'num_thread'):
            value = config.get('ollama', 'num_thread', fallback=None)
            if value is not None and str(value).strip() != "":
                self.num_thread = config.getint('ollama', 'num_thread')
            else:
                self.num_thread = None
        else:
            self.num_thread = None
            
        if config.has_option('ollama', 'num_ctx'):
            value = config.get('ollama', 'num_ctx', fallback=None)
            if value is not None and str(value).strip() != "":
                self.num_ctx = config.getint('ollama', 'num_ctx')
            else:
                self.num_ctx = None
        else:
            self.num_ctx = None

        if config.has_option('ollama', 'use_mmap'):
            value = config.get('ollama', 'use_mmap', fallback=None)
            if value is not None and str(value).strip() != "":
                self.use_mmap = config.getboolean('ollama', 'use_mmap')
            else:
                self.use_mmap = None
        else:
            self.use_mmap = None

        if config.has_option('ollama', 'use_mlock'):
            value = config.get('ollama', 'use_mlock', fallback=None)
            if value is not None and str(value).strip() != "":
                self.use_mlock = config.getboolean('ollama', 'use_mlock')
            else:
                self.use_mlock = None
        else:
            self.use_mlock = None
        
        # Initialize cache for evaluation results
        self.evaluation_cache = {}
        
        self.logger.info(f"CriticService initialized. Enabled: {self.enabled}, Service: {self.service}, Model: {self.model}")
        if self.service == 'ollama':
            self.logger.debug(f"Ollama API URL: {self.ollama_api_url}")
        elif self.service == 'lmstudio':
            self.logger.debug(f"LM Studio API URL: {self.lmstudio_api_url}")
    
    def evaluate_translation(self, source_text: str, translated_text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """
        Evaluate the quality of a translation.
        
        Args:
            source_text: The original text in the source language
            translated_text: The translated text
            source_lang: The source language code
            target_lang: The target language code
            
        Returns:
            Dictionary containing evaluation results including score and feedback
        """
        if not self.enabled:
            return {"score": 1.0, "feedback": "Translation quality evaluation is disabled"}
        
        # Generate a cache key
        cache_key = f"{source_lang}:{target_lang}:{hash(source_text)}:{hash(translated_text)}"
        
        # Check if we have this evaluation cached
        if cache_key in self.evaluation_cache:
            self.logger.debug(f"Using cached evaluation for translation")
            return self.evaluation_cache[cache_key]
        
        try:
            # Use the appropriate service for evaluation
            if self.service == 'ollama':
                result = self._evaluate_with_ollama(source_text, translated_text, source_lang, target_lang)
            elif self.service == 'lmstudio':
                result = self._evaluate_with_lmstudio(source_text, translated_text, source_lang, target_lang)
            else:
                self.logger.warning(f"Unsupported critic service: {self.service}, defaulting to basic evaluation")
                result = self._basic_evaluation(source_text, translated_text)
            
            # Cache the result
            self.evaluation_cache[cache_key] = result
            return result
            
        except Exception as e:
            self.logger.error(f"Error in translation evaluation: {e}")
            return {"score": 0.5, "feedback": f"Error evaluating translation: {str(e)}"}
    
    def _evaluate_with_lmstudio(self, source_text: str, translated_text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """
        Evaluate translation quality using LM Studio's OpenAI-compatible API.
        
        Args:
            source_text: The original text in the source language
            translated_text: The translated text
            source_lang: The source language code
            target_lang: The target language code
            
        Returns:
            Dictionary with evaluation score and feedback
        """
        # Create language names from codes
        source_lang_name = self._get_language_name(source_lang)
        target_lang_name = self._get_language_name(target_lang)
        
        # Build the system message
        system_message = f"""You are a translation critic and improver. Your task is to review translations from {source_lang_name} to {target_lang_name} and provide detailed feedback."""
        
        # Build the user message
        user_message = f"""Review this translation:

Original text ({source_lang_name}): {source_text}

Attempted translation ({target_lang_name}): {translated_text}

Your task:
1. Rate the translation quality on a scale of 1-10
2. Identify any errors or issues in the translation
3. MOST IMPORTANTLY: Provide a corrected/improved version of the translation (ONLY ONE DEFINITIVE REVISED VERSION)

Return your response in this JSON format:
{{
  "score": <number between 1 and 10>,
  "feedback": "<your critique with specific improvement suggestions>",
  "revised_translation": "<your corrected version of the translation>"
}}

Only return the JSON object, no other text."""
        
        try:
            # Prepare request payload in OpenAI Chat Completions format
            headers = {
                "Content-Type": "application/json"
            }
            
            temperature = self.config.getfloat("lmstudio", "temperature", fallback=self.temperature)
            
            data = {
                "model": self.lmstudio_model,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message}
                ],
                "temperature": temperature,
                "max_tokens": self.config.getint("lmstudio", "context_length", fallback=4096),
                "stream": False
            }
            
            # Make request with retries
            max_retries = 3
            retry_delay = 2  # seconds
            
            for attempt in range(max_retries):
                try:
                    self.logger.debug(f"Sending evaluation request to LM Studio for {self.lmstudio_model} at {self.lmstudio_api_url}")
                    
                    # Increase timeout for more complex evaluations (300 seconds = 5 minutes)
                    response = requests.post(self.lmstudio_api_url, json=data, headers=headers, timeout=300)
                    response.raise_for_status()
                    
                    # Parse the response
                    result = response.json()
                    self.logger.debug(f"Received LM Studio critic response: {json.dumps(result)[:200]}...")
                    
                    # Extract the response content from the OpenAI API format
                    if "choices" in result and len(result["choices"]) > 0:
                        response_text = result["choices"][0]["message"]["content"].strip()
                        
                        # Extract the JSON part from the response
                        try:
                            evaluation = json.loads(response_text)
                        except json.JSONDecodeError:
                            # If that fails, try to extract JSON-like content
                            self.logger.debug("Couldn't parse response as JSON directly, trying to extract JSON object")
                            try:
                                json_str = self._extract_json_from_text(response_text)
                                evaluation = json.loads(json_str)
                            except (json.JSONDecodeError, ValueError):
                                self.logger.warning(f"Failed to extract JSON from response: {response_text[:100]}...")
                                # Fallback: create a basic result based on the text
                                evaluation = self._analyze_non_json_response(response_text)
                        
                        # Ensure score is within bounds
                        if 'score' in evaluation:
                            # Score might be on 1-10 scale, convert to 0-1
                            if evaluation['score'] > 1.0:
                                normalized_score = float(evaluation['score']) / 10.0
                                self.logger.debug(f"Normalizing score from {evaluation['score']} to {normalized_score}")
                                evaluation['score'] = normalized_score
                            evaluation['score'] = min(max(float(evaluation['score']), 0.0), 1.0)
                        else:
                            evaluation['score'] = 0.5
                            
                        if 'feedback' not in evaluation:
                            evaluation['feedback'] = "No feedback provided by the evaluation model"
                        
                        self.logger.info(f"Translation evaluation result: Score {evaluation['score']:.2f}")
                        self.logger.debug(f"Evaluation feedback: {evaluation['feedback']}")
                        
                        return evaluation
                    else:
                        self.logger.warning(f"LM Studio API returned unexpected response format on attempt {attempt+1}")
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                
                except requests.exceptions.Timeout:
                    self.logger.warning(f"LM Studio API request timed out on attempt {attempt+1}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                except requests.RequestException as e:
                    self.logger.error(f"Error making request to LM Studio API: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
            
            # If we get here, all retries failed
            return {"score": 0.5, "feedback": "Failed to get evaluation from LM Studio after multiple attempts"}
                
        except Exception as e:
            self.logger.error(f"Error in LM Studio evaluation: {str(e)}")
            import traceback
            self.logger.debug(f"Full error details: {traceback.format_exc()}")
            return {"score": 0.5, "feedback": f"Error processing evaluation: {str(e)}"}
    
    def _evaluate_with_ollama(self, source_text: str, translated_text: str, source_lang: str, target_lang: str) -> Dict[str, Any]:
        """
        Evaluate translation quality using Ollama LLM.
        
        Args:
            source_text: The original text in the source language
            translated_text: The translated text
            source_lang: The source language code
            target_lang: The target language code
            
        Returns:
            Dictionary with evaluation score and feedback
        """
        # Create language names from codes
        source_lang_name = self._get_language_name(source_lang)
        target_lang_name = self._get_language_name(target_lang)
        
        # Build the prompt for the LLM
        prompt = f"""You are a translation critic and improver. Review this translation from {source_lang} to {target_lang}.

Original text ({source_lang}): {source_text}

Attempted translation ({target_lang}): {translated_text}

Your task:
1. Rate the translation quality on a scale of 1-10
2. Identify any errors or issues in the translation
3. MOST IMPORTANTLY: Provide a corrected/improved version of the translation (ONLY ONE DEFINITIVE REVISED VERSION)

Return your response in this JSON format:
{{
  "score": <number between 1 and 10>,
  "feedback": "<your critique with specific improvement suggestions>",
  "revised_translation": "<your corrected version of the translation>"
}}

Only return the JSON object, no other text.
"""
        
        try:
            # Build the API request
            data = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.temperature
                }
            }
            
            # Only add performance options if they are explicitly defined in the config
            options = {}
            
            # Process numeric options (num_gpu, num_thread, num_ctx)
            for option_name in ["num_gpu", "num_thread", "num_ctx"]:
                if self.config.has_option("ollama", option_name):
                    # Get the raw value and check if it's actually set and not commented out
                    raw_value = self.config.get("ollama", option_name, fallback=None)
                    if raw_value is not None and str(raw_value).strip() and not str(raw_value).strip().startswith('#'):
                        try:
                            # Only include options with valid integer values
                            options[option_name] = self.config.getint("ollama", option_name)
                            self.logger.debug(f"Including Ollama option in critic request: {option_name}={options[option_name]}")
                        except ValueError:
                            self.logger.warning(f"Invalid value for Ollama option '{option_name}': {raw_value}")
            
            # Process boolean options (use_mmap, use_mlock)
            for option_name in ["use_mmap", "use_mlock"]:
                if self.config.has_option("ollama", option_name):
                    raw_value = self.config.get("ollama", option_name, fallback=None)
                    if raw_value is not None and str(raw_value).strip() and not str(raw_value).strip().startswith('#'):
                        try:
                            # Only include options with valid boolean values
                            options[option_name] = self.config.getboolean("ollama", option_name)
                            self.logger.debug(f"Including Ollama option in critic request: {option_name}={options[option_name]}")
                        except ValueError:
                            self.logger.warning(f"Invalid value for Ollama option '{option_name}': {raw_value}")
            
            # Add options to the request only if we found valid ones
            if options:
                data["options"].update(options)
                self.logger.debug(f"Sending Ollama critic options: {json.dumps(options)}")
                
            self.logger.debug(f"Sending evaluation request to Ollama for {self.model} at {self.ollama_api_url}")
            
            # Make the API call with retries
            max_retries = 3
            retry_delay = 2  # seconds
            
            for attempt in range(max_retries):
                try:
                    # Increase timeout for more complex evaluations (300 seconds = 5 minutes)
                    response = requests.post(self.ollama_api_url, json=data, timeout=300)
                    response.raise_for_status()
                    
                    # Parse the response
                    result = response.json()
                    self.logger.debug(f"Received Ollama critic response: {json.dumps(result)[:200]}...")
                    response_text = result.get('response', '')
                    
                    # Apply think tags filter to remove thinking content
                    response_text = self.remove_think_tags(response_text)
                    
                    # Extract the JSON part from the response
                    # First, try to parse the response as JSON directly
                    try:
                        evaluation = json.loads(response_text)
                    except json.JSONDecodeError:
                        # If that fails, try to extract JSON-like content
                        self.logger.debug("Couldn't parse response as JSON directly, trying to extract JSON object")
                        try:
                            json_str = self._extract_json_from_text(response_text)
                            evaluation = json.loads(json_str)
                        except (json.JSONDecodeError, ValueError):
                            self.logger.warning(f"Failed to extract JSON from response: {response_text[:100]}...")
                            # Fallback: create a basic result based on the text
                            evaluation = self._analyze_non_json_response(response_text)
                    
                    # Ensure score is within bounds
                    if 'score' in evaluation:
                        # Score might be on 1-10 scale, convert to 0-1
                        if evaluation['score'] > 1.0:
                            normalized_score = float(evaluation['score']) / 10.0
                            self.logger.debug(f"Normalizing score from {evaluation['score']} to {normalized_score}")
                            evaluation['score'] = normalized_score
                        evaluation['score'] = min(max(float(evaluation['score']), 0.0), 1.0)
                    else:
                        evaluation['score'] = 0.5
                        
                    if 'feedback' not in evaluation:
                        evaluation['feedback'] = "No feedback provided by the evaluation model"
                    
                    self.logger.info(f"Translation evaluation result: Score {evaluation['score']:.2f}")
                    self.logger.debug(f"Evaluation feedback: {evaluation['feedback']}")
                    
                    return evaluation
                
                except requests.exceptions.Timeout:
                    self.logger.warning(f"Ollama API request timed out on attempt {attempt+1}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    raise
                except requests.RequestException as e:
                    self.logger.error(f"Error making request to Ollama API: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    raise
            
            # If we get here, all retries failed
            return {"score": 0.5, "feedback": "Failed to get evaluation after multiple attempts"}
                
        except Exception as e:
            self.logger.error(f"Error in Ollama evaluation: {str(e)}")
            import traceback
            self.logger.debug(f"Full error details: {traceback.format_exc()}")
            return {"score": 0.5, "feedback": f"Error processing evaluation: {str(e)}"}
    
    def _extract_json_from_text(self, text: str) -> str:
        """
        Extract a JSON object from a text string.
        
        Args:
            text: Text possibly containing a JSON object
        
        Returns:
            JSON string extracted from the text
        
        Raises:
            ValueError: If no valid JSON object is found
        """
        # Try to find content between curly braces
        import re
        json_match = re.search(r'(\{.*\})', text, re.DOTALL)
        if json_match:
            return json_match.group(1)
        
        # If no JSON object is found
        raise ValueError("No JSON object found in response")
    
    def _analyze_non_json_response(self, text: str) -> Dict[str, Any]:
        """
        Create an evaluation result from non-JSON response by analysis.
        
        Args:
            text: The response text to analyze
        
        Returns:
            Dictionary with estimated score and feedback
        """
        # Look for numeric scores in the text
        import re
        
        # Look for patterns like "score: 0.8" or "score of 0.8"
        score_matches = re.findall(r'score:?\s*(\d+\.\d+|\d+)', text, re.IGNORECASE)
        if score_matches:
            try:
                score = float(score_matches[0])
                # Ensure score is between 0 and 1
                if score > 1 and score <= 10:
                    score = score / 10  # Convert 0-10 scale to 0-1
                score = min(max(score, 0.0), 1.0)
            except ValueError:
                score = 0.5
        else:
            # Try to infer score from text sentiment
            score = 0.5
            positive_indicators = ['excellent', 'good', 'accurate', 'fluent', 'perfect']
            negative_indicators = ['poor', 'bad', 'incorrect', 'error', 'issue', 'problem']
            
            positive_count = sum(1 for word in positive_indicators if word.lower() in text.lower())
            negative_count = sum(1 for word in negative_indicators if word.lower() in text.lower())
            
            if positive_count > negative_count:
                score = 0.7 + (0.3 * min(positive_count / 5, 1))
            elif negative_count > positive_count:
                score = 0.3 - (0.3 * min(negative_count / 5, 1))
        
        return {
            "score": score,
            "feedback": text[:200] + ('...' if len(text) > 200 else '')
        }
    
    def _basic_evaluation(self, source_text: str, translated_text: str) -> Dict[str, Any]:
        """
        Basic evaluation method when no advanced service is available.
        Uses simple heuristics to estimate translation quality.
        
        Args:
            source_text: The original text
            translated_text: The translated text
            
        Returns:
            Dictionary with a basic quality score and generic feedback
        """
        # Very basic length-based check
        source_length = len(source_text.split())
        translated_length = len(translated_text.split())
        
        # Calculate length ratio - translations shouldn't be wildly different in length
        if source_length == 0:
            length_ratio = 1.0  # Avoid division by zero
        else:
            length_ratio = translated_length / source_length
        
        # Score based on length ratio
        if 0.5 <= length_ratio <= 2.0:
            score = 0.8  # Reasonable length ratio
        elif 0.3 <= length_ratio <= 3.0:
            score = 0.5  # Questionable length ratio
        else:
            score = 0.2  # Very suspicious length ratio
        
        feedback = f"Basic evaluation based on text length. Source has {source_length} words, translation has {translated_length} words."
        
        return {"score": score, "feedback": feedback}
    
    def _get_language_name(self, lang_code: str) -> str:
        """
        Convert a language code to a language name.
        
        Args:
            lang_code: ISO language code (e.g., 'en', 'fr')
            
        Returns:
            Full language name (e.g., 'English', 'French')
        """
        language_map = {
            'en': 'English',
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'it': 'Italian',
            'pt': 'Portuguese',
            'ru': 'Russian',
            'ja': 'Japanese',
            'ko': 'Korean',
            'zh': 'Chinese',
            'da': 'Danish',
            'nl': 'Dutch',
            'fi': 'Finnish',
            'sv': 'Swedish',
            'no': 'Norwegian',
            'hi': 'Hindi'
        }
        
        return language_map.get(lang_code.lower(), lang_code)
    
    def generate_quality_report(self, evaluations: List[Dict[str, Any]], source_lang: str, target_lang: str) -> str:
        """
        Generate a quality report based on a collection of evaluations.
        
        Args:
            evaluations: List of evaluation results
            source_lang: Source language code
            target_lang: Target language code
            
        Returns:
            Text report with quality statistics and insights
        """
        if not self.generate_report or not evaluations:
            return ""
        
        # Calculate statistics
        scores = [e.get('score', 0) for e in evaluations]
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)
        
        # Count problematic translations (below minimum score threshold)
        problematic = [e for e in evaluations if e.get('score', 0) < self.min_score]
        
        # Format the report
        source_lang_name = self._get_language_name(source_lang)
        target_lang_name = self._get_language_name(target_lang)
        
        report = f"""# Translation Quality Report
* **Source Language**: {source_lang_name}
* **Target Language**: {target_lang_name}
* **Evaluated Segments**: {len(evaluations)}
* **Average Quality Score**: {avg_score:.2f} / 1.00
* **Quality Range**: {min_score:.2f} - {max_score:.2f}
* **Problematic Segments**: {len(problematic)} ({len(problematic)/len(evaluations)*100:.1f}%)

## Quality Assessment
This report provides an automated assessment of translation quality using computational linguistic analysis.
Scores close to 1.00 indicate high-quality translations that preserve the original meaning
while maintaining natural language flow in the target language.

"""
        
        # Add section with examples of issues if there are problematic translations
        if problematic:
            report += "\n## Problematic Translations\n"
            # Include up to 5 examples of problematic translations
            for i, example in enumerate(problematic[:5]):
                report += f"\n### Example {i+1} (Score: {example.get('score', 0):.2f})\n"
                report += f"**Source**: {example.get('source_text', 'N/A')}\n"
                report += f"**Translation**: {example.get('translated_text', 'N/A')}\n"
                report += f"**Feedback**: {example.get('feedback', 'No feedback available')}\n"
        
        # Add timestamp
        report += f"\n\nReport generated: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        
        return report
    
    def save_report_to_file(self, report: str, base_filename: str) -> str:
        """
        Save a translation quality report to a file.
        
        Args:
            report: The report text
            base_filename: Base name for the report file
            
        Returns:
            Path to the saved report file
        """
        if not report:
            return ""
            
        try:
            # Determine the output filename
            base_path = os.path.dirname(base_filename)
            base_name = os.path.splitext(os.path.basename(base_filename))[0]
            report_path = os.path.join(base_path, f"{base_name}_quality_report.txt")
            
            # Write the report to file
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report)
                
            self.logger.info(f"Translation quality report saved to {report_path}")
            return report_path
            
        except Exception as e:
            self.logger.error(f"Error saving quality report: {e}")
            return ""

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
        debug_mode = self.config.getboolean('agent_critic', 'debug', fallback=False)
        if debug_mode and text != cleaned_text:
            self.logger.debug(f"Removed thinking content from critic response (original length: {len(text)}, new length: {len(cleaned_text)})")
            
        return cleaned_text.strip()