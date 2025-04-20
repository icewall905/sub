# Subtitle Translator

A tool for translating subtitle files with a web interface and live translation monitoring.

## Prerequisites
- Python 3.x (recommended: 3.8+)
- (macOS/Linux/Windows) Bash shell for running the setup script

## Quick Start

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone <repo-url>
   cd subtitle-translator
   ```

2. **Run the setup and start script:**
   ```bash
   ./start_translator.sh
   ```
   - This script will:
     - Set up a Python virtual environment (if not already present)
     - Install required dependencies (`Flask`, `pysrt`, `requests`, `colorama`)
     - Create a default `config.ini` if missing
     - Start the web application

3. **Open your browser:**
   - Visit [http://127.0.0.1:5000](http://127.0.0.1:5000) to use the subtitle translator web UI.

4. **Stopping the app:**
   - Press `Ctrl+C` in the terminal to stop the server.

## Additional Tools
- **Live Translation Viewer:**
  - For real-time log monitoring or translation report summaries, use:
    ```bash
    python live_translation_viewer.py monitor
    python live_translation_viewer.py report
    ```

## Configuration
- Edit `config.ini` to customize settings. If it does not exist, it will be created from `config.ini.example` on first run.

## License
MIT License. See [LICENSE](LICENSE) for details.