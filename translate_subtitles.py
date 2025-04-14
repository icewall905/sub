import os
import configparser
import requests
import pysrt
import json
import sys
from typing import List, Dict, Any

# Language mapping dictionary (full name -> ISO code)
LANGUAGE_MAPPING = {
    # Common language full names to ISO codes
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
    # Add more as needed
}

def load_config(config_path: str) -> configparser.ConfigParser:
    """
    Loads the configuration from an INI file.
    """
    config = configparser.ConfigParser()
    config.read(config_path)
    return config

# Helper function to get the ISO code from a language name
def get_iso_code(language_name: str) -> str:
    """
    Convert a language name to its ISO code.
    Returns the input as-is if no mapping is found.
    """
    language_name = language_name.lower().strip('"\' ')
    return LANGUAGE_MAPPING.get(language_name, language_name)

def call_ollama(server_url: str, model: str, prompt: str) -> str:
    """
    Send a prompt to an Ollama server and return the text response.
    Uses Ollama's API format for generate endpoint.
    """
    url = f"{server_url}/api/generate"
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(url, json=data, timeout=300)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Ollama server: {e}")
        return ""

    # Parse the response according to Ollama's API format
    resp_json = response.json()
    return resp_json.get("response", "")

def call_openai(api_key: str, model: str, prompt: str, api_base_url: str) -> str:
    """
    Calls the OpenAI ChatCompletion API to get the text response.
    """
    url = f"{api_base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=300)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling OpenAI: {e}")
        return ""

    resp_json = response.json()
    return resp_json["choices"][0]["message"]["content"]

