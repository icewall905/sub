#!/usr/bin/env python3
"""
A single-file "installer" + web UI for translating SRT subtitles.

** Key Changes / Fixes in This Version **
- Improved venv check (less OS-specific).
- More robust JSON parsing from LLM response.
- Output filename now uses target language (e.g., input.en.srt -> input.da.srt).
- Added basic temporary file cleanup on exit.
- Refined logging and error messages slightly.
- If you get 404 from Ollama, we now log the exact URL and data being sent.
- By default, we call "http://localhost:11434/api/generate", but can be adjusted in config.ini
- Example config.ini provided below includes "ollama.endpoint = /api/generate", so you can customize.
- DeepL disabled by default in example config.
"""

import os
import sys
import subprocess
import shutil
import time
import atexit
import tempfile
import signal

# --- Language Mapping ---
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

# Helper function to get the ISO code from a language name
def get_iso_code(language_name: str) -> str:
    """
    Convert a language name to its ISO code.
    Returns the input as-is if no mapping is found.
    """
    language_name = language_name.lower().strip('"\' ')
    return LANGUAGE_MAPPING.get(language_name, language_name)

# --- Globals ---
TEMP_DIRS_TO_CLEAN = set()

# --- Utility Functions ---

def cleanup_temp_dirs():
    """Remove temporary directories created during execution."""
    print("[INFO] Cleaning up temporary directories...")
    for temp_dir in TEMP_DIRS_TO_CLEAN:
        if os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                print(f"[INFO] Removed temporary directory: {temp_dir}")
            except Exception as e:
                print(f"[WARNING] Failed to remove temp directory {temp_dir}: {e}")
    TEMP_DIRS_TO_CLEAN.clear()

# Register cleanup function to run on exit
atexit.register(cleanup_temp_dirs)
# Also register for common termination signals
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))


def which(cmd):
    """Return True if `cmd` is found in PATH, else False."""
    return shutil.which(cmd) is not None

def run_cmd(cmd_list):
    """Run a command list. Returns process return code."""
    print(f"[CMD] {' '.join(cmd_list)}")
    # Use Popen to potentially capture output later if needed
    process = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Stream output
    if process.stdout:
        for line in iter(process.stdout.readline, b''):
            print(line.decode().strip())
        process.stdout.close()
    return process.wait()


def ensure_python3_venv_available():
    """
    Check if we can import 'venv'. If not, exit with instructions.
    """
    try:
        import venv  # noqa: F401
        print("[INFO] Python 'venv' module is available.")
        return True
    except ImportError:
        print("[ERROR] Python 'venv' module not found.")
        print("Please install the package for your Python distribution that provides the 'venv' module.")
        print("On Debian/Ubuntu: sudo apt-get install python3-venv")
        print("On Fedora/CentOS: sudo dnf install python3-venv")
        print("On macOS (usually included with Python 3 from python.org or Homebrew).")
        print("On Windows (usually included with Python 3 installer).")
        return False

REQUIRED_PYTHON_PACKAGES = ["Flask", "pysrt", "requests"] # configparser is built-in

def create_and_populate_venv(venv_dir="venv"):
    """Create a Python venv in venv_dir, then pip install required packages."""
    if not os.path.exists(venv_dir):
        print(f"[INFO] Creating virtual environment in {venv_dir} ...")
        # Use the current Python executable to create the venv
        rc = subprocess.call([sys.executable, "-m", "venv", venv_dir])
        if rc != 0:
            print(f"[ERROR] Failed to create venv using '{sys.executable}'.")
            sys.exit(1)
    else:
        print(f"[INFO] Virtual environment '{venv_dir}' already exists.")

    # Determine platform-specific paths
    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "bin", "pip")

    if not os.path.exists(pip_exe):
         print(f"[ERROR] pip executable not found at {pip_exe}")
         sys.exit(1)

    print("[INFO] Installing/Updating Python packages in the virtual environment...")
    cmd_list = [pip_exe, "install", "--upgrade"] + REQUIRED_PYTHON_PACKAGES
    rc = run_cmd(cmd_list)
    if rc != 0:
        print("[ERROR] Failed to install required packages in the venv.")
        sys.exit(1)
    print("[INFO] Required packages installed successfully.")

