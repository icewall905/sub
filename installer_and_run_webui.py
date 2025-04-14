#!/usr/bin/env python3
"""
A single-file "installer" + web UI for translating SRT subtitles.

** Key Changes / Fixes in This Version **
- If you get 404 from Ollama, we now log the exact URL and data being sent.
- By default, we call "http://localhost:11434/api/generate", but can be adjusted in config.ini
- Example config.ini provided below includes "ollama.endpoint = /api/generate", so you can customize.
- DeepL disabled by default.
"""

import os
import sys
import subprocess
import shutil
import time

def which(cmd):
    """Return True if `cmd` is found in PATH, else False."""
    return shutil.which(cmd) is not None

def run_cmd(cmd_list, require_sudo=False):
    """
    Run a command list with optional sudo. Returns process return code.
    Example usage: run_cmd(["apt-get", "install", ...], require_sudo=True)
    """
    if require_sudo:
        if os.geteuid() != 0 and which("sudo"):
            cmd_list = ["sudo"] + cmd_list
    print(f"[CMD] {' '.join(cmd_list)}")
    return subprocess.call(cmd_list)

def install_system_package(package_name):
    """
    Attempt to install `package_name` using apt-get or dnf if available.
    Return True if the install succeeded or is not necessary.
    """
    if which("apt-get"):
        run_cmd(["apt-get", "update"], require_sudo=True)  # best effort
        rc = run_cmd(["apt-get", "-y", "install", package_name], require_sudo=True)
        return (rc == 0)
    if which("dnf"):
        rc = run_cmd(["dnf", "-y", "install", package_name], require_sudo=True)
        return (rc == 0)

    print(f"[WARNING] Could not install '{package_name}' - no apt-get or dnf found.")
    return False

def ensure_python3_venv_available():
    """
    Check if we can import 'venv'. If not, attempt to install 'python3-venv'.
    """
    try:
        import venv  # noqa
        return True
    except ImportError:
        print("[INFO] 'venv' module not found. Trying to install python3-venv...")
        success = install_system_package("python3-venv")
        if success:
            try:
                import venv  # noqa
                return True
            except ImportError:
                pass
        return False

REQUIRED_PYTHON_PACKAGES = ["Flask", "pysrt", "requests", "configparser"]

def create_and_populate_venv(venv_dir="venv"):
    """Create a Python venv in venv_dir, then pip install required packages."""
    if not os.path.exists(venv_dir):
        os.mkdir(venv_dir)

    print(f"[INFO] Creating virtual environment in {venv_dir} ...")
    rc = subprocess.call([sys.executable, "-m", "venv", venv_dir])
    if rc != 0:
        print("[ERROR] Failed to create venv.")
        sys.exit(1)

    python_exe = os.path.join(venv_dir, "bin", "python")
    pip_exe = os.path.join(venv_dir, "bin", "pip")
    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")

    print("[INFO] Installing Python packages in the virtual environment...")
    cmd_list = [pip_exe, "install"] + REQUIRED_PYTHON_PACKAGES
    rc = subprocess.call(cmd_list)
    if rc != 0:
        print("[ERROR] Failed to install required packages in the venv.")
        sys.exit(1)

def is_running_in_venv():
    if hasattr(sys, 'base_prefix'):
        return sys.base_prefix != sys.prefix
    elif hasattr(sys, 'real_prefix'):
        return sys.real_prefix != sys.prefix
    return False

def re_run_in_venv(venv_dir="venv"):
    python_exe = os.path.join(venv_dir, "bin", "python")
    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")

    print(f"[INFO] Re-running script inside venv: {python_exe}")
    rc = subprocess.call([python_exe, __file__])
    sys.exit(rc)

