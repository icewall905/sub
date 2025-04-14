#!/usr/bin/env python3

import os
import sys
import subprocess
import venv
import tempfile
import shutil
from typing import List
import configparser
import requests
import pysrt
import json
import time

from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for

###############################################################################
# 1) OPTIONAL: Create or reuse a Python virtual environment and install deps. #
###############################################################################

# List all dependencies your app needs
REQUIRED_PACKAGES = [
    "Flask",
    "pysrt",
    "requests",
    "configparser"
]

def ensure_venv(venv_dir: str = "venv"):
    """
    Checks if a virtual environment exists in `venv_dir`. If not, creates it and
    installs all required dependencies.
    """
    if not os.path.isdir(venv_dir):
        print(f"Creating new virtual environment in: {venv_dir}")
        venv.create(venv_dir, with_pip=True)
    else:
        print(f"Virtual environment {venv_dir} already exists.")

    # Now install required packages
    python_exe = os.path.join(venv_dir, "bin", "python")
    if os.name == "nt":  # Windows
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")

    print("Installing required packages...")
    for pkg in REQUIRED_PACKAGES:
        subprocess.check_call([python_exe, "-m", "pip", "install", pkg])


###############################################################################
# 2) Translation logic (adapted from previous example).                       #
###############################################################################

def load_config(config_path: str = "config.ini") -> configparser.ConfigParser:
    """
    Loads the configuration from an INI file. Defaults to config.ini.
    """
    config = configparser.ConfigParser()
    config.read(config_path)
    return config

