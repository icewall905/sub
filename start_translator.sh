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

# Check and install system packages if missing
install_system_package() {
    local package_name=$1
    
    # Check if package is installed
    if ! command -v "$package_name" &>/dev/null; then
        print_message "$YELLOW" "$package_name not found. Attempting to install..."
        
        # Check for sudo access
        local SUDO=""
        if command -v sudo &>/dev/null && [ "$EUID" -ne 0 ]; then
            SUDO="sudo"
        fi
        
        # Try to install using the appropriate package manager
        if command -v winget &>/dev/null; then
            # Windows with winget
            print_message "$YELLOW" "Installing using winget..."
            case "$package_name" in
                "ffmpeg")
                    winget install -e --id Gyan.FFmpeg
                    ;;
                "python3")
                    winget install -e --id Python.Python.3
                    ;;
                *)
                    winget install -e "$package_name"
                    ;;
            esac
            # Add to PATH if necessary for Windows installs
            if [ "$package_name" = "ffmpeg" ] && ! command -v ffmpeg &>/dev/null; then
                print_message "$YELLOW" "Adding FFmpeg to PATH for this session..."
                # Common FFmpeg install locations on Windows
                for path in "/c/ffmpeg/bin" "$PROGRAMFILES/FFmpeg/bin" "$LOCALAPPDATA/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-*-essentials_build/bin"; do
                    if [ -d "$path" ]; then
                        export PATH="$PATH:$path"
                        break
                    fi
                done
            fi
        elif command -v apt-get &>/dev/null; then
            print_message "$YELLOW" "Installing using apt-get..."
            $SUDO apt-get update -qq
            $SUDO apt-get install -y "$package_name"
        elif command -v dnf &>/dev/null; then
            print_message "$YELLOW" "Installing using dnf..."
            $SUDO dnf install -y "$package_name"
        elif command -v yum &>/dev/null; then
            print_message "$YELLOW" "Installing using yum..."
            $SUDO yum install -y "$package_name"
        elif command -v pacman &>/dev/null; then
            print_message "$YELLOW" "Installing using pacman..."
            $SUDO pacman -S --noconfirm "$package_name"
        elif command -v brew &>/dev/null; then
            print_message "$YELLOW" "Installing using brew..."
            brew install "$package_name"
        else
            print_message "$RED" "Could not find a supported package manager to install $package_name."
            print_message "$RED" "Please install $package_name manually and run this script again."
            exit 1
        fi
        
        # Verify installation was successful
        if command -v "$package_name" &>/dev/null; then
            print_message "$GREEN" "$package_name was successfully installed."
        else
            print_message "$YELLOW" "Command '$package_name' not found after installation. This might be expected if it needs a PATH update or system restart."
            # For Windows particularly, we might need to inform the user
            if command -v winget &>/dev/null; then
                print_message "$YELLOW" "On Windows, you may need to restart your terminal or system for PATH changes to take effect."
                print_message "$YELLOW" "Attempting to continue, but you may need to restart and run this script again if there are errors."
            else
                print_message "$RED" "Failed to install $package_name. Please install it manually."
                exit 1
            fi
        fi
    else
        print_message "$GREEN" "$package_name is already installed."
    fi
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
    print_message "$YELLOW" "Python 3 not found. Attempting to install..."
    install_system_package "python3"
    PYTHON="python3"
fi

# Get Python version
PY_VERSION=$($PYTHON --version | cut -d' ' -f2)
print_message "$GREEN" "Using Python $PY_VERSION"

# Check system dependencies
print_message "$YELLOW" "Checking system dependencies..."

# Install FFmpeg and ffprobe if missing
install_system_package "ffmpeg"

# Check for ffprobe (sometimes separately packaged)
if ! command -v ffprobe &>/dev/null; then
    print_message "$YELLOW" "ffprobe not found. It's usually part of the FFmpeg package."
    print_message "$YELLOW" "Attempting to install ffmpeg-tools or similar package..."
    
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y ffmpeg
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y ffmpeg-tools
    elif command -v brew &>/dev/null; then
        brew install ffmpeg
    fi
    
    # Verify ffprobe is now available
    if command -v ffprobe &>/dev/null; then
        print_message "$GREEN" "ffprobe is now available."
    else
        print_message "$YELLOW" "Warning: ffprobe still not found. Embedded subtitle detection may be limited."
    fi
else
    print_message "$GREEN" "ffprobe is already installed."
fi

# Environment directory
VENV_DIR="venv_subtrans"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    print_message "$YELLOW" "Creating virtual environment in $VENV_DIR..."
    
    # Check if venv module is available
    if ! $PYTHON -c "import venv" &>/dev/null; then
        print_message "$YELLOW" "Python venv module not found. Attempting to install..."
        if command -v apt-get &>/dev/null; then
            sudo apt-get install -y python3-venv
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y python3-venv
        elif command -v brew &>/dev/null; then
            brew install python
        fi
    fi
    
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

# Upgrade pip first
print_message "$YELLOW" "Upgrading pip to latest version..."
pip install --upgrade pip

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
    print_message "$GREEN" "All required Python packages are already installed."
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
print_message "$GREEN" "All dependencies installed successfully."
print_message "$GREEN" "Starting Subtitle Translator application..."

# Run the application
$PYTHON app.py

# Deactivate virtual environment at exit
deactivate 2>/dev/null || true