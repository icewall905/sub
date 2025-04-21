#!/usr/bin/env python3
"""
Live Translation Viewer - Enhanced terminal display for subtitle translation

This script is designed to work alongside the subtitle-translator application
to provide enhanced real-time visualization of the translation process in the terminal.
It uses ANSI color codes and clear formatting to make the translation process more visible.
"""

import os
import sys
import platform
import time
import re
import json

# Define ANSI color codes for terminal output
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
                # Windows 10 version 1607 or later supports ANSI escape sequences
                # through the Windows Console
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

def clear_screen():
    """Clear the terminal screen for a fresh display."""
    if platform.system() == 'Windows':
        os.system('cls')
    else:
        os.system('clear')

def display_translation_status(line_number, original, translations, current_result=None, first_pass=None, critic=None, final=None):
    """
    Display translation status for a single line in the requested format.
    
    Args:
        line_number: The current line number being processed
        original: The original text
        translations: Dictionary of translations from different services
        current_result: Current translation result (if any)
        first_pass: First pass translation (if any)
        critic: Critic-revised translation (if any)
        final: Final translation (if any)
    """
    # Define ANSI color codes
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    
    # Check if terminal supports color
    supports_color = Colors.terminal_supports_color()
    if not supports_color:
        # Fall back to plain text if color isn't supported
        RESET = BOLD = GREEN = BLUE = YELLOW = CYAN = MAGENTA = RED = ""
    
    # Create a separator line
    separator = f"{CYAN}{'-' * 60}{RESET}"
    
    # Print line header
    print(separator)
    print(f"Line {line_number}:")
    print(f"  Original: \"{original}\"")
    
    # Print translations from different services
    for service, translation in translations.items():
        if translation:
            service_name = service.capitalize()
            print(f"  {service_name}: \"{translation}\"")
    
    # Print first pass translation if available
    if first_pass:
        print(f"  First pass: \"{first_pass}\"")
    
    # Print critic evaluation if available with (CHANGED) indication if it differs from first_pass
    if critic:
        critic_changed = critic != first_pass if first_pass else False
        change_indicator = " (CHANGED)" if critic_changed else ""
        print(f"  Critic: \"{critic}\"{change_indicator}")
    
    # Print final translation if available
    if final:
        print(f"  Final: \"{final}\"")
    
    print(separator)
    sys.stdout.flush()

def live_stream_translation_info(stage, original, translation, current_idx, total_lines, translations=None, first_pass=None, critic=None, final=None):
    """Display live translation information in requested format."""
    # Get dictionary of translations or create empty one
    translations = translations or {}
    
    # Clear screen for a clean display
    clear_screen()
    
    # Display translation status in the requested format
    display_translation_status(
        current_idx, 
        original, 
        translations,
        translation,
        first_pass,
        critic,
        final
    )
    
    # Force flush stdout to ensure immediate display
    sys.stdout.flush()

def show_translation_comparison(original, stages, source_lang="", target_lang=""):
    """
    Show a comparison of all stages of translation in a clear, visual way.
    
    Args:
        original: The original text
        stages: Dictionary of translation stages and their outputs
        source_lang: Source language code
        target_lang: Target language code
    """
    # Define ANSI color codes
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    
    clear_screen()
    
    # Create a header
    print(f"{CYAN}{'=' * 80}{RESET}")
    print(f"{BOLD}{CYAN}SUBTITLE TRANSLATION STAGES COMPARISON{RESET}")
    if source_lang and target_lang:
        print(f"{BOLD}Language: {BLUE}{source_lang}{RESET} → {GREEN}{target_lang}{RESET}")
    print(f"{CYAN}{'=' * 80}{RESET}\n")
    
    # Original text
    print(f"{BOLD}ORIGINAL TEXT:{RESET}")
    print(f"{BLUE}{original}{RESET}\n")
    
    # Show each stage of translation
    for stage_name, text in stages.items():
        if text:  # Only show non-empty stages
            print(f"{BOLD}{stage_name.upper()}:{RESET}")
            # Use different colors for different stages
            if "deepl" in stage_name.lower() or "google" in stage_name.lower():
                print(f"{YELLOW}{text}{RESET}\n")
            elif "critic" in stage_name.lower():
                print(f"{RED}{text}{RESET}\n")
            elif "final" in stage_name.lower():
                print(f"{GREEN}{text}{RESET}\n")
            else:
                print(f"{MAGENTA}{text}{RESET}\n")
    
    # Footer
    print(f"{CYAN}{'=' * 80}{RESET}")
    print(f"{BOLD}{CYAN}END OF TRANSLATION COMPARISON{RESET}")
    print(f"{CYAN}{'=' * 80}{RESET}")
    sys.stdout.flush()