def call_deepl(api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
    """
    Calls the DeepL API for an initial translation.
    """
    params = {
        "auth_key": api_key,
        "text": text,
        "source_lang": source_lang.upper(),  # e.g., "EN", "ES", "DE", ...
        "target_lang": target_lang.upper(),
    }
    try:
        response = requests.post(api_url, data=params, timeout=300)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling DeepL: {e}")
        return ""

    resp_json = response.json()
    # DeepL returns translations in resp_json["translations"][0]["text"]
    translations = resp_json.get("translations", [])
    if translations:
        return translations[0].get("text", "")
    return ""

def build_prompt_for_line(
    lines: List[str],
    index: int,
    config: configparser.ConfigParser,
    deepl_translation: str = ""
) -> str:
    """
    Build the prompt to send to the LLM, including some previous and upcoming
    context lines, as well as an optional DeepL translation for the line being translated.
    """
    src_lang = get_iso_code(config["general"].get("source_language", "es"))
    tgt_lang = get_iso_code(config["general"].get("target_language", "en"))
    context_size_before = config["general"].getint("context_size_before", 10)
    context_size_after  = config["general"].getint("context_size_after", 10)

    # Gather context lines
    start_context = max(0, index - context_size_before)
    end_context = min(len(lines), index + context_size_after + 1)

    context_lines = lines[start_context:index] + lines[index+1:end_context]

    # This is the line we want to translate
    line_to_translate = lines[index]

    # Example prompt
    # You can adapt or refine this prompt to better suit your translation approach.
    prompt = (
        f"You are a helpful translation assistant. Here is some conversation context in {src_lang}:\n\n"
    )
    for i, c_line in enumerate(context_lines):
        # Indicate which are previous lines vs upcoming lines
        if i < (index - start_context):
            prompt += f"[Previous] {c_line}\n"
        else:
            prompt += f"[Upcoming] {c_line}\n"

    prompt += (
        "\n"
        f"Now, please translate the following line from {src_lang} to {tgt_lang}:\n"
        f"'{line_to_translate}'\n\n"
    )

    if deepl_translation:
        prompt += (
            "Here is a translation from DeepL for reference:\n"
            f"'{deepl_translation}'\n\n"
            "If you agree that this translation is correct, please return the same text, otherwise provide the improved translation.\n"
            "Output the final translation in JSON format as follows:\n"
            '{ "translation": "your final translation here" }\n'
        )
    else:
        prompt += (
            "Output the final translation in JSON format as follows:\n"
            '{ "translation": "your final translation here" }\n'
        )
    return prompt

def pick_llm_and_translate(
    line_index: int,
    lines: List[str],
    config: configparser.ConfigParser
) -> str:
    """
    Depending on which LLM is enabled in the config (Ollama or OpenAI),
    build the prompt, call the LLM, and return the translation.
    Optionally includes a DeepL pass if enabled.
    """
    # Optionally get a translation from DeepL first
    deepl_translation = ""
    use_deepl = config["general"].getboolean("use_deepl", False)
    deepl_enabled = config["deepl"].getboolean("enabled", False)

    # The line we want to translate
    line_text = lines[line_index]
    
    if use_deepl and deepl_enabled:
        source_lang = get_iso_code(config["general"].get("source_language", "es").strip('"\''))
        target_lang = get_iso_code(config["general"].get("target_language", "en").strip('"\''))
        deepl_api_key = config["deepl"]["api_key"]
        deepl_api_url = config["deepl"]["api_url"]
        deepl_translation = call_deepl(deepl_api_key, deepl_api_url, line_text, source_lang, target_lang)

    prompt = build_prompt_for_line(lines, line_index, config, deepl_translation)

    # Decide which LLM to call
    ollama_enabled = config["ollama"].getboolean("enabled", False)
    openai_enabled = config["openai"].getboolean("enabled", False)

    if ollama_enabled:
        server_url = config["ollama"]["server_url"]
        model_name = config["ollama"]["model"]
        print(f"Translating line {line_index+1}/{len(lines)} with {model_name}...")
        llm_response = call_ollama(server_url, model_name, prompt)
    elif openai_enabled:
        api_key = config["openai"]["api_key"]
        model_name = config["openai"]["model"]
        api_base_url = config["openai"]["api_base_url"]
        print(f"Translating line {line_index+1}/{len(lines)} with {model_name}...")
        llm_response = call_openai(api_key, model_name, prompt, api_base_url)
    else:
        # Default to returning the original line if no LLM is configured
        print("No LLM configured or enabled in config. Returning original line.")
        return lines[line_index]

    # Process the LLM response
    translation = ""
    try:
        # Attempt to parse JSON from the response
        # The LLM output might contain extraneous text, so we can try to extract JSON with a quick hack:
        json_start = llm_response.find('{')
        json_end = llm_response.rfind('}')
        if json_start != -1 and json_end != -1 and json_end > json_start:
            json_str = llm_response[json_start:json_end + 1]
            parsed = json.loads(json_str)
            translation = parsed["translation"]
        else:
            # Fallback, if we can't parse, just return the entire LLM response
            translation = llm_response
    except json.JSONDecodeError:
        translation = llm_response

    # Print the original and translation for comparison with better formatting
    print("\n" + "="*60)
    print(f"SOURCE ({config['general'].get('source_language')}): \"{line_text}\"")
    print(f"TARGET ({config['general'].get('target_language')}): \"{translation}\"")
    print("="*60)
    
    return translation

def translate_srt(input_srt: str, output_srt: str, config: configparser.ConfigParser):
    """
    Loads the SRT, translates each line, and writes out a new SRT file with translations.
    """
    subs = pysrt.open(input_srt, encoding='utf-8')
    lines = [sub.text for sub in subs]

    for i, sub in enumerate(subs):
        # Translate each line, preserving timing and other SRT data
        new_text = pick_llm_and_translate(i, lines, config)
        sub.text = new_text

    # Save updated subtitles
    subs.save(output_srt, encoding='utf-8')
    print(f"Saved translated SRT to {output_srt}")

def main():
    if len(sys.argv) < 4:
        print("Usage: python translate_subtitles.py <config.ini> <input.srt> <output.srt>")
        sys.exit(1)

    config_path = sys.argv[1]
    input_srt = sys.argv[2]
    output_srt = sys.argv[3]

    config = load_config(config_path)

    translate_srt(input_srt, output_srt, config)

if __name__ == "__main__":
    main()
