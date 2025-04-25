#!/bin/bash

# Script to set up the environment and run the subtitle translator
# Author: GitHub Copilot
# Created: 2025-04-17

set -e  # Exit on error

# Color codes for prettier output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Print a colored message
print_message() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

print_message "$BLUE" "=========================================="
print_message "$BLUE" "  Subtitle Translator Setup Script"
print_message "$BLUE" "=========================================="

# Check for system dependencies (FFmpeg)
check_ffmpeg() {
    if ! command -v ffmpeg &>/dev/null; then
        print_message "$YELLOW" "FFmpeg not found, which is required for extracting embedded subtitles."
        
        # Detect the package manager and suggest installation command
        if command -v apt-get &>/dev/null; then
            print_message "$YELLOW" "Please install FFmpeg using: sudo apt-get install ffmpeg"
        elif command -v dnf &>/dev/null; then
            print_message "$YELLOW" "Please install FFmpeg using: sudo dnf install ffmpeg"
        elif command -v yum &>/dev/null; then
            print_message "$YELLOW" "Please install FFmpeg using: sudo yum install ffmpeg"
        elif command -v pacman &>/dev/null; then
            print_message "$YELLOW" "Please install FFmpeg using: sudo pacman -S ffmpeg"
        elif command -v brew &>/dev/null; then
            print_message "$YELLOW" "Please install FFmpeg using: brew install ffmpeg"
        else
            print_message "$YELLOW" "Please install FFmpeg using your system's package manager."
        fi
        
        # Ask user if they want to continue anyway
        read -p "Continue without FFmpeg? Embedded subtitle extraction will not work. (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_message "$RED" "Setup aborted. Please install FFmpeg and run the script again."
            exit 1
        fi
        print_message "$YELLOW" "Continuing without FFmpeg. Embedded subtitle extraction will be disabled."
    else
        print_message "$GREEN" "FFmpeg is installed and available."
        
        # Check ffprobe as well (part of FFmpeg but sometimes installed separately)
        if ! command -v ffprobe &>/dev/null; then
            print_message "$YELLOW" "Warning: ffprobe not found. It's usually part of the FFmpeg package."
            print_message "$YELLOW" "Embedded subtitle detection may be limited."
        else
            print_message "$GREEN" "ffprobe is installed and available."
        fi
    fi
}

# Check Python installation
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    print_message "$RED" "Error: Python not found. Please install Python 3."
    exit 1
fi

# Get Python version
PY_VERSION=$($PYTHON --version | cut -d' ' -f2)
print_message "$GREEN" "Using Python $PY_VERSION"

# Check system dependencies
print_message "$YELLOW" "Checking system dependencies..."
check_ffmpeg

# Environment directory
VENV_DIR="venv_subtrans"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    print_message "$YELLOW" "Creating virtual environment in $VENV_DIR..."
    $PYTHON -m venv "$VENV_DIR"
    print_message "$GREEN" "Virtual environment created."
else
    print_message "$GREEN" "Using existing virtual environment: $VENV_DIR"
fi

# Determine activation script based on OS
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    # Windows
    ACTIVATE_SCRIPT="$VENV_DIR/Scripts/activate"
else
    # Unix-like (macOS, Linux)
    ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"
fi

# Activate the virtual environment and install dependencies
print_message "$YELLOW" "Activating virtual environment and checking dependencies..."
source "$ACTIVATE_SCRIPT"

# Check if required packages are installed
REQUIRED_PACKAGES="Flask pysrt requests colorama beautifulsoup4 mwparserfromhell"
MISSING_PACKAGES=""

for package in $REQUIRED_PACKAGES; do
    if ! $PYTHON -c "import ${package/beautifulsoup4/bs4}" &>/dev/null; then
        if [ -z "$MISSING_PACKAGES" ]; then
            MISSING_PACKAGES="$package"
        else
            MISSING_PACKAGES="$MISSING_PACKAGES $package"
        fi
    fi
done

# Install missing packages if needed
if [ ! -z "$MISSING_PACKAGES" ]; then
    print_message "$YELLOW" "Installing missing packages: $MISSING_PACKAGES"
    pip install $MISSING_PACKAGES
    print_message "$GREEN" "Dependencies installed successfully."
else
    print_message "$GREEN" "All required packages are already installed."
fi

# Check if config.ini exists, create from example if not
if [ ! -f "config.ini" ] && [ -f "config.ini.example" ]; then
    print_message "$YELLOW" "Creating default config.ini from example..."
    cp config.ini.example config.ini
    print_message "$GREEN" "Created config.ini. You may want to edit this file to customize settings."
fi

# Get server port from config.ini if possible
PORT=5089
if [ -f "config.ini" ]; then
    # Extract port from config.ini using grep and cut
    CONFIG_PORT=$(grep -E "^\s*port\s*=" config.ini | cut -d'=' -f2 | tr -d '[:space:]')
    if [ ! -z "$CONFIG_PORT" ]; then
        PORT=$CONFIG_PORT
    fi
fi

# Just a simple setup message - the app.py will handle the full welcome message with correct port
print_message "$GREEN" "Dependencies installed successfully."

# Run the application (using the new app.py instead of the old file)
$PYTHON app.py

# Deactivate virtual environment at exit
deactivate 2>/dev/null || true