def call_ollama(server_url: str, model: str, prompt: str) -> str:
    """
    Send a prompt to an Ollama server and return the text response.
    """
    url = f"{server_url}/generate"
    data = {
        "prompt": prompt,
        "model": model
    }
    try:
        response = requests.post(url, json=data, timeout=300)
        response.raise_for_status()
        resp_json = response.json()
        # Adapt to your Ollama response structure:
        return resp_json.get("choices", [{}])[0].get("text", "")
    except requests.exceptions.RequestException as e:
        append_log(f"[ERROR] Ollama server error: {e}")
        return ""

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
        resp_json = response.json()
        return resp_json["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        append_log(f"[ERROR] OpenAI API error: {e}")
        return ""

def call_deepl(api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
    """
    Calls the DeepL API for an initial translation.
    """
    params = {
        "auth_key": api_key,
        "text": text,
        "source_lang": source_lang.upper(),
        "target_lang": target_lang.upper(),
    }
    try:
        response = requests.post(api_url, data=params, timeout=300)
        response.raise_for_status()
        resp_json = response.json()
        translations = resp_json.get("translations", [])
        if translations:
            return translations[0].get("text", "")
        return ""
    except requests.exceptions.RequestException as e:
        append_log(f"[ERROR] DeepL API error: {e}")
        return ""

def build_prompt_for_line(
    lines: List[str],
    index: int,
    config: configparser.ConfigParser,
    deepl_translation: str = ""
) -> str:
    """
    Build the prompt to send to the LLM, including context lines.
    """
    src_lang = config["general"].get("source_language", "es")
    tgt_lang = config["general"].get("target_language", "en")
    context_size_before = config["general"].getint("context_size_before", 10)
    context_size_after  = config["general"].getint("context_size_after", 10)

    start_context = max(0, index - context_size_before)
    end_context = min(len(lines), index + context_size_after + 1)

    context_before = lines[start_context:index]
    context_after = lines[index+1:end_context]
    line_to_translate = lines[index]

    prompt = (
        f"You are a helpful translation assistant. Here is some conversation context in {src_lang}:\n\n"
    )
    for c_line in context_before:
        prompt += f"[Previous] {c_line}\n"
    for c_line in context_after:
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
            "If you agree that this translation is correct, please return the same text, "
            "otherwise provide the improved translation.\n"
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
    deepl_translation = ""
    use_deepl = config["general"].getboolean("use_deepl", False)
    deepl_enabled = config["deepl"].getboolean("enabled", False)

    if use_deepl and deepl_enabled:
        source_lang = config["general"].get("source_language", "es")
        target_lang = config["general"].get("target_language", "en")
        deepl_api_key = config["deepl"]["api_key"]
        deepl_api_url = config["deepl"]["api_url"]
        line_text = lines[line_index]
        deepl_translation = call_deepl(deepl_api_key, deepl_api_url, line_text, source_lang, target_lang)

    prompt = build_prompt_for_line(lines, line_index, config, deepl_translation)

    ollama_enabled = config["ollama"].getboolean("enabled", False)
    openai_enabled = config["openai"].getboolean("enabled", False)

    if ollama_enabled:
        server_url = config["ollama"]["server_url"]
        model_name = config["ollama"]["model"]
        llm_response = call_ollama(server_url, model_name, prompt)
    elif openai_enabled:
        api_key = config["openai"]["api_key"]
        model_name = config["openai"]["model"]
        api_base_url = config["openai"]["api_base_url"]
        llm_response = call_openai(api_key, model_name, prompt, api_base_url)
    else:
        append_log("[WARNING] No LLM configured. Returning line as-is.")
        return lines[line_index]

    # Try to parse JSON from the LLM response
    try:
        json_start = llm_response.find('{')
        json_end = llm_response.rfind('}')
        if json_start != -1 and json_end != -1 and json_end > json_start:
            json_str = llm_response[json_start:json_end + 1]
            parsed = json.loads(json_str)
            return parsed["translation"]
        else:
            return llm_response
    except json.JSONDecodeError:
        return llm_response

def translate_srt(input_path: str, output_path: str, config: configparser.ConfigParser):
    """
    Translates an SRT file line by line and saves the result.
    """
    subs = pysrt.open(input_path, encoding='utf-8')
    lines = [sub.text for sub in subs]

    append_log(f"Loaded {len(subs)} subtitle entries. Beginning translation...")

    for i, sub in enumerate(subs):
        append_log(f"Translating line {i+1}/{len(subs)}")
        new_text = pick_llm_and_translate(i, lines, config)
        sub.text = new_text

    subs.save(output_path, encoding='utf-8')
    append_log(f"Saved translated SRT to {output_path}")

###############################################################################
# 3) A small Flask app to upload an SRT and download the translated result.   #
###############################################################################

app = Flask(__name__)

# In-memory log for debugging
LOG_BUFFER = []

def append_log(msg: str):
    """
    A simple function to capture logs in memory.
    """
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    LOG_BUFFER.append(f"{ts} {msg}")
    print(f"{ts} {msg}", flush=True)

def get_logs():
    """
    Return all current logs as a single string.
    """
    return "\n".join(LOG_BUFFER)

# Simple HTML template for the upload page + console link
INDEX_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>SRT Translator</title>
</head>
<body>
    <h1>SRT Translator</h1>
    <form action="/upload" method="POST" enctype="multipart/form-data">
        <label for="srtfile">Select SRT File:</label><br><br>
        <input type="file" name="srtfile" accept=".srt" required><br><br>
        <input type="submit" value="Upload & Translate">
    </form>
    <br>
    <a href="/console" target="_blank">Open Debug Console</a>
</body>
</html>
"""

CONSOLE_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Translation Console</title>
</head>
<body>
    <h1>Translation Debug Console</h1>
    <pre id="logBox">{{ logs }}</pre>

    <script>
      // Auto-refresh the logs every 3 seconds
      setInterval(function(){
        fetch('/logs')
          .then(resp => resp.text())
          .then(txt => {
            document.getElementById('logBox').textContent = txt;
          });
      }, 3000);
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return INDEX_PAGE

@app.route("/upload", methods=["POST"])
def upload():
    """
    Handle the upload of the .srt file, process translation, store the result,
    then redirect to a download URL.
    """
    if "srtfile" not in request.files:
        return "No SRT file uploaded.", 400

    file = request.files["srtfile"]
    if file.filename == "":
        return "Empty filename.", 400

    # Save uploaded SRT to temp folder
    upload_dir = tempfile.mkdtemp(prefix="srt_upload_")
    input_path = os.path.join(upload_dir, file.filename)
    file.save(input_path)
    append_log(f"Received file: {file.filename}")

    # Prepare output path
    output_path = os.path.join(upload_dir, "translated_" + file.filename)

    # Load config
    config = load_config("config.ini")

    # Translate
    try:
        translate_srt(input_path, output_path, config)
    except Exception as e:
        append_log(f"[ERROR] Translation failed: {e}")
        return f"Translation error: {e}", 500

    # Provide a link to download
    return redirect(url_for('download_file', folder=os.path.basename(upload_dir), filename=os.path.basename(output_path)))

@app.route("/download/<folder>/<filename>")
def download_file(folder, filename):
    """
    Serve the translated SRT file for download.
    """
    # The folder is a temp directory name, the filename is "translated_xxx.srt".
    temp_parent = os.path.dirname(tempfile.mkdtemp(prefix="dummy_"))  # Just for base path
    requested_dir = os.path.join(temp_parent, folder)
    # We actually need the real path since we created it earlier
    # We'll do a simple approach: find it in /tmp or system temp if it still exists:
    full_dir = os.path.join(os.path.dirname(requested_dir), folder)

    if not os.path.exists(os.path.join(full_dir, filename)):
        return "File not found (maybe expired).", 404

    append_log(f"Downloading file from: {full_dir}/{filename}")
    return send_from_directory(full_dir, filename, as_attachment=True)

@app.route("/console")
def console():
    """
    Returns the Debug Console page, which auto-refreshes logs.
    """
    return render_template_string(CONSOLE_PAGE_TEMPLATE, logs=get_logs())

@app.route("/logs")
def logs():
    """
    Returns the in-memory logs as text.
    """
    return get_logs(), 200, {"Content-Type": "text/plain; charset=utf-8"}

def run_server():
    """
    Run the Flask server. By default, listen on localhost:5000.
    """
    append_log("Starting Flask web server at http://127.0.0.1:5000 ...")
    app.run(host="127.0.0.1", port=5000, debug=False)

###############################################################################
# 4) Main entry point to do "installer-like" steps, then launch the web UI.   #
###############################################################################

def main():
    # 1) Create/ensure venv
    venv_dir = "venv"
    ensure_venv(venv_dir)

    # 2) Re-run this script *inside* the venv if we're not already in it.
    #    This ensures that once the venv is created, we actually use it.
    #    We'll detect by checking if sys.prefix ends with our venv directory.
    if not sys.prefix.endswith(venv_dir):
        python_exe = os.path.join(venv_dir, "bin", "python")
        if os.name == "nt":  # Windows fix
            python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        print("Re-running inside virtual environment...")
        subprocess.check_call([python_exe, __file__])
        sys.exit(0)

    # 3) If we made it here, we are in the venv. Launch the web server.
    run_server()

if __name__ == "__main__":
    main()
