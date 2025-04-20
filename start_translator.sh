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
REQUIRED_PACKAGES="Flask pysrt requests colorama"
MISSING_PACKAGES=""

for package in $REQUIRED_PACKAGES; do
    if ! $PYTHON -c "import $package" &>/dev/null; then
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

# Run the translator application
print_message "$BLUE" "=========================================="
print_message "$GREEN" "Starting Subtitle Translator..."
print_message "$BLUE" "=========================================="
print_message "$YELLOW" "If your browser doesn't open automatically, navigate to http://127.0.0.1:5000"
print_message "$YELLOW" "Press Ctrl+C to stop the application."
print_message "$BLUE" "=========================================="

# Run the application (using the new app.py instead of the old file)
$PYTHON app.py

# Deactivate virtual environment at exit
deactivate 2>/dev/null || true