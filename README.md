# Subtitle Translator 🎬🌍

**Subtitle Translator** is a powerful and versatile tool designed to translate subtitle files seamlessly. It offers a user-friendly web interface, supports various subtitle formats, integrates with multiple translation services (including local LLMs), and even provides video transcription capabilities. Enhanced with features like live translation monitoring, bulk processing, and context-aware translation using TMDB and Fandom/MediaWiki wikis, this tool aims to be a comprehensive solution for all your subtitle translation needs.

![Subtitle Translator Logo](static/images/sublogo.png)

## ✨ Key Features

* **Versatile Format Support**: Translates `.srt`, `.ass`, and `.vtt` subtitle files.
* **Intuitive Web Interface**: Easy-to-use UI for uploading files, managing translations, configuring settings, and viewing logs.
* **Multiple Translation Services**:
    * Supports popular services like DeepL, Google Translate.
    * Integrates with local LLMs via Ollama and LM Studio for privacy-focused or custom translations.
    * OpenAI (GPT) and MyMemory translation options.
    * Service priority can be configured.
* **Video Transcription**: Transcribe audio from video files directly into SRT subtitle files using Faster-Whisper (supports remote server or local execution).
* **Live Translation Monitoring**:
    * Real-time progress updates in the web UI, showing current line, overall progress, and individual service suggestions.
    * Dedicated terminal-based live viewer (`live_translation_viewer.py`) for detailed log monitoring and report summaries.
* **Bulk Processing**:
    * Scan entire directories and translate all supported subtitle files within them.
    * Automatically identify source language files and skip already translated ones.
* **Advanced Contextual Translation**:
    * **TMDB Integration**: Fetches movie/show metadata (plot, genres, cast) from The Movie Database to provide better context to LLMs.
    * **Wiki Terminology**: Pulls character names, locations, and specific jargon from Fandom/MediaWiki sites to improve translation accuracy for niche content.
    * **Custom Glossary**: Define special meanings for words or phrases via `files/meaning.json` or the web UI to ensure consistent and correct terminology.
* **Translation Quality Critic**: Utilizes LLMs (Ollama or LM Studio) to evaluate the quality of translations, provide feedback, and even suggest improved versions.
* **Secure File Handling**:
    * Secure file browser for selecting subtitle and video files directly from the host system, with configurable allowed/denied paths.
* **Comprehensive Configuration**:
    * Easy-to-use web-based configuration editor (`/config`).
    * Detailed settings in `config.ini` for all aspects of the application.
* **Logging & Debugging**:
    * View application logs directly in the web UI (`/logs`).
    * Debug mode for verbose logging, including full LLM prompts.

## 📂 Project Structure

├── app.py                     # Main Flask application├── config.ini.example         # Example configuration file├── start_translator.sh        # Setup and startup script├── requirements.txt           # Python dependencies├── README.md                  # This file├── LICENSE                    # Project license├── py/                        # Backend Python modules│   ├── config_manager.py      # Handles config.ini│   ├── subtitle_processor.py  # Core subtitle parsing and translation logic│   ├── translation_service.py # Facade for translation APIs, TMDB, Wiki│   ├── critic_service.py      # LLM-based translation critic│   ├── video_transcriber.py   # Video to SRT transcription│   ├── local_whisper.py       # Local Whisper implementation│   ├── wiki_terminology.py    # Fandom/MediaWiki integration│   ├── secure_browser.py      # Secure file browsing logic│   ├── logger.py              # Logging setup│   └── wyoming_client.py      # Client for Wyoming protocol (Whisper)├── static/                    # Frontend static assets│   ├── css/                   # Stylesheets (main.css)│   ├── js/                    # JavaScript files (main.js, config_editor.js, etc.)│   └── images/                # Images like the logo├── templates/                 # HTML templates for Flask│   ├── index.html             # Main translation page│   ├── config_editor.html     # Configuration page│   └── log_viewer.html        # Log viewing page├── cache/                     # Temporary cache files (e.g., for subtitles, wikis)│   └── wikis/                 # Cached wiki terminology├── subs/                      # Default output directory for translated subtitles├── files/                     # Supporting files│   └── meaning.json           # User-defined special meanings/glossary├── live_translation_viewer.py # Terminal tool for live monitoring├── wyoming_chunk2srt.py       # Script for Wyoming protocol STT chunk processing└── ... (other utility scripts and files)
## ⚙️ Prerequisites