def is_running_in_venv():
    """Check if the script is running inside a virtual environment."""
    return (hasattr(sys, 'real_prefix') or
            (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))

def re_run_in_venv(venv_dir="venv"):
    """Execute this script again using the Python interpreter from the venv."""
    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")

    if not os.path.exists(python_exe):
        print(f"[ERROR] Python executable not found in venv at {python_exe}")
        sys.exit(1)

    print(f"[INFO] Re-running script inside venv: {python_exe} {__file__}")
    # Pass command line arguments along
    rc = subprocess.call([python_exe, __file__] + sys.argv[1:])
    sys.exit(rc) # Exit with the return code of the child process

def setup_environment_and_run():
    """Checks venv, creates/populates if needed, and re-runs inside venv."""
    if not ensure_python3_venv_available():
        sys.exit(1)

    VENV_DIR = "venv_subtrans" # Use a more specific name

    if not is_running_in_venv():
        print("[INFO] Not running inside a virtual environment.")
        create_and_populate_venv(VENV_DIR)
        re_run_in_venv(VENV_DIR)
    else:
        print(f"[INFO] Running inside virtual environment: {sys.prefix}")
        # Proceed to run the main application logic
        run_web_ui()


# --- Main Application Logic (runs inside venv) ---

def run_web_ui():
    # Imports are safe now, we are inside the venv
    import pysrt
    import requests
    import configparser
    import time
    import json
    import flask
    from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for, jsonify

    # --- Logging ---
    LOG_BUFFER = []
    MAX_LOG_LINES = 500 # Limit buffer size

    def append_log(msg: str):
        ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
        log_line = f"{ts} {msg}"
        LOG_BUFFER.append(log_line)
        # Trim old logs if buffer exceeds max size
        if len(LOG_BUFFER) > MAX_LOG_LINES:
            LOG_BUFFER.pop(0)
        print(log_line, flush=True) # Also print to console

    def get_logs():
        return "\n".join(LOG_BUFFER)

    # --- Configuration ---
    CONFIG_FILENAME = "config.ini"

    def load_config(config_path: str = CONFIG_FILENAME) -> configparser.ConfigParser:
        if not os.path.exists(config_path):
             append_log(f"[ERROR] Configuration file '{config_path}' not found!")
             # Consider creating a default one or exiting
             sys.exit(f"Error: {config_path} not found. Please create it (see config.ini.example).")
        cfg = configparser.ConfigParser()
        try:
            cfg.read(config_path)
            append_log(f"Loaded configuration from '{config_path}'")
        except configparser.Error as e:
            append_log(f"[ERROR] Failed to parse configuration file '{config_path}': {e}")
            sys.exit(f"Error parsing {config_path}.")
        return cfg

    def call_ollama(server_url: str, endpoint_path: str, model: str, prompt: str) -> str:
        url = f"{server_url.rstrip('/')}{endpoint_path}"
        data = {"model": model, "prompt": prompt, "stream": False}
        append_log(f"[DEBUG] Calling Ollama: POST {url} | Model: {model}")
        # append_log(f"[DEBUG] Ollama Data: {json.dumps(data)}") # Can be verbose

        try:
            resp = requests.post(url, json=data, timeout=300)
            resp.raise_for_status()
            j = resp.json()
            return j.get("response", "")
        except requests.exceptions.Timeout:
            append_log(f"[ERROR] Ollama request timed out after 300 seconds.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Ollama request failed: {e}")
            # Log response body if available and not too large
            if e.response is not None:
                try:
                    err_body = e.response.text
                    append_log(f"[ERROR] Ollama Response Body (truncated): {err_body[:500]}")
                except Exception:
                    append_log("[ERROR] Could not read Ollama error response body.")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Failed to decode JSON response from Ollama: {e}")
            append_log(f"[ERROR] Ollama Raw Response: {resp.text[:500]}")
            return ""


    def call_openai(api_key: str, api_base_url: str, model: str, prompt: str) -> str:
        url = f"{api_base_url.rstrip('/')}/chat/completions"
        append_log(f"[DEBUG] Calling OpenAI: POST {url} | Model: {model}")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        data = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=300)
            resp.raise_for_status()
            j = resp.json()
            if "choices" in j and len(j["choices"]) > 0 and "message" in j["choices"][0] and "content" in j["choices"][0]["message"]:
                 return j["choices"][0]["message"]["content"]
            else:
                 append_log(f"[ERROR] Unexpected OpenAI response format: {j}")
                 return ""
        except requests.exceptions.Timeout:
            append_log(f"[ERROR] OpenAI request timed out after 300 seconds.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] OpenAI request failed: {e}")
            if e.response is not None:
                try:
                    err_body = e.response.text
                    append_log(f"[ERROR] OpenAI Response Body (truncated): {err_body[:500]}")
                except Exception:
                    append_log("[ERROR] Could not read OpenAI error response body.")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Failed to decode JSON response from OpenAI: {e}")
            append_log(f"[ERROR] OpenAI Raw Response: {resp.text[:500]}")
            return ""


    def call_deepl(api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
        # Convert full language names to ISO codes
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling DeepL: POST {api_url} | Lang: {source_lang}({source_iso}) -> {target_lang}({target_iso})")
        params = {
            "auth_key": api_key,
            "text": text,
            "source_lang": source_iso.upper(), # DeepL expects uppercase
            "target_lang": target_iso.upper(),
        }
        try:
            r = requests.post(api_url, data=params, timeout=120) # Shorter timeout for DeepL?
            r.raise_for_status()
            j = r.json()
            translations = j.get("translations", [])
            if translations:
                return translations[0].get("text", "")
            append_log("[WARNING] DeepL response did not contain translations.")
            return ""
        except requests.exceptions.Timeout:
            append_log(f"[ERROR] DeepL request timed out after 120 seconds.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] DeepL request failed: {e}")
            if e.response is not None:
                try:
                    err_body = e.response.text
                    append_log(f"[ERROR] DeepL Response Body (truncated): {err_body[:500]}")
                except Exception:
                    append_log("[ERROR] Could not read DeepL error response body.")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Failed to decode JSON response from DeepL: {e}")
            append_log(f"[ERROR] DeepL Raw Response: {r.text[:500]}")
            return ""

    # --- Prompt Building ---

    def build_prompt_for_line(lines, index, cfg, deepl_translation=""):
        # Read config safely with fallbacks
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after = lines[index+1:end_idx]
        line_to_translate = lines[index]

        prompt_lines = [
            f"You are a helpful translation assistant specializing in subtitles.",
            f"Translate the following line from {src_lang_full} to {tgt_lang_full}, maintaining the original meaning and tone.",
            "Consider the surrounding context provided below for accuracy.",
            "\n--- Context ---"
        ]
        if chunk_before:
            prompt_lines.append("[Previous Lines]:")
            prompt_lines.extend(chunk_before)
        if chunk_after:
            prompt_lines.append("[Upcoming Lines]:")
            prompt_lines.extend(chunk_after)
        prompt_lines.append("--- End Context ---\n")

        prompt_lines.append(f"Line to Translate ({src_lang_full}):")
        prompt_lines.append(f"'{line_to_translate}'\n")

        if deepl_translation:
            prompt_lines.append(f"A reference translation (from DeepL) is provided:")
            prompt_lines.append(f"'{deepl_translation}'")
            prompt_lines.append("If this reference is accurate and suitable, use it. Otherwise, provide your improved translation.\n")

        prompt_lines.append(f"Output ONLY the final translated line in {tgt_lang_full} within a JSON object like this:")
        prompt_lines.append('{ "translation": "your final translation here" }')
        prompt_lines.append("Do not include any other text, explanations, or markdown formatting outside the JSON structure.")

        return "\n".join(prompt_lines)

    # --- Translation Logic ---

    def pick_llm_and_translate(line_index, lines, cfg):
        # Read config safely
        use_deepl = cfg.getboolean("general", "use_deepl", fallback=False)
        deepL_enabled = cfg.getboolean("deepl", "enabled", fallback=False)
        ollama_enabled = cfg.getboolean("ollama", "enabled", fallback=False)
        openai_enabled = cfg.getboolean("openai", "enabled", fallback=False)

        source_lang = cfg.get("general", "source_language", fallback="en").strip('"\' ')
        target_lang = cfg.get("general", "target_language", fallback="en").strip('"\' ')
        original_text = lines[line_index]

        # Optionally get a "first pass" from DeepL
        deepl_translation = ""
        if use_deepl and deepL_enabled:
            d_key = cfg.get("deepl", "api_key", fallback="")
            d_url = cfg.get("deepl", "api_url", fallback="")
            if d_key and d_url:
                deepl_translation = call_deepl(d_key, d_url, original_text, source_lang, target_lang)
                if deepl_translation:
                     append_log(f"[DEBUG] DeepL Reference: '{deepl_translation}'")
            else:
                append_log("[WARNING] DeepL is enabled in config, but api_key or api_url is missing.")

        prompt = build_prompt_for_line(lines, line_index, cfg, deepl_translation)

        # Decide which LLM to call
        llm_response = ""
        if ollama_enabled:
            server_url = cfg.get("ollama", "server_url", fallback="")
            endpoint_path = cfg.get("ollama", "endpoint", fallback="/api/generate")
            model_name = cfg.get("ollama", "model", fallback="")
            if server_url and model_name:
                llm_response = call_ollama(server_url, endpoint_path, model_name, prompt)
            else:
                 append_log("[WARNING] Ollama is enabled, but server_url or model is missing in config.")
        elif openai_enabled:
            api_key = cfg.get("openai", "api_key", fallback="")
            base_url = cfg.get("openai", "api_base_url", fallback="https://api.openai.com/v1")
            model_name = cfg.get("openai", "model", fallback="")
            if api_key and model_name:
                llm_response = call_openai(api_key, base_url, model_name, prompt)
            else:
                 append_log("[WARNING] OpenAI is enabled, but api_key or model is missing in config.")
        else:
            append_log("[WARNING] No LLM (Ollama or OpenAI) is enabled in config. Returning original line.")
            return original_text # Return original if no LLM configured/called

        if not llm_response:
             append_log("[WARNING] LLM call failed or returned empty response. Returning original line.")
             return original_text

        # Attempt to parse JSON for the final result
        translation = ""
        try:
            # First, try parsing the whole response directly
            parsed = json.loads(llm_response)
            if isinstance(parsed, dict) and "translation" in parsed:
                translation = parsed["translation"]
            else:
                 append_log(f"[WARNING] LLM response was JSON, but missing 'translation' key: {llm_response[:200]}")
        except json.JSONDecodeError:
            # If direct parsing fails, try finding JSON within the text
            append_log("[DEBUG] LLM response not direct JSON, attempting extraction...")
            try:
                json_start = llm_response.find('{')
                json_end = llm_response.rfind('}')
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    json_str = llm_response[json_start:json_end + 1]
                    parsed = json.loads(json_str)
                    if isinstance(parsed, dict) and "translation" in parsed:
                        translation = parsed["translation"]
                    else:
                        append_log(f"[WARNING] Extracted JSON object missing 'translation' key: {json_str[:200]}")
                else:
                    append_log(f"[WARNING] Could not find JSON object markers '{{' and '}}' in LLM response.")
            except json.JSONDecodeError:
                append_log(f"[WARNING] Failed to parse extracted JSON from LLM response.")

        # If JSON parsing failed, use the raw response as a last resort
        if not translation:
            append_log("[WARNING] Could not extract translation from JSON. Using raw LLM response.")
            # Clean up potential markdown code fences
            translation = llm_response.strip().strip('`')

        # Log the result clearly
        append_log("-" * 60)
        append_log(f"  Line {line_index+1}/{len(lines)}")
        append_log(f"  SRC ({source_lang}): \"{original_text}\"")
        append_log(f"  TGT ({target_lang}): \"{translation}\"")
        append_log("-" * 60)

        return translation

    def translate_srt(input_path, output_path, cfg):
        try:
            subs = pysrt.open(input_path, encoding='utf-8')
            lines = [s.text.strip() for s in subs] # Get text lines, strip whitespace
            append_log(f"Loaded {len(subs)} subtitle entries from '{os.path.basename(input_path)}'")
        except Exception as e:
            append_log(f"[ERROR] Failed to load or parse SRT file '{input_path}': {e}")
            raise # Re-raise the exception to be caught by the caller

        total_lines = len(subs)
        for i, sub in enumerate(subs):
            append_log(f"Processing line {i+1}/{total_lines}...")
            try:
                new_text = pick_llm_and_translate(i, lines, cfg)
                sub.text = new_text # Update the subtitle object's text
                # Optional: Add a small delay to avoid overwhelming APIs
                # time.sleep(0.1)
            except Exception as e:
                 append_log(f"[ERROR] Unhandled exception during translation of line {i+1}: {e}")
                 append_log(f"Original text: {sub.text}")
                 # Decide whether to continue with original text or stop
                 append_log("Skipping translation for this line due to error.")
                 # sub.text remains unchanged

        try:
            subs.save(output_path, encoding='utf-8')
            append_log(f"Successfully saved translated SRT to '{os.path.basename(output_path)}'")
        except Exception as e:
            append_log(f"[ERROR] Failed to save translated SRT file '{output_path}': {e}")
            raise # Re-raise the exception

    # ---- Flask Web UI ----
    app = Flask(__name__)
    # Use a secret key for session management if needed later, even if simple
    app.secret_key = os.urandom(24)

    # --- HTML Templates ---
    INDEX_PAGE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>SRT Subtitle Translator</title>
        <style>
            body { font-family: sans-serif; margin: 2em; background-color: #f4f4f4; }
            .container { max-width: 600px; margin: auto; background: #fff; padding: 2em; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
            h1 { text-align: center; color: #333; }
            label { display: block; margin-bottom: 0.5em; font-weight: bold; }
            input[type="file"] { border: 1px solid #ccc; padding: 0.5em; width: calc(100% - 1.2em); margin-bottom: 1em; }
            input[type="submit"] { background-color: #5cb85c; color: white; padding: 0.8em 1.5em; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; width: 100%; }
            input[type="submit"]:hover { background-color: #4cae4c; }
            .console-link { display: block; text-align: center; margin-top: 1.5em; color: #0275d8; }
            .status { margin-top: 1em; padding: 1em; background-color: #e9ecef; border-left: 5px solid #0275d8; display: none; } /* Hidden initially */
            .error { margin-top: 1em; padding: 1em; background-color: #f8d7da; border-left: 5px solid #d9534f; color: #721c24; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>SRT Subtitle Translator</h1>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                {% for category, message in messages %}
                  <div class="{{ category }}">{{ message }}</div>
                {% endfor %}
              {% endif %}
            {% endwith %}
            <form action="/upload" method="POST" enctype="multipart/form-data" id="uploadForm">
                <label for="srtfile">Select SRT File:</label>
                <input type="file" id="srtfile" name="srtfile" accept=".srt" required>
                <input type="submit" value="Upload & Translate">
            </form>
            <div id="status" class="status">Processing... Please wait. This may take several minutes. <a href="/console" target="_blank">View Progress</a></div>
            <a href="/console" target="_blank" class="console-link">Open Debug Console</a>
        </div>
        <script>
            document.getElementById('uploadForm').addEventListener('submit', function() {
                document.getElementById('status').style.display = 'block'; // Show status message
            });
        </script>
    </body>
    </html>
    """

    CONSOLE_PAGE_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Translation Console</title>
        <style>
            body { font-family: monospace; line-height: 1.4; margin: 1em; background: #fdfdfd; }
            h1 { color: #333; border-bottom: 1px solid #eee; padding-bottom: 5px; font-family: sans-serif;}
            #logBox { background-color: #282c34; color: #abb2bf; border: 1px solid #ccc; padding: 15px; border-radius: 4px; height: 80vh; overflow-y: auto; white-space: pre-wrap; font-size: 0.9em; }
            .error { color: #e06c75; font-weight: bold; }
            .warn { color: #e5c07b; }
            .info { color: #61afef; }
            .debug { color: #98c379; }
            .progress { color: #c678dd; }
        </style>
    </head>
    <body>
        <h1>Translation Debug Console</h1>
        <div id="logBox">Loading logs...</div>
        <script>
          const logBox = document.getElementById('logBox');
          let isScrolledToBottom = true; // Assume initially scrolled to bottom

          logBox.addEventListener('scroll', () => {
              // Check if scrolled near the bottom
              isScrolledToBottom = logBox.scrollHeight - logBox.clientHeight <= logBox.scrollTop + 10; // 10px buffer
          });

          function fetchLogs() {
            fetch('/logs')
              .then(resp => resp.text())
              .then(txt => {
                logBox.textContent = txt; // Update content
                // Auto-scroll only if user was already near the bottom
                if(isScrolledToBottom) {
                    logBox.scrollTop = logBox.scrollHeight;
                }
              })
              .catch(error => {
                  console.error("Error fetching logs:", error);
                  logBox.textContent += "\\nError fetching logs...";
              });
          }

          // Fetch logs immediately and then every 3 seconds
          fetchLogs();
          setInterval(fetchLogs, 3000);
        </script>
    </body>
    </html>
    """

    # --- Flask Routes ---
    @app.route("/")
    def index():
        return render_template_string(INDEX_PAGE)

    @app.route("/upload", methods=["POST"])
    def upload():
        from flask import flash # Import flash here

        if "srtfile" not in request.files:
            flash("No SRT file part in the request.", "error")
            return redirect(url_for("index"))

        file = request.files["srtfile"]
        if file.filename == "":
            flash("No selected file.", "error")
            return redirect(url_for("index"))

        if not file.filename.lower().endswith(".srt"):
             flash("Invalid file type. Please upload an SRT file.", "error")
             return redirect(url_for("index"))

        # Create a unique temporary directory for this upload
        try:
            tempd = tempfile.mkdtemp(prefix="srt_translate_")
            TEMP_DIRS_TO_CLEAN.add(tempd) # Register for cleanup
            append_log(f"Created temporary directory: {tempd}")
        except Exception as e:
            append_log(f"[ERROR] Failed to create temporary directory: {e}")
            flash("Server error creating temporary directory.", "error")
            return redirect(url_for("index"))

        input_filename = file.filename # Keep original filename
        input_path = os.path.join(tempd, input_filename)

        try:
            file.save(input_path)
            append_log(f"Saved uploaded file to: {input_path}")
        except Exception as e:
            append_log(f"[ERROR] Failed to save uploaded file '{input_path}': {e}")
            flash("Server error saving uploaded file.", "error")
            return redirect(url_for("index"))

        # Load config *after* file upload attempt
        try:
            cfg = load_config(CONFIG_FILENAME)
            target_lang = cfg.get("general", "target_language", fallback="translated").strip('"\' ')
            source_lang = cfg.get("general", "source_language", fallback="original").strip('"\' ')
        except Exception as e:
             append_log(f"[ERROR] Failed to load or parse config during upload: {e}")
             flash("Server error loading configuration.", "error")
             return redirect(url_for("index"))

        # Construct the output filename based on target language
        base, ext = os.path.splitext(input_filename)
        # Attempt to replace source lang code if present, otherwise append target lang
        output_filename = base.replace(f'.{source_lang}', f'.{target_lang}')
        if output_filename == base: # If source lang wasn't found/replaced
             output_filename = f"{base}.{target_lang}{ext}"
        else:
             output_filename += ext # Add back the extension

        # Ensure filename is safe
        from werkzeug.utils import secure_filename
        output_filename = secure_filename(output_filename)
        output_path = os.path.join(tempd, output_filename)

        append_log(f"Starting translation: '{input_filename}' -> '{output_filename}'")
        try:
            # Run the translation process
            translate_srt(input_path, output_path, cfg)
            append_log("Translation process completed.")
            # Redirect to download
            return redirect(url_for("download_file",
                                    folder=os.path.basename(tempd),
                                    filename=output_filename))
        except Exception as e:
            append_log(f"[ERROR] Translation process failed: {e}")
            # Optionally log traceback here
            import traceback
            append_log(traceback.format_exc())
            flash(f"Translation failed: {e}", "error")
            return redirect(url_for("index"))


    @app.route("/download/<path:folder>/<path:filename>")
    def download_file(folder, filename):
        # Security: Basic check to prevent accessing arbitrary folders
        # We rely on the fact that 'folder' should be one of the temp dirs we created.
        # A more robust check might involve storing allowed temp dirs in the session.
        base_temp = tempfile.gettempdir()
        full_dir = os.path.join(base_temp, folder)

        # Prevent path traversal
        if not os.path.normpath(full_dir).startswith(os.path.normpath(base_temp)):
             append_log(f"[SECURITY] Attempt to access invalid directory: {folder}")
             return "Invalid directory specified", 400

        # Check if the directory and file exist
        file_path = os.path.join(full_dir, filename)
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            append_log(f"[ERROR] Download request for non-existent file: {file_path}")
            return "File not found or expired.", 404

        append_log(f"Serving download: {filename} from {folder}")
        try:
            return send_from_directory(full_dir, filename, as_attachment=True)
        except Exception as e:
             append_log(f"[ERROR] Error sending file '{filename}': {e}")
             return "Error serving file.", 500


    @app.route("/console")
    def console():
        return render_template_string(CONSOLE_PAGE_TEMPLATE)

    @app.route("/logs")
    def logs():
        # Return logs as plain text
        return get_logs(), 200, {"Content-Type": "text/plain; charset=utf-8"}

    # --- Start Server ---
    append_log("Starting Flask web server...")
    print("="*40)
    print(f"  Subtitle Translator UI running at:")
    print(f"  http://127.0.0.1:5000/")
    print(f"  Open this URL in your web browser.")
    print("="*40)
    print("Press CTRL+C to stop the server.")
    try:
        # Use waitress or gunicorn for production, but Flask dev server is fine for this script
        app.run(host="127.0.0.1", port=5000, debug=False) # Turn off Flask debug mode for cleaner logs
    except Exception as e:
        append_log(f"[ERROR] Failed to start Flask server: {e}")
        sys.exit(1)
    finally:
        # Ensure cleanup runs even if server stops unexpectedly
        cleanup_temp_dirs()


# --- Script Entry Point ---

def main():
    # This function now only orchestrates the setup
    setup_environment_and_run()

if __name__ == "__main__":
    main()
