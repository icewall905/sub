#!/usr/bin/env python3
"""
A single-file "installer" + web UI for translating SRT subtitles, now with an optional Agent Critic pass.

** Key Changes / Fixes in This Version **
- (Existing changes omitted for brevity.)
- Added Agent Critic feature.
- Added a live console box on the home page to watch the logs as soon as you click "Upload & Translate."
"""

import os
import sys
import subprocess
import shutil
import time
import atexit
import tempfile
import signal
import platform
import logging
import threading
import configparser
import webbrowser
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from collections import deque

# HTML template for viewing logs in a separate tab
LOG_VIEWER_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Log Viewer - SRT Subtitle Translator</title>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }
        .container { max-width: 1200px; margin: 0 auto; padding: 1em; }
        header { background-color: #333; color: white; padding: 1em; }
        header h1 { margin: 0; }
        nav { display: flex; margin-top: 0.5em; }
        nav a { color: #ddd; text-decoration: none; margin-right: 1em; }
        nav a:hover { color: white; }
        #log-container { 
            background-color: #282c34; 
            color: #abb2bf; 
            padding: 1em; 
            border-radius: 4px; 
            font-family: monospace; 
            height: 70vh; 
            overflow-y: auto;
            white-space: pre-wrap;
            margin-top: 1em;
        }
        .error { color: #e06c75; }
        .warning { color: #e5c07b; }
        .info { color: #61afef; }
        .debug { color: #98c379; }
        .controls { margin-top: 1em; display: flex; align-items: center; }
        .controls button { 
            background-color: #4CAF50; 
            border: none; 
            color: white; 
            padding: 0.5em 1em; 
            margin-right: 1em;
            border-radius: 4px;
            cursor: pointer;
        }
        .controls button:hover { background-color: #45a049; }
        .controls label { margin-right: 0.5em; }
        footer { margin-top: 2em; text-align: center; color: #666; }
    </style>
</head>
<body>
    <header>
        <h1>Live Log Viewer</h1>
        <nav>
            <a href="/">Home</a>
            <a href="/config">Configuration</a>
            <a href="/logs" class="active">Logs</a>
        </nav>
    </header>

    <div class="container">
        <div class="controls">
            <button id="refresh-btn">Refresh Now</button>
            <label for="auto-refresh">Auto-refresh:</label>
            <input type="checkbox" id="auto-refresh" checked>
            <span id="status" style="margin-left: 1em; color: #666;"></span>
        </div>
        
        <div id="log-container">Loading logs...</div>
        
        <footer>
            <p>SRT Subtitle Translator - Live Log Viewer</p>
        </footer>
    </div>

    <script>
        const logContainer = document.getElementById('log-container');
        const refreshBtn = document.getElementById('refresh-btn');
        const autoRefreshCheckbox = document.getElementById('auto-refresh');
        const statusElement = document.getElementById('status');
        let isScrolledToBottom = true;
        let refreshInterval;

        // Detect if user manually scrolled up
        logContainer.addEventListener('scroll', () => {
            isScrolledToBottom = Math.abs(
                (logContainer.scrollHeight - logContainer.clientHeight) - 
                logContainer.scrollTop
            ) < 10;
        });

        function formatLogs(logText) {
            if (!logText) return "No logs available";
            
            // Apply syntax highlighting
            return logText
                .replace(/\[ERROR\].*$/gm, match => `<span class="error">${match}</span>`)
                .replace(/\[WARNING\].*$/gm, match => `<span class="warning">${match}</span>`)
                .replace(/\[INFO\].*$/gm, match => `<span class="info">${match}</span>`)
                .replace(/\[DEBUG\].*$/gm, match => `<span class="debug">${match}</span>`);
        }

        function fetchLogs() {
            statusElement.textContent = "Fetching logs...";
            
            fetch('/api/logs')
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! Status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    logContainer.innerHTML = formatLogs(data.logs);
                    
                    if (isScrolledToBottom) {
                        logContainer.scrollTop = logContainer.scrollHeight;
                    }
                    
                    statusElement.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
                })
                .catch(error => {
                    console.error('Error fetching logs:', error);
                    statusElement.textContent = `Error: ${error.message}`;
                });
        }

        function toggleAutoRefresh() {
            if (autoRefreshCheckbox.checked) {
                refreshInterval = setInterval(fetchLogs, 3000);
                statusElement.textContent = "Auto-refresh enabled";
            } else {
                clearInterval(refreshInterval);
                statusElement.textContent = "Auto-refresh disabled";
            }
        }

        // Initial load
        fetchLogs();
        
        // Setup event listeners
        refreshBtn.addEventListener('click', fetchLogs);
        autoRefreshCheckbox.addEventListener('change', toggleAutoRefresh);
        
        // Start auto-refresh
        toggleAutoRefresh();
    </script>
</body>
</html>
"""

CONFIG_EDITOR_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Configuration Editor - SRT Subtitle Translator</title>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }
        .container { max-width: 1200px; margin: 0 auto; padding: 1em; }
        header { background-color: #333; color: white; padding: 1em; }
        header h1 { margin: 0; }
        nav { display: flex; margin-top: 0.5em; }
        nav a { color: #ddd; text-decoration: none; margin-right: 1em; }
        nav a:hover { color: white; }
        
        .config-container {
            background-color: white;
            border-radius: 4px;
            padding: 1em;
            margin-top: 1em;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .section {
            margin-bottom: 2em;
            padding: 1em;
            background-color: #f9f9f9;
            border-left: 4px solid #4CAF50;
        }
        
        .section h2 {
            margin-top: 0;
            color: #333;
            font-size: 1.2em;
        }
        
        .form-group {
            margin-bottom: 1em;
        }
        
        label {
            display: block;
            margin-bottom: 0.5em;
            font-weight: bold;
        }
        
        input[type="text"], input[type="number"], input[type="password"], select {
            width: 100%;
            padding: 0.5em;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }
        
        input[type="checkbox"] {
            margin-right: 0.5em;
        }
        
        .buttons {
            margin-top: 1em;
            text-align: right;
        }
        
        button {
            background-color: #4CAF50;
            border: none;
            color: white;
            padding: 0.7em 1.5em;
            border-radius: 4px;
            cursor: pointer;
            font-size: 1em;
        }
        
        button:hover {
            background-color: #45a049;
        }
        
        button.cancel {
            background-color: #f44336;
            margin-right: 1em;
        }
        
        button.cancel:hover {
            background-color: #e53935;
        }
        
        .notification {
            padding: 1em;
            margin: 1em 0;
            border-radius: 4px;
            display: none;
        }
        
        .success {
            background-color: #dff0d8;
            border-left: 4px solid #3c763d;
            color: #3c763d;
        }
        
        .error {
            background-color: #f2dede;
            border-left: 4px solid #a94442;
            color: #a94442;
        }
        
        footer { margin-top: 2em; text-align: center; color: #666; }
    </style>
</head>
<body>
    <header>
        <h1>Configuration Editor</h1>
        <nav>
            <a href="/">Home</a>
            <a href="/config" class="active">Configuration</a>
            <a href="/logs">Logs</a>
        </nav>
    </header>

    <div class="container">
        <div id="notification" class="notification"></div>
        
        <div class="config-container">
            <form id="config-form">
                <div id="config-sections">
                    <!-- Config sections will be dynamically generated here -->
                    <div class="loading">Loading configuration...</div>
                </div>
                
                <div class="buttons">
                    <button type="button" class="cancel" id="reset-btn">Reset Changes</button>
                    <button type="submit" id="save-btn">Save Configuration</button>
                </div>
            </form>
        </div>
        
        <footer>
            <p>SRT Subtitle Translator - Configuration Editor</p>
        </footer>
    </div>

    <script>
        let originalConfig = {};
        
        // Fetch the current configuration
        function fetchConfig() {
            fetch('/api/config')
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! Status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    originalConfig = data;
                    renderConfigForm(data.config);
                })
                .catch(error => {
                    console.error('Error fetching configuration:', error);
                    showNotification('error', `Error loading configuration: ${error.message}`);
                });
        }
        
        // Render the configuration form
        function renderConfigForm(config) {
            const configSections = document.getElementById('config-sections');
            configSections.innerHTML = '';
            
            // Sort sections for consistent display
            const sortedSections = Object.keys(config).sort();
            
            for (const section of sortedSections) {
                const sectionDiv = document.createElement('div');
                sectionDiv.className = 'section';
                
                const sectionTitle = document.createElement('h2');
                sectionTitle.textContent = section.toUpperCase();
                sectionDiv.appendChild(sectionTitle);
                
                const settings = config[section];
                const sortedKeys = Object.keys(settings).sort();
                
                for (const key of sortedKeys) {
                    const value = settings[key];
                    const formGroup = document.createElement('div');
                    formGroup.className = 'form-group';
                    
                    const label = document.createElement('label');
                    label.setAttribute('for', `${section}-${key}`);
                    label.textContent = formatSettingName(key);
                    
                    let input;
                    
                    // Create appropriate input based on value type
                    if (typeof value === 'boolean') {
                        input = document.createElement('input');
                        input.type = 'checkbox';
                        input.checked = value;
                    } else if (typeof value === 'number') {
                        input = document.createElement('input');
                        input.type = 'number';
                        input.step = Number.isInteger(value) ? '1' : '0.1';
                        input.value = value;
                    } else if (key.includes('api_key') || key.includes('password')) {
                        input = document.createElement('input');
                        input.type = 'password';
                        input.value = value;
                    } else if (key === 'enabled') {
                        // Boolean
                        input = document.createElement('input');
                        input.type = 'checkbox';
                        // Handle 'true' or True
                        input.checked = (value === true || value === 'true');
                    } else if (key === 'model' && section === 'ollama') {
                        input = document.createElement('select');
                        
                        // Common Ollama models
                        const models = ['llama2', 'llama2:13b', 'llama2:70b', 'mistral', 'mixtral', 'codellama', 'codellama:13b', 'codellama:34b', 'phi', 'phi:2.7b'];
                        
                        // Add current value if not in list
                        if (value && !models.includes(value)) {
                            models.unshift(value);
                        }
                        
                        for (const model of models) {
                            const option = document.createElement('option');
                            option.value = model;
                            option.textContent = model;
                            option.selected = model === value;
                            input.appendChild(option);
                        }
                    } else if (key === 'model' && section === 'openai') {
                        input = document.createElement('select');
                        
                        // Common OpenAI models
                        const models = ['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo', 'gpt-4o'];
                        
                        // Add current value if not in list
                        if (value && !models.includes(value)) {
                            models.unshift(value);
                        }
                        
                        for (const model of models) {
                            const option = document.createElement('option');
                            option.value = model;
                            option.textContent = model;
                            option.selected = model === value;
                            input.appendChild(option);
                        }
                    } else {
                        input = document.createElement('input');
                        input.type = 'text';
                        input.value = value;
                    }
                    
                    input.id = `${section}-${key}`;
                    input.name = `${section}-${key}`;
                    input.className = 'form-control';
                    input.setAttribute('data-section', section);
                    input.setAttribute('data-key', key);
                    
                    formGroup.appendChild(label);
                    formGroup.appendChild(input);
                    sectionDiv.appendChild(formGroup);
                }
                
                configSections.appendChild(sectionDiv);
            }
        }
        
        // Format setting name for display (e.g., "api_key" -> "API Key")
        function formatSettingName(key) {
            return key
                .split('_')
                .map(word => word.charAt(0).toUpperCase() + word.slice(1))
                .join(' ');
        }
        
        // Save the configuration
        function saveConfig() {
            const updatedConfig = {};
            
            // Get all input elements
            const inputs = document.querySelectorAll('#config-form input, #config-form select');
            
            // Build updated config object
            for (const input of inputs) {
                const section = input.getAttribute('data-section');
                const key = input.getAttribute('data-key');
                
                if (!updatedConfig[section]) {
                    updatedConfig[section] = {};
                }
                
                let value;
                if (input.type === 'checkbox') {
                    value = input.checked;
                } else if (input.type === 'number') {
                    value = parseFloat(input.value);
                } else {
                    value = input.value;
                }
                
                updatedConfig[section][key] = value;
            }
            
            // Send the updated config to the server
            fetch('/api/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ config: updatedConfig })
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! Status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    showNotification('success', 'Configuration saved successfully!');
                    // Update the original config
                    originalConfig = updatedConfig;
                } else {
                    showNotification('error', `Error: ${data.message}`);
                }
            })
            .catch(error => {
                console.error('Error saving configuration:', error);
                showNotification('error', `Error saving configuration: ${error.message}`);
            });
        }
        
        // Reset the form to the original config
        function resetForm() {
            renderConfigForm(originalConfig.config);
            showNotification('success', 'Form reset to last saved configuration');
        }
        
        // Show a notification
        function showNotification(type, message) {
            const notification = document.getElementById('notification');
            notification.className = `notification ${type}`;
            notification.textContent = message;
            notification.style.display = 'block';
            
            // Hide after 5 seconds
            setTimeout(() => {
                notification.style.display = 'none';
            }, 5000);
        }
        
        // Event listeners
        document.getElementById('config-form').addEventListener('submit', function(e) {
            e.preventDefault();
            saveConfig();
        });
        
        document.getElementById('reset-btn').addEventListener('click', resetForm);
        
        // Initial load
        fetchConfig();
    </script>
</body>
</html>
"""

# Global constants
CONFIG_FILENAME = "config.ini"
CONFIG_EXAMPLE_FILENAME = "config.ini.example"
LOG_FILENAME = "translator.log"
MAX_LOG_BUFFER_SIZE = 1000
LOG_BUFFER = deque(maxlen=MAX_LOG_BUFFER_SIZE)
TEMP_DIRS_TO_CLEAN = set()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILENAME),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class Colors:
    """Terminal color codes for pretty output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    
    # Additional colors
    BRIGHT_BLUE = '\033[94;1m'
    BRIGHT_GREEN = '\033[92;1m'
    BRIGHT_YELLOW = '\033[93;1m'
    BRIGHT_CYAN = '\033[96;1m'
    MAGENTA = '\033[35m'
    BRIGHT_MAGENTA = '\033[35;1m'
    
    @staticmethod
    def terminal_supports_color():
        """Check if the terminal supports color."""
        if platform.system() == 'Windows':
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                return True
            except:
                return False
        else:
            return sys.stdout.isatty()
    
    @staticmethod
    def format(text, color_code):
        """Apply color to text if supported by terminal."""
        if Colors.terminal_supports_color():
            return f"{color_code}{text}{Colors.ENDC}"
        return text

def live_stream_translation_info(stage, original, translation, current_idx, total_lines, deepl_translation=None, critic_type=None):
    """Display live translation information in a colorful, formatted way."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    
    supports_color = Colors.terminal_supports_color()
    if not supports_color:
        RESET = BOLD = GREEN = BLUE = YELLOW = CYAN = MAGENTA = RED = ""

    separator = f"{CYAN}{'=' * 30} TRANSLATION PROCESS {'=' * 30}{RESET}"
    sub_separator = f"{CYAN}{'-' * 80}{RESET}"
    progress = f"{current_idx}/{total_lines}"

    print(f"\n{separator}", flush=True)
    print(f"{BOLD}{GREEN}[{progress}] STAGE: {stage.upper()}{RESET}", flush=True)
    print(f"{sub_separator}", flush=True)
    
    print(f"{BOLD}Original Text:{RESET}")
    print(f"{BLUE}{original}{RESET}")
    
    if deepl_translation and stage.upper() != "DEEPL TRANSLATION":
        print(f"\n{BOLD}DeepL Translation:{RESET}")
        print(f"{YELLOW}{deepl_translation}{RESET}")
    
    if translation:
        if translation != "No changes":
            label = "Translation"
            if critic_type:
                label += f" ({critic_type})"
            print(f"\n{BOLD}{label}:{RESET}")
            print(f"{MAGENTA}{translation}{RESET}")
        else:
            print(f"\n{BOLD}Result:{RESET} {RED}No changes made{RESET}")
    
    print(f"\n{sub_separator}")
    print(f"{BOLD}{GREEN}✓ {stage.upper()} COMPLETE{RESET}")
    
    timestamp = time.strftime("%H:%M:%S")
    print(f"{CYAN}[Timestamp: {timestamp}]{RESET}")
    print(f"{separator}\n", flush=True)
    
    log_msg = f"[LIVE] [{stage}] Original: \"{original}\" | Translation: \"{translation}\""
    if file_logger:
        file_logger.info(log_msg)
    
    sys.stdout.flush()

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

def get_iso_code(language_name: str) -> str:
    language_name = language_name.lower().strip('"\' ')
    return LANGUAGE_MAPPING.get(language_name, language_name)

TEMP_DIRS_TO_CLEAN = set()

def cleanup_temp_dirs():
    print("[INFO] Cleaning up temporary directories...")
    for temp_dir in TEMP_DIRS_TO_CLEAN:
        if os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                print(f"[INFO] Removed temporary directory: {temp_dir}")
            except Exception as e:
                print(f"[WARNING] Failed to remove temp directory {temp_dir}: {e}")
    TEMP_DIRS_TO_CLEAN.clear()

atexit.register(cleanup_temp_dirs)
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))

def which(cmd):
    return shutil.which(cmd) is not None

def run_cmd(cmd_list):
    print(f"[CMD] {' '.join(cmd_list)}")
    process = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if process.stdout:
        for line in iter(process.stdout.readline, b''):
            print(line.decode().strip())
        process.stdout.close()
    return process.wait()

def ensure_python3_venv_available():
    try:
        import venv  # noqa: F401
        print("[INFO] Python 'venv' module is available.")
        return True
    except ImportError:
        print("[ERROR] Python 'venv' module not found.")
        print("Please install the package for your Python distribution that provides the 'venv' module.")
        return False

REQUIRED_PYTHON_PACKAGES = ["Flask", "pysrt", "requests", "colorama"]

def create_and_populate_venv(venv_dir="venv"):
    if not os.path.exists(venv_dir):
        print(f"[INFO] Creating virtual environment in {venv_dir} ...")
        rc = subprocess.call([sys.executable, "-m", "venv", venv_dir])
        if (rc != 0):
            print(f"[ERROR] Failed to create venv using '{sys.executable}'.")
            sys.exit(1)
    else:
        print(f"[INFO] Virtual environment '{venv_dir}' already exists.")

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
    return (
        hasattr(sys, 'real_prefix') or
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    )

def re_run_in_venv(venv_dir="venv"):
    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")

    if not os.path.exists(python_exe):
        print(f"[ERROR] Python executable not found in venv at {python_exe}")
        sys.exit(1)

    print(f"[INFO] Re-running script inside venv: {python_exe} {__file__}")
    rc = subprocess.call([python_exe, __file__] + sys.argv[1:])
    sys.exit(rc)

def setup_environment_and_run():
    if sys.platform == "darwin":  # macOS
        print("[INFO] Detected macOS system")
        has_venv = ensure_python3_venv_available()
        if not has_venv:
            if is_brew_installed():
                print("[INFO] Homebrew is installed. Will use it to setup environment.")
                if install_dependencies_with_brew():
                    print("[INFO] Successfully installed dependencies with Homebrew.")
                    has_venv = ensure_python3_venv_available()
                else:
                    print("[ERROR] Failed to install dependencies with Homebrew.")
                    sys.exit(1)
            else:
                print("[INFO] Homebrew not found. Recommending installation...")
                print("\nTo install Homebrew on macOS, run this command in your terminal:")
                print('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
                sys.exit(1)
    else:
        if not ensure_python3_venv_available():
            sys.exit(1)

    VENV_DIR = "venv_subtrans"
    if not is_running_in_venv():
        print("[INFO] Not running inside a virtual environment.")
        create_and_populate_venv(VENV_DIR)
        re_run_in_venv(VENV_DIR)
    else:
        print(f"[INFO] Running inside virtual environment: {sys.prefix}")
        run_web_ui()

def is_brew_installed():
    return which("brew")

def install_dependencies_with_brew():
    print("[INFO] Installing dependencies using Homebrew...")
    run_cmd(["brew", "update"])
    if not which("python3"):
        print("[INFO] Installing Python 3 with Homebrew...")
        run_cmd(["brew", "install", "python3"])
    else:
        print("[INFO] Python 3 is already installed.")

    if not which("pip3"):
        print("[WARNING] pip3 not found. Trying to install...")
        run_cmd(["brew", "reinstall", "python3"])

    return which("python3") and which("pip3")

def run_web_ui():
    import pysrt
    import requests
    import configparser
    import time
    import json
    import flask
    import os
    import logging
    from logging.handlers import RotatingFileHandler
    from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for, jsonify

    LOG_BUFFER = []
    MAX_LOG_LINES = 500

    # --- Logging Setup ---
    global file_logger
    file_logger = None
    
    def append_log(msg: str):
        ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
        log_line = f"{ts} {msg}"
        LOG_BUFFER.append(log_line)
        if len(LOG_BUFFER) > MAX_LOG_LINES:
            LOG_BUFFER.pop(0)
        print(log_line, flush=True)
        
        if file_logger:
            log_level = "INFO"
            if msg.startswith("[ERROR]"):
                log_level = "ERROR"
            elif msg.startswith("[WARNING]"):
                log_level = "WARNING"
            elif msg.startswith("[DEBUG]"):
                log_level = "DEBUG"
            
            # Strip any prior timestamp if present
            if msg.startswith("[20"):  
                msg = msg[21:]  
            
            if log_level == "ERROR":
                file_logger.error(msg)
            elif log_level == "WARNING":
                file_logger.warning(msg)
            elif log_level == "DEBUG":
                file_logger.debug(msg)
            else:
                file_logger.info(msg)

    def get_logs():
        try:
            if os.path.exists(LOG_FILENAME):
                with open(LOG_FILENAME, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    return ''.join(lines[-500:])
            return '\n'.join(LOG_BUFFER)
        except Exception as e:
            return f"Error reading logs: {str(e)}\n\n" + '\n'.join(LOG_BUFFER)

    # Load config to set up any file logging
    cfg_for_logger = configparser.ConfigParser()
    try:
        cfg_for_logger.read(CONFIG_FILENAME)
    except:
        pass
    if cfg_for_logger.has_section("logging"):
        file_enabled = cfg_for_logger.getboolean("logging", "file_enabled", fallback=True)
        log_file = cfg_for_logger.get("logging", "log_file", fallback="translator.log")
        max_size_mb = cfg_for_logger.getint("logging", "max_size_mb", fallback=5)
        backup_count = cfg_for_logger.getint("logging", "backup_count", fallback=2)
        if file_enabled:
            max_bytes = max_size_mb * 1024 * 1024
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir)
                except Exception as e:
                    pass
            try:
                file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
                file_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                file_logger = logging.getLogger("subtitle_translator")
                file_logger.setLevel(logging.DEBUG)
                file_logger.addHandler(file_handler)
            except Exception as e:
                pass

    append_log("=== Starting Subtitle Translator Session ===")

    def load_config(config_path: str = CONFIG_FILENAME) -> configparser.ConfigParser:
        if not os.path.exists(config_path):
            append_log(f"[ERROR] Configuration file '{config_path}' not found!")
            sys.exit(f"Error: {config_path} not found.")
        cfg = configparser.ConfigParser()
        try:
            cfg.read(config_path)
            append_log(f"Loaded configuration from '{config_path}'")
        except configparser.Error as e:
            append_log(f"[ERROR] Failed to parse config: {e}")
            sys.exit(f"Error parsing {config_path}.")
        return cfg

    def call_ollama(server_url: str, endpoint_path: str, model: str, prompt: str, temperature: float = 0.2) -> str:
        url = f"{server_url.rstrip('/')}{endpoint_path}"
        data = {"model": model, "prompt": prompt, "stream": False, "temperature": temperature}
        append_log(f"[DEBUG] Calling Ollama: POST {url} | Model: {model} | Temperature: {temperature}")

        try:
            resp = requests.post(url, json=data, timeout=300)
            resp.raise_for_status()
            j = resp.json()
            return j.get("response", "")
        except requests.exceptions.Timeout:
            append_log("[ERROR] Ollama request timed out.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Ollama request failed: {e}")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Invalid JSON from Ollama: {e}")
            return ""

    def call_openai(api_key: str, api_base_url: str, model: str, prompt: str, temperature: float = 0.2) -> str:
        url = f"{api_base_url.rstrip('/')}/chat/completions"
        append_log(f"[DEBUG] Calling OpenAI: POST {url} | Model: {model} | Temperature: {temperature}")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature
        }

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=300)
            resp.raise_for_status()
            j = resp.json()
            if ("choices" in j and len(j["choices"]) > 0 and
                "message" in j["choices"][0] and "content" in j["choices"][0]["message"]):
                return j["choices"][0]["message"]["content"]
            else:
                append_log(f"[ERROR] Unexpected OpenAI response format.")
                return ""
        except requests.exceptions.Timeout:
            append_log("[ERROR] OpenAI request timed out.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] OpenAI request failed: {e}")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Invalid JSON from OpenAI: {e}")
            return ""

    def call_deepl(api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        params = {
            "auth_key": api_key,
            "text": text,
            "source_lang": source_iso.upper(),
            "target_lang": target_iso.upper(),
        }
        append_log(f"[DEBUG] Calling DeepL: {api_url} / {source_iso} -> {target_iso}")
        try:
            r = requests.post(api_url, data=params, timeout=120)
            r.raise_for_status()
            j = r.json()
            translations = j.get("translations", [])
            if translations:
                return translations[0].get("text", "")
            append_log("[WARNING] No translations from DeepL response.")
            return ""
        except requests.exceptions.Timeout:
            append_log("[ERROR] DeepL request timed out.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] DeepL request failed: {e}")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Invalid JSON from DeepL: {e}")
            return ""

    def call_google_translate(text: str, source_lang: str, target_lang: str) -> str:
        """
        Uses the Google Translate API (free web API approach) for translation.
        """
        import urllib.parse
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling Google Translate: {source_iso} -> {target_iso}")
        
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
            
            # Parse the Google Translate response format
            result = response.json()
            translated_text = ""
            
            # Google returns an array of translated segments
            for segment in result[0]:
                if segment[0]:
                    translated_text += segment[0]
            
            append_log(f"[DEBUG] Google Translate result: \"{translated_text}\"")
            return translated_text
            
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Google Translate request failed: {e}")
            return ""
        except (ValueError, KeyError, IndexError) as e:
            append_log(f"[ERROR] Failed to parse Google Translate response: {e}")
            return ""

    def call_libretranslate(text: str, source_lang: str, target_lang: str, api_url: str = "https://libretranslate.de/translate") -> str:
        """
        Uses LibreTranslate API for an alternative free translation with fallback servers.
        """
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        # List of fallback servers in case the main one fails
        fallback_servers = [
            "https://libretranslate.de/translate",
            "https://translate.argosopentech.com/translate",
            "https://libretranslate.com/translate"
        ]
        
        # If a custom URL was provided, try it first
        if api_url != "https://libretranslate.de/translate" and api_url not in fallback_servers:
            fallback_servers.insert(0, api_url)
        elif api_url in fallback_servers:
            # Move the provided URL to the front of the list
            fallback_servers.remove(api_url)
            fallback_servers.insert(0, api_url)
        
        append_log(f"[DEBUG] Calling LibreTranslate: {source_iso} -> {target_iso}")
        
        payload = {
            "q": text,
            "source": source_iso,
            "target": target_iso,
            "format": "text"
        }
        
        # Try each server until one works
        for server_url in fallback_servers:
            try:
                append_log(f"[DEBUG] Trying LibreTranslate server: {server_url}")
                response = requests.post(server_url, json=payload, timeout=8)
                response.raise_for_status()
                
                resp_json = response.json()
                result = resp_json.get("translatedText", "")
                append_log(f"[DEBUG] LibreTranslate result from {server_url}: \"{result}\"")
                return result
                
            except requests.exceptions.RequestException as e:
                append_log(f"[WARNING] LibreTranslate server {server_url} failed: {e}")
                # Continue to the next server
                continue
            except json.JSONDecodeError as e:
                append_log(f"[WARNING] Failed to parse LibreTranslate response from {server_url}: {e}")
                continue
        
        # If we get here, all servers failed
        append_log(f"[ERROR] All LibreTranslate servers failed. Unable to get translation.")
        return ""

    def call_mymemory_translate(text: str, source_lang: str, target_lang: str) -> str:
        """
        Uses MyMemory Translation API (free tier) for translation.
        """
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling MyMemory Translation: {source_iso} -> {target_iso}")
        
        base_url = "https://api.mymemory.translated.net/get"
        
        # Combine source and target languages with a pipe
        lang_pair = f"{source_iso}|{target_iso}"
        
        params = {
            "q": text,
            "langpair": lang_pair
        }
        
        try:
            response = requests.get(base_url, params=params, timeout=10)
            response.raise_for_status()
            
            resp_json = response.json()
            result = resp_json.get("responseData", {}).get("translatedText", "")
            append_log(f"[DEBUG] MyMemory result: \"{result}\"")
            return result
            
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] MyMemory request failed: {e}")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Failed to parse MyMemory response: {e}")
            return ""
            
    def get_multiple_translations(text: str, source_lang: str, target_lang: str, cfg) -> dict:
        """
        Get translations from multiple services and return them as a dictionary.
        """
        translations = {}
        
        # Add DeepL if enabled
        if cfg.getboolean("general", "use_deepl", fallback=False) and cfg.getboolean("deepl", "enabled", fallback=False):
            d_key = cfg.get("deepl", "api_key", fallback="")
            d_url = cfg.get("deepl", "api_url", fallback="")
            if d_key and d_url:
                deepl_result = call_deepl(d_key, d_url, text, source_lang, target_lang)
                if deepl_result:
                    translations["DeepL"] = deepl_result
        
        # Add Google Translate (always available as it doesn't require API key)
        if cfg.getboolean("general", "use_google", fallback=True):
            google_result = call_google_translate(text, source_lang, target_lang)
            if google_result:
                translations["Google"] = google_result
        
        # Add LibreTranslate if enabled
        if cfg.getboolean("general", "use_libretranslate", fallback=False):
            libre_api_url = cfg.get("libretranslate", "api_url", fallback="https://libretranslate.de/translate")
            libre_result = call_libretranslate(text, source_lang, target_lang, libre_api_url)
            if libre_result:
                translations["LibreTranslate"] = libre_result
        
        # Add MyMemory if enabled
        if cfg.getboolean("general", "use_mymemory", fallback=False):
            mymemory_result = call_mymemory_translate(text, source_lang, target_lang)
            if mymemory_result:
                translations["MyMemory"] = mymemory_result
        
        return translations

    import re
    def extract_json_key(response_text: str, key_name: str) -> str:
        try:
            parsed = json.loads(response_text)
            if isinstance(parsed, dict) and key_name in parsed:
                return parsed[key_name]
        except json.JSONDecodeError:
            pass
        json_pattern = r'(\{[\s\S]*?%s[\s\S]*?\})' % re.escape(key_name)
        json_matches = re.findall(json_pattern, response_text)
        for potential_json in json_matches:
            try:
                parsed = json.loads(potential_json)
                if isinstance(parsed, dict) and key_name in parsed:
                    return parsed[key_name]
            except json.JSONDecodeError:
                continue
        pattern = f'"{key_name}"\\s*:\\s*"([^"]+)"'
        match = re.search(pattern, response_text)
        if match:
            return match.group(1)
        return response_text.strip()

    def build_prompt_for_line(lines, index, cfg, deepl_translation=""):
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after  = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx   = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after  = lines[index+1:end_idx]
        line_to_translate = lines[index]

        prompt_lines = [
            f"You are an expert subtitle translator from {src_lang_full} to {tgt_lang_full}.",
            "You will see a DeepL suggestion that is usually correct, but might miss subtle context.",
            "",
            "GUIDELINES:",
            "1. If DeepL is correct, keep it. Otherwise fix it minimally.",
            "2. Avoid adding extra words or changing the meaning.",
            "3. Do not hallucinate content not in the original.",
            "",
            "--- CONTEXT ---"
        ]

        if chunk_before:
            prompt_lines.append("[PREVIOUS LINES]:")
            for i, prev_line in enumerate(chunk_before):
                prompt_lines.append(f"Line {start_idx + i + 1}: {prev_line}")

        prompt_lines.append("\n[CURRENT LINE TO TRANSLATE]:")
        prompt_lines.append(f"Line {index+1}: {line_to_translate}")

        if chunk_after:
            prompt_lines.append("\n[NEXT LINES]:")
            for i, next_line in enumerate(chunk_after):
                prompt_lines.append(f"Line {index + i + 2}: {next_line}")

        prompt_lines.append("--- END CONTEXT ---\n")
        if deepl_translation:
            prompt_lines.append(f"DEEPL SUGGESTION: \"{deepl_translation}\"")
            prompt_lines.append("If correct or close, use it. If wrong, fix it minimally.")
        else:
            prompt_lines.append("NO DEEPL SUGGESTION AVAILABLE")

        prompt_lines.append("")
        prompt_lines.append("Respond ONLY with JSON in this format: {\"translation\": \"...\"}")
        return "\n".join(prompt_lines)

    def build_critic_prompt(original_line, first_pass_translation, lines, index, cfg):
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after  = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx   = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after  = lines[index+1:end_idx]

        prompt_lines = [
            f"You are a Critic for translations from {src_lang_full} to {tgt_lang_full}.",
            "Review the line in context. If the translation is correct, return it as-is; if not, correct it minimally.",
            "--- CONTEXT ---"
        ]

        if chunk_before:
            prompt_lines.append("[PREVIOUS LINES]:")
            for i, prev_line in enumerate(chunk_before):
                prompt_lines.append(f"Line {start_idx + i + 1}: {prev_line}")

        prompt_lines.append("\n[CURRENT LINE / FIRST PASS]:")
        prompt_lines.append(f"Original: {original_line}")
        prompt_lines.append(f"Translation: {first_pass_translation}")

        if chunk_after:
            prompt_lines.append("\n[NEXT LINES]:")
            for i, next_line in enumerate(chunk_after):
                prompt_lines.append(f"Line {index + i + 2}: {next_line}")

        prompt_lines.append("--- END CONTEXT ---\n")
        prompt_lines.append("Respond ONLY with JSON: {\"corrected_translation\": \"...\"}")
        return "\n".join(prompt_lines)

    def build_specialized_critic_prompt(original_line, current_translation, lines, index, cfg, critic_type="general", pass_num=1):
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after = lines[index+1:end_idx]

        prompt_lines = [
            f"You are a specialized Critic (Pass #{pass_num}, type: {critic_type}).",
            f"Focus on {critic_type}-related issues. Return the same translation if no fix is needed.",
            "",
            "--- CONTEXT ---"
        ]

        if chunk_before:
            prompt_lines.append("[PREVIOUS LINES]:")
            for i, prev_line in enumerate(chunk_before):
                prompt_lines.append(f"Line {start_idx + i + 1}: {prev_line}")

        prompt_lines.append("\n[CURRENT LINE / TRANSLATION]:")
        prompt_lines.append(f"Original: {original_line}")
        prompt_lines.append(f"Current Translation: {current_translation}")

        if chunk_after:
            prompt_lines.append("\n[NEXT LINES]:")
            for i, next_line in enumerate(chunk_after):
                prompt_lines.append(f"Line {index + i + 2}: {next_line}")

        prompt_lines.append("--- END CONTEXT ---\n")
        prompt_lines.append("Respond ONLY with JSON: {\"corrected_translation\": \"...\"}")
        return "\n".join(prompt_lines)

    def run_llm_call(cfg, prompt, temperature):
        ollama_enabled = cfg.getboolean("ollama", "enabled", fallback=False)
        openai_enabled = cfg.getboolean("openai", "enabled", fallback=False)
        
        if ollama_enabled:
            server_url = cfg.get("ollama", "server_url", fallback="")
            endpoint_path = cfg.get("ollama", "endpoint", fallback="/api/generate")
            model_name = cfg.get("ollama", "model", fallback="")
            if server_url and model_name:
                return call_ollama(server_url, endpoint_path, model_name, prompt, temperature)
        elif openai_enabled:
            api_key = cfg.get("openai", "api_key", fallback="")
            base_url = cfg.get("openai", "api_base_url", fallback="https://api.openai.com/v1")
            model_name = cfg.get("openai", "model", fallback="")
            if api_key and model_name:
                return call_openai(api_key, base_url, model_name, prompt, temperature)
        
        append_log("[WARNING] No LLM is enabled in config, or missing credentials.")
        return ""

    def sanitize_text(text: str) -> str:
        import re
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        text = re.sub(r'<[^>]*>', '', text)
        text = re.sub(r'\[(.*?)\]', r'#BRACKET_OPEN#\1#BRACKET_CLOSE#', text)
        text = re.sub(r' +', ' ', text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.strip()

    def translate_srt(input_path, output_path, cfg):
        import pysrt
        start_time = time.time()
        from flask import flash

        translation_stats = {
            'source_language': cfg.get("general", "source_language", fallback="en"),
            'target_language': cfg.get("general", "target_language", fallback="en"),
            'total_lines': 0,
            'standard_critic_enabled': cfg.has_section("agent_critic") and cfg.getboolean("agent_critic", "enabled", fallback=False),
            'standard_critic_changes': 0,
            'multi_critic_enabled': cfg.has_section("multi_critic") and cfg.getboolean("multi_critic", "enabled", fallback=False),
            'translations': []
        }

        try:
            subs = pysrt.open(input_path, encoding='utf-8')
            lines = [s.text.strip() for s in subs]
            translation_stats['total_lines'] = len(subs)
            append_log(f"Loaded {len(subs)} subtitle entries from '{os.path.basename(input_path)}'")
        except Exception as e:
            append_log(f"[ERROR] Cannot parse SRT: {e}")
            raise

        src_lang = cfg.get("general", "source_language", fallback="en")
        tgt_lang = cfg.get("general", "target_language", fallback="en")
        
        translation_progress["total_lines"] = len(subs)
        translation_progress["status"] = "translating"

        for i, sub in enumerate(subs):
            append_log(f"Processing line {i+1}/{len(subs)}...")
            original_text = sanitize_text(sub.text)
            
            # Get translations from all enabled services
            service_translations = get_multiple_translations(original_text, src_lang, tgt_lang, cfg)
            
            translation_progress["current_line"] = i+1
            translation_progress["current"] = {
                "line_number": i+1,
                "original": original_text,
                "suggestions": service_translations,
                "first_pass": "",
                "standard_critic": "",
                "standard_critic_changed": False,
                "critics": [],
                "final": ""
            }

            line_stats = {
                'line_number': i+1,
                'original': original_text,
                'reference_translations': service_translations
            }

            # Use DeepL as the reference if available, for backward compatibility
            deepl_suggestion = service_translations.get("DeepL", "")

            # Build first pass prompt
            clean_lines = [sanitize_text(l) for l in lines]
            prompt = build_prompt_for_line(clean_lines, i, cfg, deepl_suggestion)
            
            # Call LLM for first pass translation
            llm_response = run_llm_call(cfg, prompt, cfg.getfloat("general", "temperature", fallback=0.2))

            if not llm_response:
                sub.text = original_text
                line_stats['first_pass'] = original_text
                line_stats['final'] = original_text
                translation_stats['translations'].append(line_stats)
                continue

            first_pass = extract_json_key(llm_response, "translation")
            first_pass = first_pass.replace('#BRACKET_OPEN#', '[').replace('#BRACKET_CLOSE#', ']')
            
            line_stats['first_pass'] = first_pass
            translation_progress["current"]["first_pass"] = first_pass

            live_stream_translation_info(
                "FIRST PASS",
                original_text,
                first_pass,
                i+1,
                len(subs),
                deepl_suggestion
            )

            current_translation = first_pass

            # Standard agent_critic
            if translation_stats['standard_critic_enabled']:
                critic_prompt = build_critic_prompt(original_text, current_translation, clean_lines, i, cfg)
                critic_resp = run_llm_call(cfg, critic_prompt, cfg.getfloat("agent_critic", "temperature", fallback=0.2))
                
                if critic_resp:
                    corrected = extract_json_key(critic_resp, "corrected_translation")
                    
                    if corrected and corrected != current_translation:
                        line_stats['standard_critic'] = corrected
                        line_stats['standard_critic_changed'] = True
                        translation_stats['standard_critic_changes'] += 1
                        translation_progress["current"]["standard_critic"] = corrected
                        translation_progress["current"]["standard_critic_changed"] = True

                        live_stream_translation_info(
                            "CRITIC",
                            original_text,
                            corrected,
                            i+1,
                            len(subs),
                            None,
                            "standard"
                        )

                        current_translation = corrected
                    else:
                        translation_progress["current"]["standard_critic"] = current_translation
                        translation_progress["current"]["standard_critic_changed"] = False
                        
                        live_stream_translation_info(
                            "CRITIC",
                            original_text,
                            current_translation,
                            i+1,
                            len(subs),
                            None,
                            "standard"
                        )
            
            # Multi-critic passes
            critic_reviews = {}
            if translation_stats['multi_critic_enabled']:
                for pass_num in range(1, 4):
                    sec = f"critic_pass_{pass_num}"
                    if cfg.has_section(sec) and cfg.getboolean(sec, "enabled", fallback=False):
                        ctype = cfg.get(sec, "type", fallback="general")
                        critic_prompt = build_specialized_critic_prompt(
                            original_text, current_translation, clean_lines, i, cfg, critic_type=ctype, pass_num=pass_num
                        )
                        ctemp = cfg.getfloat(sec, "temperature", fallback=0.2)
                        cresp = run_llm_call(cfg, critic_prompt, ctemp)
                        
                        if cresp:
                            cfix = extract_json_key(cresp, "corrected_translation")
                            
                            if cfix and cfix != current_translation:
                                line_stats[f'critic_{pass_num}'] = cfix
                                line_stats[f'critic_{pass_num}_changed'] = True
                                line_stats[f'critic_{pass_num}_type'] = ctype
                                critic_reviews[ctype] = cfix
                                
                                translation_progress["current"]["critics"].append({
                                    "type": ctype, 
                                    "translation": cfix, 
                                    "changed": True
                                })

                                live_stream_translation_info(
                                    f"CRITIC ({ctype})",
                                    original_text,
                                    cfix,
                                    i+1,
                                    len(subs),
                                    None,
                                    ctype
                                )
                                current_translation = cfix
                            else:
                                critic_reviews[ctype] = "No changes"
                                translation_progress["current"]["critics"].append({
                                    "type": ctype, 
                                    "translation": current_translation, 
                                    "changed": False
                                })
                                
                                live_stream_translation_info(
                                    f"CRITIC ({ctype})",
                                    original_text,
                                    current_translation,
                                    i+1,
                                    len(subs),
                                    None,
                                    ctype
                                )
            
            line_stats['critic_reviews'] = critic_reviews
            final_translation = current_translation.replace('#BRACKET_OPEN#', '[').replace('#BRACKET_CLOSE#', ']')
            line_stats['final'] = final_translation
            sub.text = final_translation

            translation_progress["current"]["final"] = final_translation

            live_stream_translation_info(
                "FINAL TRANSLATION",
                original_text,
                final_translation,
                i+1,
                len(subs)
            )

            translation_stats['translations'].append(line_stats)

        # Save the translated subtitle file
        subs.save(output_path, encoding='utf-8')
        append_log(f"[INFO] Saved translated SRT to '{os.path.basename(output_path)}'")
        
        # Generate a translation report
        report_path = os.path.join(os.path.dirname(output_path), "translation_report.txt")
        generate_translation_report(translation_stats, report_path)
        append_log(f"[INFO] Saved detailed translation report to {report_path}")
        
        end_time = time.time()
        translation_stats['processing_time'] = end_time - start_time
        processing_time_seconds = end_time - start_time
        processing_time_minutes = processing_time_seconds / 60
        
        # Print final statistics
        append_log("\n[INFO] TRANSLATION STATISTICS:")
        append_log(f"- Total lines translated: {len(subs)}")
        append_log(f"- Translation services used: {', '.join(service_translations.keys() if service_translations else ['None'])}")
        if translation_stats['standard_critic_enabled']:
            critic_changes = translation_stats['standard_critic_changes']
            append_log(f"- Lines improved by standard critic: {critic_changes} ({(critic_changes/len(subs))*100:.1f}%)")
        append_log(f"- Total processing time: {processing_time_minutes:.2f} minutes ({processing_time_seconds:.2f} seconds)")
        append_log(f"- Average time per line: {processing_time_seconds/len(subs):.2f} seconds")

        translation_progress["status"] = "done"
        translation_progress["message"] = "Translation complete."

    def generate_translation_report(stats, output_path):
        """Generate a detailed translation report with comprehensive statistics"""
        with open(output_path, 'w', encoding='utf-8') as f:
            # Report header
            f.write("="*80 + "\n")
            f.write("SUBTITLE TRANSLATION REPORT\n")
            f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("-"*80 + "\n")
            if 'input_file' in stats:
                f.write(f"Input file: {stats['input_file']}\n")
            if 'output_file' in stats:
                f.write(f"Output file: {stats['output_file']}\n")
            f.write(f"Source language: {stats['source_language']}\n")
            f.write(f"Target language: {stats['target_language']}\n")
            f.write("="*80 + "\n\n")
            
            # Translation details for each line
            f.write("TRANSLATION DETAILS\n")
            f.write("-"*80 + "\n")
            
            for entry in stats['translations']:
                line_num = entry['line_number']
                f.write(f"Line {line_num}:\n")
                f.write(f"  Original: \"{entry['original']}\"\n")
                
                # Show all machine translations (DeepL, Google, LibreTranslate, etc.)
                if 'reference_translations' in entry and entry['reference_translations']:
                    for service, translation in entry['reference_translations'].items():
                        f.write(f"  {service}: \"{translation}\"\n")
                
                # Show first pass translation
                if 'first_pass' in entry:
                    f.write(f"  First pass: \"{entry['first_pass']}\"\n")
                
                # Show standard critic result if available
                if 'standard_critic' in entry:
                    if entry.get('standard_critic_changed', False):
                        f.write(f"  Critic: \"{entry['standard_critic']}\" (CHANGED)\n")
                    else:
                        f.write(f"  Critic: No changes\n")
                
                # Show specialized critic results if available
                for i in range(1, 4):
                    critic_key = f'critic_{i}'
                    critic_type_key = f'critic_{i}_type'
                    
                    if critic_key in entry:
                        critic_type = entry.get(critic_type_key, f"Type {i}")
                        if entry.get(f'{critic_key}_changed', False):
                            f.write(f"  Critic {i} ({critic_type}): \"{entry[critic_key]}\" (CHANGED)\n")
                        else:
                            f.write(f"  Critic {i} ({critic_type}): No changes\n")
                
                # Show final translation
                if 'final' in entry:
                    f.write(f"  Final: \"{entry['final']}\"\n")
                
                f.write("-"*60 + "\n")
            
            # Enhanced summary statistics
            f.write("\nSUMMARY STATISTICS\n")
            f.write("-"*80 + "\n")
            f.write(f"Total lines translated: {stats['total_lines']}\n\n")
            
            # Translation service usage and metrics
            service_usage = {}
            service_similarity = {}
            service_selected = {}
            first_pass_matches = {}
            
            if 'translations' in stats and stats['translations']:
                # Count how many times each service was used and analyze similarity
                for entry in stats['translations']:
                    if 'reference_translations' in entry and entry['reference_translations']:
                        for service, translation in entry['reference_translations'].items():
                            # Track service usage
                            service_usage[service] = service_usage.get(service, 0) + 1
                            
                            # Count how often first pass matches each service
                            if 'first_pass' in entry and entry['first_pass'] == translation:
                                first_pass_matches[service] = first_pass_matches.get(service, 0) + 1
                            
                            # Count how often final translation matches each service
                            if 'final' in entry and entry['final'] == translation:
                                service_selected[service] = service_selected.get(service, 0) + 1
                            
                            # Calculate similarity between service and final translation
                            if 'final' in entry:
                                # Simple character-based similarity
                                service_trans = translation.strip()
                                final_trans = entry['final'].strip()
                                
                                # Basic Levenshtein distance calculation (character-level similarity)
                                max_len = max(len(service_trans), len(final_trans))
                                if max_len > 0:
                                    from difflib import SequenceMatcher
                                    similarity = SequenceMatcher(None, service_trans, final_trans).ratio() * 100
                                    
                                    if service not in service_similarity:
                                        service_similarity[service] = []
                                    service_similarity[service].append(similarity)
                
                # Print service usage statistics
                f.write("\nTRANSLATION SERVICE STATISTICS:\n")
                f.write("-"*40 + "\n")
                
                for service in sorted(service_usage.keys()):
                    usage_count = service_usage[service]
                    usage_percent = (usage_count / stats['total_lines']) * 100
                    f.write(f"{service}:\n")
                    f.write(f"  - Available for {usage_count} lines ({usage_percent:.1f}%)\n")
                    
                    # How often the LLM's first pass matched this service
                    if service in first_pass_matches:
                        match_percent = (first_pass_matches[service] / usage_count) * 100
                        f.write(f"  - First pass matched exactly: {first_pass_matches[service]} times ({match_percent:.1f}%)\n")
                    
                    # How often this service's translation was selected as final
                    if service in service_selected:
                        selected_percent = (service_selected[service] / usage_count) * 100
                        f.write(f"  - Selected as final translation: {service_selected[service]} times ({selected_percent:.1f}%)\n")
                    
                    # Average similarity to final translation
                    if service in service_similarity and service_similarity[service]:
                        avg_similarity = sum(service_similarity[service]) / len(service_similarity[service])
                        f.write(f"  - Average similarity to final translation: {avg_similarity:.1f}%\n")
                    
                    f.write("\n")
            
            # LLM first pass statistics
            f.write("\nLLM FIRST PASS STATISTICS:\n")
            f.write("-"*40 + "\n")
            
            first_pass_unchanged = 0
            first_pass_modified = 0
            
            for entry in stats['translations']:
                if 'first_pass' in entry and 'final' in entry:
                    if entry['first_pass'] == entry['final']:
                        first_pass_unchanged += 1
                    else:
                        first_pass_modified += 1
            
            total_with_first_pass = first_pass_unchanged + first_pass_modified
            if total_with_first_pass > 0:
                unchanged_percent = (first_pass_unchanged / total_with_first_pass) * 100
                modified_percent = (first_pass_modified / total_with_first_pass) * 100
                
                f.write(f"First pass translations used without changes: {first_pass_unchanged} ({unchanged_percent:.1f}%)\n")
                f.write(f"First pass translations modified by critics: {first_pass_modified} ({modified_percent:.1f}%)\n\n")
            
            # Standard critic statistics
            if stats.get('standard_critic_enabled', False):
                f.write("\nSTANDARD CRITIC STATISTICS:\n")
                f.write("-"*40 + "\n")
                
                critic_changes = stats.get('standard_critic_changes', 0)
                percentage = (critic_changes / stats['total_lines']) * 100 if stats['total_lines'] > 0 else 0
                f.write(f"Standard Critic changes: {critic_changes} ({percentage:.1f}%)\n")
                f.write(f"Standard Critic pass rate: {stats['total_lines'] - critic_changes} lines left unchanged ({100 - percentage:.1f}%)\n\n")
            
            # Multi-critic statistics
            if stats.get('multi_critic_enabled', False):
                f.write("\nMULTI-CRITIC STATISTICS:\n")
                f.write("-"*40 + "\n")
                
                # Count how many changes each specialized critic made
                critic_changes = {}
                critic_total = {}
                
                for i in range(1, 4):
                    critic_key = f'critic_{i}'
                    critic_type_key = f'critic_{i}_type'
                    changes = 0
                    total = 0
                    
                    for entry in stats['translations']:
                        if critic_key in entry:
                            total += 1
                            critic_type = entry.get(critic_type_key, f"Type {i}")
                            
                            if entry.get(f'{critic_key}_changed', False):
                                changes += 1
                                critic_changes[critic_type] = critic_changes.get(critic_type, 0) + 1
                            
                            critic_total[critic_type] = critic_total.get(critic_type, 0) + 1
                
                for critic_type in sorted(critic_total.keys()):
                    total = critic_total[critic_type]
                    changes = critic_changes.get(critic_type, 0)
                    percentage = (changes / total) * 100 if total > 0 else 0
                    f.write(f"Critic '{critic_type}':\n")
                    f.write(f"  - Changes made: {changes} ({percentage:.1f}%)\n")
                    f.write(f"  - Lines left unchanged: {total - changes} ({100 - percentage:.1f}%)\n\n")
                
                # Calculate which critic was most active
                if critic_changes:
                    most_active = max(critic_changes.items(), key=lambda x: x[1])
                    f.write(f"Most active critic: '{most_active[0]}' with {most_active[1]} changes\n\n")
            
            # Word-level statistics
            total_source_words = 0
            total_target_words = 0
            
            for entry in stats['translations']:
                if 'original' in entry:
                    total_source_words += len(entry['original'].split())
                if 'final' in entry:
                    total_target_words += len(entry['final'].split())
            
            if total_source_words > 0:
                expansion_ratio = (total_target_words / total_source_words) * 100
                f.write("\nWORD-LEVEL STATISTICS:\n")
                f.write("-"*40 + "\n")
                f.write(f"Total source words: {total_source_words}\n")
                f.write(f"Total target words: {total_target_words}\n")
                f.write(f"Target/Source ratio: {expansion_ratio:.1f}%\n\n")
            
            # Processing time
            if 'processing_time' in stats:
                processing_time = stats['processing_time']
                minutes = int(processing_time // 60)
                seconds = processing_time % 60
                
                f.write("\nPROCESSING TIME STATISTICS:\n")
                f.write("-"*40 + "\n")
                f.write(f"Total processing time: {minutes}m {seconds:.2f}s\n")
                if stats['total_lines'] > 0:
                    f.write(f"Average time per line: {processing_time / stats['total_lines']:.2f} seconds\n")
                    if total_source_words > 0:
                        f.write(f"Average time per word: {processing_time / total_source_words:.2f} seconds\n\n")
            
            f.write("="*80 + "\n")
            f.write("\nNOTE: Similarity metrics are approximate and based on character-level comparison.\n")
            f.write("Higher similarity percentages indicate closer matches between service translations and final output.\n")

    app = Flask(__name__)
    app.secret_key = os.urandom(24)

    # Updated INDEX_PAGE with a large console box
    INDEX_PAGE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>SRT Subtitle Translator</title>
        <style>
            body { font-family: sans-serif; margin: 0; padding: 0; background-color: #f4f4f4; }
            .container {
                max-width: 1200px;
                margin: 2em auto;
                background: #fff;
                padding: 2em;
                border-radius: 8px;
                box-shadow: 0 0 10px rgba(0,0,0,0.1);
            }
            h1 { text-align: center; color: #333; }
            label { display: block; margin-bottom: 0.5em; font-weight: bold; }
            input[type="file"] { border: 1px solid #ccc; padding: 0.5em; width: calc(100% - 1.2em); margin-bottom: 1em; }
            input[type="submit"] {
                background-color: #5cb85c;
                color: white;
                padding: 0.8em 1.5em;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 1em;
                width: 100%;
            }
            input[type="submit"]:hover { background-color: #4cae4c; }
            .status {
                margin-top: 1em;
                padding: 1em;
                background-color: #e9ecef;
                border-left: 5px solid #0275d8;
                display: none;
            }
            .error {
                margin-top: 1em;
                padding: 1em;
                background-color: #f8d7da;
                border-left: 5px solid #d9534f;
                color: #721c24;
            }
            .progress-box {
                margin-top: 2em;
                background: #f7f7fa;
                border-radius: 6px;
                padding: 1em;
                border: 1px solid #d0d0e0;
                font-size: 0.95em;
            }
            #console-box {
                margin-top: 2em;
                background-color: #282c34;
                color: #abb2bf;
                border-radius: 6px;
                padding: 1em;
                height: 40vh;
                overflow-y: auto;
                white-space: pre-wrap;
                font-family: monospace;
                font-size: 0.9em;
            }
            .error-line { color: #e06c75; }
            .warn-line { color: #e5c07b; }
            .info-line { color: #61afef; }
            .debug-line { color: #98c379; }
            .progress-line { color: #c678dd; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>SRT Subtitle Translator</h1>
            <form action="/upload" method="POST" enctype="multipart/form-data" id="uploadForm">
                <label for="srtfile">Select SRT File:</label>
                <input type="file" id="srtfile" name="srtfile" accept=".srt" required>
                <input type="submit" value="Upload & Translate">
            </form>
            <div id="status" class="status">Processing... Please wait. This may take several minutes.</div>
            
            <div id="progress-box" class="progress-box" style="display:none"></div>
            
            <!-- New console box that occupies a significant portion of the page -->
            <h2 style="margin-top: 2em;">Live Console Output</h2>
            <div id="console-box">Logs will appear here in real time...</div>
        </div>
        <script>
            const progressBox = document.getElementById('progress-box');
            const statusBox = document.getElementById('status');
            const consoleBox = document.getElementById('console-box');
            let scrollBottom = true;

            document.getElementById('uploadForm').addEventListener('submit', function() {
                statusBox.style.display = 'block';
                progressBox.style.display = 'block';
            });

            consoleBox.addEventListener('scroll', () => {
                // Track whether user has scrolled away from the bottom
                scrollBottom = (consoleBox.scrollHeight - consoleBox.clientHeight) <= (consoleBox.scrollTop + 10);
            });

            function renderProgressBox(data) {
                if (!data || !data.current) return '';
                let html = `<div><b>Line ${data.current_line} / ${data.total_lines}</b> - ${data.status}</div>`;
                
                // Show original text
                if (data.current.original) {
                    html += `<div style="margin-top: 10px;"><b>Original:</b> <i>${data.current.original}</i></div>`;
                }
                
                // Show all translation service suggestions
                if (data.current.suggestions && Object.keys(data.current.suggestions).length > 0) {
                    html += `<div style="margin-top: 10px;"><b>Translation Service Suggestions:</b></div>`;
                    Object.entries(data.current.suggestions).forEach(([service, translation]) => {
                        html += `<div style="margin-left: 15px;"><b>${service}:</b> <i>${translation}</i></div>`;
                    });
                }
                
                // Show first pass translation
                if (data.current.first_pass) {
                    html += `<div style="margin-top: 10px;"><b>First Pass:</b> <span style="color: #0275d8;">${data.current.first_pass}</span></div>`;
                }
                
                // Show standard critic result
                if (data.current.standard_critic) {
                    html += `<div style="margin-top: 10px;"><b>Standard Critic:</b> <span style="color: #5bc0de;">${data.current.standard_critic}</span>`;
                    if (data.current.standard_critic_changed) html += ` <span style="color:red">(changed)</span>`;
                    html += `</div>`;
                }
                
                // Show specialized critics
                if (data.current.critics && data.current.critics.length > 0) {
                    data.current.critics.forEach((c, idx) => {
                        html += `<div style="margin-top: 5px;"><b>Critic ${idx+1} (${c.type}):</b> <span style="color: #5bc0de;">${c.translation}</span>`;
                        if (c.changed) html += ` <span style="color:red">(changed)</span>`;
                        html += `</div>`;
                    });
                }
                
                // Show final translation
                if (data.current.final) {
                    html += `<div style="margin-top: 10px;"><b>Final Translation:</b> <span style="color: #5cb85c; font-weight: bold;">${data.current.final}</span></div>`;
                }
                
                return html;
            }

            function pollProgress() {
                fetch('/api/progress')
                  .then(r => r.json())
                  .then(data => {
                    if (data.status !== 'idle') {
                        progressBox.style.display = 'block';
                        progressBox.innerHTML = renderProgressBox(data);
                    } else {
                        progressBox.style.display = 'none';
                    }
                  })
                  .catch(err => console.error(err));
            }

            function colorizeLogs(logText) {
                if (!logText) return '';
                return logText
                   .replace(/\[ERROR\].*$/gm, m => `<span class="error-line">${m}</span>`)
                   .replace(/\[WARNING\].*$/gm, m => `<span class="warn-line">${m}</span>`)
                   .replace(/\[INFO\].*$/gm, m => `<span class="info-line">${m}</span>`)
                   .replace(/\[DEBUG\].*$/gm, m => `<span class="debug-line">${m}</span>`);
            }

            function pollLogs() {
                fetch('/api/logs')
                  .then(r => r.json())
                  .then(data => {
                    const colored = colorizeLogs(data.logs);
                    consoleBox.innerHTML = colored;
                    if (scrollBottom) {
                        consoleBox.scrollTop = consoleBox.scrollHeight;
                    }
                  })
                  .catch(err => {
                    console.error("Error fetching logs:", err);
                  });
            }

            // Poll progress and logs
            setInterval(pollProgress, 2000);
            setInterval(pollLogs, 2000);
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
    </head>
    <body>
        <h1>Translation Debug Console</h1>
        <pre id="logBox">Loading logs...</pre>
        <script>
          const logBox = document.getElementById('logBox');
          let isScrolled = true;
          logBox.addEventListener('scroll', function() {
              isScrolled = (logBox.scrollHeight - logBox.clientHeight) <= (logBox.scrollTop + 10);
          });
          function fetchLogs() {
            fetch('/logs')
              .then(resp => resp.text())
              .then(txt => {
                logBox.textContent = txt;
                if(isScrolled) {
                    logBox.scrollTop = logBox.scrollHeight;
                }
              })
              .catch(error => {
                  console.error("Error fetching logs:", error);
              });
          }
          fetchLogs();
          setInterval(fetchLogs, 3000);
        </script>
    </body>
    </html>
    """

    translation_progress = {
        "current_line": 0,
        "total_lines": 0,
        "status": "idle",
        "message": "",
        "current": {
            "line_number": 0,
            "original": "",
            "deepl": "",
            "first_pass": "",
            "standard_critic": "",
            "standard_critic_changed": False,
            "critics": [],
            "final": ""
        }
    }

    app = Flask(__name__)
    app.secret_key = os.urandom(24)

    @app.route("/")
    def index():
        return render_template_string(INDEX_PAGE)

    @app.route("/logs")
    def logs_page():
        # This route will render the big log viewer template in a separate tab
        return render_template_string(LOG_VIEWER_TEMPLATE)

    @app.route("/config")
    def config_page():
        return render_template_string(CONFIG_EDITOR_TEMPLATE)

    @app.route("/console")
    def console():
        return render_template_string(CONSOLE_PAGE_TEMPLATE)

    @app.route('/api/logs')
    def api_logs():
        return jsonify({"logs": get_logs()})

    @app.route('/api/config')
    def api_config():
        c = load_config()
        config_dict = {}
        for section in c.sections():
            config_dict[section] = {}
            for key, value in c[section].items():
                if value.lower() in ('true', 'false'):
                    config_dict[section][key] = c.getboolean(section, key)
                else:
                    # Try to parse as number
                    if value.replace('.', '', 1).isdigit():
                        try:
                            if '.' in value:
                                config_dict[section][key] = float(value)
                            else:
                                config_dict[section][key] = int(value)
                        except ValueError:
                            config_dict[section][key] = value
                    else:
                        config_dict[section][key] = value
        return jsonify({"config": config_dict})

    @app.route('/api/config', methods=['POST'])
    def update_config():
        try:
            data = request.json
            updated_config = data['config']
            current_config = load_config()
            for section, items in updated_config.items():
                if not current_config.has_section(section):
                    current_config.add_section(section)
                for k, v in items.items():
                    if isinstance(v, bool):
                        v = 'true' if v else 'false'
                    else:
                        v = str(v)
                    current_config[section][k] = v
            with open(CONFIG_FILENAME, 'w') as f:
                current_config.write(f)
            append_log("[INFO] Configuration updated.")
            return jsonify({"success": True})
        except Exception as e:
            append_log(f"[ERROR] Failed to update config: {e}")
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/progress')
    def get_progress():
        return jsonify(translation_progress)

    @app.route("/upload", methods=["POST"])
    def upload():
        from flask import flash
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
        try:
            tempd = tempfile.mkdtemp(prefix="srt_translate_")
            TEMP_DIRS_TO_CLEAN.add(tempd)
            append_log(f"Temp directory: {tempd}")
        except Exception as e:
            append_log(f"[ERROR] {e}")
            flash("Server error creating temp dir.", "error")
            return redirect(url_for("index"))

        input_path = os.path.join(tempd, file.filename)
        file.save(input_path)
        append_log(f"Received SRT: {file.filename} -> {input_path}")

        cfg = load_config()
        src_lang = cfg.get("general", "source_language", fallback="original").strip('"\' ')
        tgt_lang = cfg.get("general", "target_language", fallback="translated").strip('"\' ')
        src_iso = get_iso_code(src_lang)
        tgt_iso = get_iso_code(tgt_lang)

        base, ext = os.path.splitext(file.filename)
        out_base = base
        replaced = False
        patterns = [
            f'.{src_iso}.', f'.{src_iso}-', f'.{src_iso}_',
            f'{src_iso}.', f'-{src_iso}.', f'_{src_iso}.'
        ]
        import re
        for pat in patterns:
            if pat in base.lower():
                newpat = pat.replace(src_iso, tgt_iso)
                out_base = re.sub(pat, newpat, base, flags=re.IGNORECASE)
                replaced = True
                break
        if not replaced:
            out_base = f"{base}.{tgt_iso}"
        out_filename = secure_filename(out_base + ext)
        output_path = os.path.join(tempd, out_filename)

        try:
            translate_srt(input_path, output_path, cfg)
        except Exception as e:
            append_log(f"[ERROR] Translation failed: {e}")
            flash(f"Translation failed: {e}", "error")
            return redirect(url_for("index"))

        return redirect(url_for("download_file", folder=os.path.basename(tempd), filename=out_filename))

    @app.route("/download/<path:folder>/<path:filename>")
    def download_file(folder, filename):
        base_temp = tempfile.gettempdir()
        full_dir = os.path.join(base_temp, folder)
        if not os.path.normpath(full_dir).startswith(os.path.normpath(base_temp)):
            append_log("[SECURITY] Invalid directory access attempt.")
            return "Invalid directory", 400
        file_path = os.path.join(full_dir, filename)
        if not os.path.exists(file_path):
            append_log(f"[ERROR] File not found for download: {file_path}")
            return "File not found or expired", 404
        append_log(f"Serving file: {file_path}")
        return send_from_directory(full_dir, filename, as_attachment=True)

    host = cfg_for_logger.get("general", "host", fallback="127.0.0.1")
    port = cfg_for_logger.getint("general", "port", fallback=5000)

    print("="*40)
    print(f" Subtitle Translator UI running at http://{host}:{port}/ ")
    print(" Press CTRL+C to stop the server.")
    print("="*40)

    try:
        app.run(host=host, port=port, debug=False)
    except Exception as e:
        append_log(f"[ERROR] Flask server failed: {e}")
        sys.exit(1)
    finally:
        cleanup_temp_dirs()

def main():
    setup_environment_and_run()

if __name__ == "__main__":
    main()