* **Python**: Version 3.8 or higher is recommended.
* **Bash Shell**: Required for running the setup script (`start_translator.sh`) on macOS, Linux, or Windows (e.g., via Git Bash or WSL).
* **FFmpeg**: Required for video transcription (extracting audio from video files). The `start_translator.sh` script will attempt to install it if missing.

## 🚀 Installation & Setup

1.  **Clone the Repository**:
    ```bash
    git clone <your-repository-url>
    cd subtitle-translator
    ```

2.  **Run the Setup and Start Script**:
    ```bash
    ./start_translator.sh
    ```
    This script will:
    * Check for and attempt to install essential system dependencies like Python and FFmpeg.
    * Set up a Python virtual environment (e.g., in `venv_subtrans/`).
    * Install all required Python packages from `requirements.txt` (e.g., Flask, requests, srt, Pillow, faster-whisper, CTranslate2).
    * Create a default `config.ini` from `config.ini.example` if it doesn't exist.
    * Start the Subtitle Translator web application.

    *Note for Windows users*: You might need to restart your terminal or system for PATH changes (especially for FFmpeg) to take effect if installed by the script.

## ▶️ Running the Application

1.  **Start the Application**:
    Execute the startup script from the project's root directory:
    ```bash
    ./start_translator.sh
    ```
2.  **Access the Web UI**:
    Open your web browser and navigate to `http://127.0.0.1:PORT` (the default port is `5089` but can be changed in `config.ini`).
3.  **Stopping the Application**:
    Press `Ctrl+C` in the terminal where the script is running.

## 📖 Usage

The primary way to use Subtitle Translator is through its web interface.

### Single File Translation
* Navigate to the main "Translate" page.
* **Upload a file**: Click "Choose File" to upload an `.srt`, `.ass`, or `.vtt` file from your computer.
* **Or select a host file**: Click "Browse" next to "Select File from Host" to use the secure file browser to pick a file from allowed directories on the server.
* **Choose Languages**: Select the source and target languages for translation. Defaults are read from `config.ini`.
* **Special Meanings (Optional)**: Expand the "Special Meanings" section to define custom translations for specific words or phrases. This is useful for character names, technical jargon, or correcting common mistranslations by the AI. These can also be pre-configured in `files/meaning.json`.
* Click "Start Translation". Progress will be shown in the "Translation Progress" card.

### Video Transcription to SRT
* On the main "Translate" page, find the "Transcribe Video File" card.
* Click "Browse Video" to select a video file from the host system.
* Optionally, select the language of the video audio. Leaving it blank will use auto-detection (if supported by the Whisper model).
* Click "Start Transcription". The system will extract audio, transcribe it using Whisper (via a remote server or local processing as configured), and save the result as an SRT file in the `subs/` directory.

### Bulk Directory Translation
* On the main "Translate" page, find the "Bulk Translate Directory" card.
* Click "Browse Directory" to open the inline file browser. Navigate to the desired directory.
* Once inside the target directory, click "Select this directory" (this option appears within the browser).
* Confirm the selected path appears next to the "Browse Directory" button.
* Click the "Translate This Directory" button (which becomes active/visible after selection or is part of the browser actions).
* The application will scan the directory (and its subdirectories) for subtitle files. It will attempt to identify source language files and translate them if a target language version isn't already present.
* Progress for bulk operations is also displayed in the "Translation Progress" card, and a ZIP file of all translated subtitles will be available for download upon completion.