def setup_environment_and_run():
    if not ensure_python3_venv_available():
        print("[ERROR] 'python3-venv' could not be installed/found.")
        sys.exit(1)

    if not is_running_in_venv():
        create_and_populate_venv("venv")
        re_run_in_venv("venv")

    # We are now inside venv. Safe to import everything we need:
    import pysrt
    import requests
    import configparser
    import time
    import json
    import tempfile
    import os
    import flask
    from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for

    LOG_BUFFER = []

    def append_log(msg: str):
        ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
        LOG_BUFFER.append(f"{ts} {msg}")
        print(f"{ts} {msg}", flush=True)

    def get_logs():
        return "\n".join(LOG_BUFFER)

    INDEX_PAGE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SRT Translator</title>
    </head>
    <body>
        <h1>SRT Translator</h1>
        <form action="/upload" method="POST" enctype="multipart/form-data">
            <label>Select SRT File:</label><br><br>
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
        <style>
            body {
                font-family: Arial, sans-serif;
                line-height: 1.5;
                margin: 20px;
                max-width: 1200px;
            }
            h1 {
                color: #2c3e50;
                border-bottom: 1px solid #eee;
                padding-bottom: 10px;
            }
            #logBox {
                background-color: #f8f9fa;
                border: 1px solid #ddd;
                padding: 15px;
                border-radius: 4px;
                height: 600px;
                overflow-y: auto;
                white-space: pre-wrap;
                font-family: monospace;
            }
            .translation-pair {
                margin-bottom: 10px;
                padding: 8px;
                border-left: 3px solid #3498db;
                background-color: #ecf0f1;
            }
            .original {
                color: #c0392b;
            }
            .translation {
                color: #27ae60;
            }
            .progress {
                font-weight: bold;
                color: #2980b9;
            }
            .error {
                color: #e74c3c;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <h1>Translation Debug Console</h1>
        <div id="logBox">{{ logs }}</div>
        <script>
          // Auto-refresh the logs every 3 seconds
          setInterval(function(){
            fetch('/logs')
              .then(resp => resp.text())
              .then(txt => {
                document.getElementById('logBox').textContent = txt;
                // Auto-scroll to bottom
                const logBox = document.getElementById('logBox');
                logBox.scrollTop = logBox.scrollHeight;
              });
          }, 3000);
        </script>
    </body>
    </html>
    """

    def load_config(config_path: str = "config.ini") -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        return cfg

    # --------------------
    # LLM call functions
    # --------------------

    def call_ollama(server_url: str, endpoint_path: str, model: str, prompt: str) -> str:
        """
        Calls Ollama with a prompt, returning the "response" field if present.
        We log the final URL to help debug 404 issues.
        """
        url = f"{server_url.rstrip('/')}{endpoint_path}"
        data = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        # Debug log:
        append_log(f"[DEBUG] call_ollama -> POST {url}  data={data}")

        try:
            resp = requests.post(url, json=data, timeout=300)
            resp.raise_for_status()  # will raise an error if >= 400
            j = resp.json()
            # 'response' is typically the field holding the entire text
            return j.get("response", "")
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Ollama server error: {e}")
            return ""

    def call_openai(api_key: str, api_base_url: str, model: str, prompt: str) -> str:
        url = f"{api_base_url}/chat/completions"
        append_log(f"[DEBUG] call_openai -> POST {url}")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=300)
            resp.raise_for_status()
            j = resp.json()
            return j["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] OpenAI error: {e}")
            return ""

    def call_deepl(api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
        append_log(f"[DEBUG] call_deepl -> POST {api_url}")
        params = {
            "auth_key": api_key,
            "text": text,
            "source_lang": source_lang.upper(),
            "target_lang": target_lang.upper(),
        }
        try:
            r = requests.post(api_url, data=params, timeout=300)
            r.raise_for_status()
            j = r.json()
            translations = j.get("translations", [])
            if translations:
                return translations[0].get("text", "")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] DeepL error: {e}")
            return ""

    # --------------
    # Prompt Builder
    # --------------

    def build_prompt_for_line(lines, index, cfg, deepl_translation=""):
        src_lang = cfg["general"].get("source_language", "es")
        tgt_lang = cfg["general"].get("target_language", "en")
        context_before = cfg["general"].getint("context_size_before", 10)
        context_after = cfg["general"].getint("context_size_after", 10)

        start_idx = max(0, index - context_before)
        end_idx = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after = lines[index+1:end_idx]
        line_to_translate = lines[index]

        prompt = (
            f"You are a helpful translation assistant. Here is some conversation context in {src_lang}:\n\n"
        )
        for c in chunk_before:
            prompt += f"[Previous] {c}\n"
        for c in chunk_after:
            prompt += f"[Upcoming] {c}\n"

        prompt += (
            "\n"
            f"Now, please translate the following line from {src_lang} to {tgt_lang}:\n"
            f"'{line_to_translate}'\n\n"
        )

        if deepl_translation:
            prompt += (
                "Here is a translation from DeepL for reference:\n"
                f"'{deepl_translation}'\n\n"
                "If you agree that this translation is correct, return the same text, "
                "otherwise provide an improved translation.\n"
                "Output in JSON format:\n"
                '{ "translation": "your final translation here" }\n'
            )
        else:
            prompt += (
                "Output the final translation in JSON format as follows:\n"
                '{ "translation": "your final translation here" }\n'
            )

        return prompt

    def pick_llm_and_translate(line_index, lines, cfg):
        use_deepl = cfg["general"].getboolean("use_deepl", False)
        deepL_enabled = cfg["deepl"].getboolean("enabled", False)
        ollama_enabled = cfg["ollama"].getboolean("enabled", False)
        openai_enabled = cfg["openai"].getboolean("enabled", False)
        
        # Get language settings
        source_lang = cfg["general"].get("source_language", "en").strip('"\'')
        target_lang = cfg["general"].get("target_language", "da").strip('"\'')

        # Optionally get a "first pass" from DeepL
        deepl_translation = ""
        if use_deepl and deepL_enabled:
            d_key = cfg["deepl"]["api_key"]
            d_url = cfg["deepl"]["api_url"]
            text = lines[line_index]
            deepl_translation = call_deepl(d_key, d_url, text, source_lang, target_lang)

        prompt = build_prompt_for_line(lines, line_index, cfg, deepl_translation)
        original_text = lines[line_index]
        
        # Progress indicator
        append_log(f"Translating line {line_index+1}/{len(lines)}...")

        # Decide which LLM to call
        if ollama_enabled:
            server_url = cfg["ollama"]["server_url"]
            endpoint_path = cfg["ollama"].get("endpoint", "/api/generate")
            model_name = cfg["ollama"]["model"]
            llm_response = call_ollama(server_url, endpoint_path, model_name, prompt)
        elif openai_enabled:
            api_key = cfg["openai"]["api_key"]
            base_url = cfg["openai"]["api_base_url"]
            model_name = cfg["openai"]["model"]
            llm_response = call_openai(api_key, base_url, model_name, prompt)
        else:
            append_log("[WARNING] No LLM enabled. Returning original line.")
            return lines[line_index]

        # Attempt to parse JSON for the final result
        translation = ""
        try:
            jstart = llm_response.find('{')
            jend = llm_response.rfind('}')
            if jstart != -1 and jend != -1 and jend > jstart:
                json_chunk = llm_response[jstart:jend+1]
                parsed = json.loads(json_chunk)
                translation = parsed["translation"]
            else:
                translation = llm_response
        except json.JSONDecodeError:
            translation = llm_response
            
        # Display the translation with clear formatting
        append_log("=" * 60)
        append_log(f"SOURCE ({source_lang}): \"{original_text}\"")
        append_log(f"TARGET ({target_lang}): \"{translation}\"")
        append_log("=" * 60)

        return translation

    def translate_srt(input_path, output_path, cfg):
        import pysrt
        subs = pysrt.open(input_path, encoding='utf-8')
        lines = [s.text for s in subs]
        append_log(f"Loaded {len(subs)} lines from '{input_path}'")

        for i, sub in enumerate(subs):
            append_log(f"Translating line {i+1}/{len(subs)}...")
            new_text = pick_llm_and_translate(i, lines, cfg)
            sub.text = new_text

        subs.save(output_path, encoding='utf-8')
        append_log(f"Saved translated SRT to '{output_path}'")

    # ---- Flask Web UI ----
    app = Flask(__name__)

    @app.route("/")
    def index():
        return INDEX_PAGE

    @app.route("/upload", methods=["POST"])
    def upload():
        if "srtfile" not in request.files:
            return "No SRT file found in upload", 400

        file = request.files["srtfile"]
        if file.filename == "":
            return "Empty filename not allowed", 400

        tempd = tempfile.mkdtemp(prefix="srt_upload_")
        input_path = os.path.join(tempd, file.filename)
        file.save(input_path)
        append_log(f"Uploaded file -> {input_path}")

        cfg = load_config("config.ini")

        try:
            output_path = os.path.join(tempd, "translated_" + file.filename)
            translate_srt(input_path, output_path, cfg)
        except Exception as e:
            append_log(f"[ERROR] Translation failed: {e}")
            return f"Translation error: {e}", 500

        return redirect(url_for("download_file",
                                folder=os.path.basename(tempd),
                                filename=os.path.basename(output_path)))

    @app.route("/download/<folder>/<filename>")
    def download_file(folder, filename):
        import tempfile
        base_temp = tempfile.gettempdir()
        full_dir = os.path.join(base_temp, folder)

        if not os.path.exists(os.path.join(full_dir, filename)):
            return "File not found or expired", 404

        append_log(f"Downloading translated file -> {filename}")
        return send_from_directory(full_dir, filename, as_attachment=True)

    @app.route("/console")
    def console():
        return render_template_string(CONSOLE_PAGE_TEMPLATE, logs=get_logs())

    @app.route("/logs")
    def logs():
        return get_logs(), 200, {"Content-Type": "text/plain; charset=utf-8"}

    append_log("Starting Flask web server at http://127.0.0.1:5000 ...")
    app.run(host="127.0.0.1", port=5000, debug=False)

def main():
    setup_environment_and_run()

if __name__ == "__main__":
    main()