def read_translation_report(report_path="translation_report.txt"):
    """
    Parse the translation report file and display key insights
    in a visually appealing way.
    """
    if not os.path.exists(report_path):
        print(f"Error: Report file not found at {report_path}")
        return
    
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Extract basic statistics
        stats = {}
        for line in content.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                stats[key.strip()] = value.strip()
        
        # Extract individual translations
        translations = []
        
        # Now display the report in a colorful way
        clear_screen()
        print(f"{Colors.BRIGHT_CYAN}{'=' * 80}{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}SUBTITLE TRANSLATION REPORT SUMMARY{Colors.ENDC}")
        print(f"{Colors.BRIGHT_CYAN}{'=' * 80}{Colors.ENDC}\n")
        
        # Print key statistics
        print(f"{Colors.BOLD}BASIC INFORMATION:{Colors.ENDC}")
        for key in ['Input file', 'Output file', 'Source language', 'Target language']:
            if key in stats:
                print(f"  {Colors.BOLD}{key}:{Colors.ENDC} {Colors.GREEN}{stats[key]}{Colors.ENDC}")
        
        # Print performance statistics
        print(f"\n{Colors.BOLD}PERFORMANCE STATISTICS:{Colors.ENDC}")
        for key in ['Total lines translated', 'DeepL suggestions used', 'Standard Critic changes']:
            if key in stats:
                print(f"  {Colors.BOLD}{key}:{Colors.ENDC} {Colors.YELLOW}{stats[key]}{Colors.ENDC}")
        
        # Print processing time if available
        if 'Total processing time' in stats:
            print(f"  {Colors.BOLD}Total processing time:{Colors.ENDC} {Colors.BRIGHT_MAGENTA}{stats['Total processing time']}{Colors.ENDC}")
        
        print(f"\n{Colors.BRIGHT_CYAN}{'=' * 80}{Colors.ENDC}")
        print(f"{Colors.BOLD}Report file available at: {Colors.UNDERLINE}{report_path}{Colors.ENDC}")
        print(f"{Colors.BRIGHT_CYAN}{'=' * 80}{Colors.ENDC}")
        
    except Exception as e:
        print(f"Error reading report: {e}")

def monitor_log_file(log_path="translator.log", refresh_interval=1.0):
    """
    Monitor the translator log file in real-time and display colorized output.
    This is useful for seeing the translation process as it happens.
    
    Args:
        log_path: Path to the log file
        refresh_interval: How often to check for new log lines (in seconds)
    """
    if not os.path.exists(log_path):
        print(f"Error: Log file not found at {log_path}")
        return
    
    print(f"{Colors.GREEN}Starting log monitor for {log_path}{Colors.ENDC}")
    print(f"{Colors.CYAN}Press Ctrl+C to stop monitoring{Colors.ENDC}\n")
    
    # Track the last position we read from
    last_position = 0
    
    try:
        while True:
            file_size = os.path.getsize(log_path)
            
            # If file has grown, read the new content
            if file_size > last_position:
                with open(log_path, 'r', encoding='utf-8') as f:
                    f.seek(last_position)
                    new_content = f.read()
                    
                # Process and display new log lines
                for line in new_content.split('\n'):
                    if line.strip():
                        # Colorize log levels
                        if '[ERROR]' in line:
                            line = line.replace('[ERROR]', f"{Colors.RED}[ERROR]{Colors.ENDC}")
                        elif '[WARNING]' in line:
                            line = line.replace('[WARNING]', f"{Colors.YELLOW}[WARNING]{Colors.ENDC}")
                        elif '[INFO]' in line:
                            line = line.replace('[INFO]', f"{Colors.GREEN}[INFO]{Colors.ENDC}")
                        elif '[DEBUG]' in line:
                            line = line.replace('[DEBUG]', f"{Colors.BLUE}[DEBUG]{Colors.ENDC}")
                        
                        # Highlight translation progress
                        if 'Translation for line' in line:
                            line = Colors.BRIGHT_CYAN + line + Colors.ENDC
                        
                        # Highlight deepL references
                        if 'DeepL Reference' in line:
                            line = Colors.BRIGHT_YELLOW + line + Colors.ENDC
                            
                        print(line)
                
                # Update the last position
                last_position = file_size
            
            # Wait before checking again
            time.sleep(refresh_interval)
    
    except KeyboardInterrupt:
        print(f"\n{Colors.GREEN}Log monitoring stopped.{Colors.ENDC}")

def print_usage():
    """Print usage information for this script."""
    script_name = os.path.basename(__file__)
    print(f"{Colors.BOLD}Live Translation Viewer{Colors.ENDC}")
    print(f"Usage: python {script_name} [command]")
    print("\nCommands:")
    print(f"  {Colors.CYAN}monitor{Colors.ENDC}    - Monitor the translator.log file in real-time")
    print(f"  {Colors.CYAN}report{Colors.ENDC}     - Show a summary of the translation report")
    print(f"  {Colors.CYAN}help{Colors.ENDC}       - Show this help message")
    print("\nExamples:")
    print(f"  python {script_name} monitor")
    print(f"  python {script_name} report")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    if command == "monitor":
        log_path = sys.argv[2] if len(sys.argv) > 2 else "translator.log"
        monitor_log_file(log_path)
    elif command == "report":
        report_path = sys.argv[2] if len(sys.argv) > 2 else "translation_report.txt"
        read_translation_report(report_path)
    elif command == "help":
        print_usage()
    else:
        print(f"{Colors.RED}Unknown command: {command}{Colors.ENDC}")
        print_usage()
        sys.exit(1)