### Configuration
* **Web UI**: Navigate to the "Settings" tab (or `/config`) to access the web-based configuration editor. Changes made here are saved to `config.ini`.
* **`config.ini` File**: For more advanced settings or direct editing, modify the `config.ini` file in the project root. If it doesn't exist, it's created from `config.ini.example` on first run.
    * See the `config.ini.example` file for a comprehensive list of available options and their descriptions.

### Log Viewing
* Navigate to the "Logs" tab (or `/logs`) in the web UI to view application logs. You can refresh, clear, and filter logs.

### Live Translation Viewer (Terminal)
For developers or users who prefer a terminal-based view:
* **Monitor Live Log**: `python live_translation_viewer.py monitor`
* **View Translation Report Summary**: `python live_translation_viewer.py report`
    (This reads from `translation_report.txt` which is generated by some translation processes).

## 🛠️ Configuration Details

The application's behavior is primarily controlled by `config.ini`.

* **`[general]`**:
    * `source_language`, `target_language`: Default languages for translation.
    * `debug_mode`: `true` for verbose logging (including LLM prompts), `false` otherwise.
    * `host`, `port`: Network host and port for the web application.
* **`[whisper]`**:
    * `use_remote_whisper`: `true` to use a remote Faster-Whisper server, `false` for local processing.
    * `server_url`: URL of the remote Faster-Whisper API server (if `use_remote_whisper` is true).
    * Local Whisper settings (model, device, compute_type) are also available if running locally.
* **`[translation]`**:
    * `service_priority`: Comma-separated list of translation services to try in order (e.g., `deepl,ollama,google`).
* **LLM Services (`[ollama]`, `[lmstudio]`, `[openai]`)**:
    * `enabled`: `true` or `false`.
    * `server_url` / `api_base_url`: API endpoint.
    * `model`: Specific model name to use.
    * `api_key`: For services like OpenAI and DeepL.
    * Performance parameters for Ollama (`num_gpu`, `num_thread`, `num_ctx`, `use_mmap`, `use_mlock`).
* **`[deepl]`**: API key and URL.
* **`[tmdb]`**:
    * `enabled`: `true` to fetch media context from TMDB.
    * `api_key`: Your TMDB API v3 key.
* **`[wiki_terminology]`**:
    * `enabled`: `true` to fetch terminology from Fandom/MediaWiki sites.
    * `cache_expiry_days`, `max_terms`, `manual_wiki_override`.
* **`[critic]` / `[agent_critic]`**:
    * `enabled`: `true` to use an LLM to critique and potentially revise translations.
    * `service`: `ollama` or `lmstudio`.
    * `model`, `temperature`.
* **`[file_browser]`**:
    * `allowed_paths`: Comma-separated list of absolute base paths the file browser is allowed to access on the host system.
    * `denied_patterns`: Comma-separated list of glob patterns or specific paths to deny access to.

* **`files/meaning.json`**:
    A JSON file to define custom translations for specific words or phrases, ensuring consistency for names, jargon, or common mistranslations. Example:
    ```json
    [
      {
        "word": "Mutes",
        "meaning": "Mutated animals with enhanced intelligence"
      },
      {
        "word": "Prahm",
        "meaning": "A special reconciliation party (prom)"
      }
    ]
    ```

## 📦 Dependencies

Key Python dependencies are managed by `requirements.txt` and include:

* Flask
* requests
* srt
* ffmpeg-python
* Pillow
* Wyoming protocol client (`wyoming>=0.4.0`)
* For local Whisper: `faster-whisper`, `ctranslate2`, `torch`
* For Wiki Terminology: `beautifulsoup4`, `mwparserfromhell`

The `start_translator.sh` script handles the installation of these dependencies within a virtual environment.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit pull requests, create issues for bugs or feature requests.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgements

* This project leverages several powerful open-source libraries and tools.
* Portions of the startup script and some utility functions may have been inspired or assisted by AI code generation tools like GitHub Copilot.
