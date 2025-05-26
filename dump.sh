#!/bin/bash

# --- Configuration ---
OUTPUT_FILE="project_snapshot_bash.txt"
# MAX_TEXT_FILE_SIZE_KB=5120 # Example: 5MB limit for text file content (optional, currently not strictly enforced for text files)

# --- Helper Functions ---
get_os_type() {
    case "$(uname -s)" in
        Linux*)     echo "Linux";;
        Darwin*)    echo "Darwin";;
        CYGWIN*|MINGW*|MSYS*) echo "Windows";; # Or handle as Linux-like if using bash on Windows
        *)          echo "Unknown";;
    esac
}

get_file_size_kb() {
    local file_path="$1"
    local size_bytes=0
    local os_type
    os_type=$(get_os_type)

    if [ ! -e "$file_path" ]; then
        echo "0" # Should not happen if file_path comes from a valid list
        return
    fi

    if [ "$os_type" == "Linux" ]; then
        size_bytes=$(stat -c%s "$file_path" 2>/dev/null) || size_bytes=0
    elif [ "$os_type" == "Darwin" ]; then
        size_bytes=$(stat -f%z "$file_path" 2>/dev/null) || size_bytes=0
    else # Fallback for other systems or if stat fails
        size_bytes=$(wc -c < "$file_path" | awk '{print $1}') || size_bytes=0
    fi
    echo $((size_bytes / 1024))
}

# --- Sanity Checks ---
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo "Error: This script must be run from within a Git repository." >&2
    exit 1
fi

for cmd in git file nl tr sort awk grep stat wc mktemp uname basename date cat rm; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        echo "Error: Required command '$cmd' not found. Please install it." >&2
        exit 1
    fi
done

# --- Main Logic ---
echo "Starting project snapshot generation..."

# Temporary files
TMP_FILE_LIST_RAW=$(mktemp)
TMP_FILE_LIST_FILTERED=$(mktemp) # For list after removing OUTPUT_FILE
TMP_FILE_LIST_SORTED=$(mktemp)   # For final sorted list to process

# Get list of files from git: tracked and untracked (respecting .gitignore)
git ls-files -co --exclude-standard --full-name -z > "$TMP_FILE_LIST_RAW"

# **CRITICAL FIX**: Filter out the OUTPUT_FILE itself from the list of files to process
# This prevents the script from reading and appending its own output file.
# grep -vxF: -v (invert match), -x (match whole line), -F (fixed string)
# This ensures that if OUTPUT_FILE is "project_snapshot_bash.txt", it matches exactly that line.
tr '\0' '\n' < "$TMP_FILE_LIST_RAW" | \
    grep -vxF "$OUTPUT_FILE" | \
    tr '\n' '\0' > "$TMP_FILE_LIST_FILTERED"
rm "$TMP_FILE_LIST_RAW" # Clean up raw list

# Sort and unique the filtered file list
if LC_ALL=C sort -zu "$TMP_FILE_LIST_FILTERED" -o "$TMP_FILE_LIST_SORTED" >/dev/null 2>&1; then
    echo "Sorted filtered file list using sort -z."
else
    echo "sort -z not available or failed, using tr fallback for sorting filtered list."
    tr '\0' '\n' < "$TMP_FILE_LIST_FILTERED" | LC_ALL=C sort -u | tr '\n' '\0' > "$TMP_FILE_LIST_SORTED"
fi
rm "$TMP_FILE_LIST_FILTERED" # Clean up filtered list

# Start writing to the output file (truncate or create)
{
    echo "Project Snapshot"
    echo "Root: $(basename "$(git rev-parse --show-toplevel)")"
    echo "Generated: $(date)"
    echo "Mode: Bash Script (Corrected)"
    echo ""
    echo "========================================"
    echo "Included Files (respecting .gitignore, excluding '$OUTPUT_FILE'):"
    echo "----------------------------------------"
    if [ -s "$TMP_FILE_LIST_SORTED" ]; then
        tr '\0' '\n' < "$TMP_FILE_LIST_SORTED" | awk 'NF' # awk NF to skip potential empty lines
    else
        echo "[No files to include based on git ls-files, .gitignore rules, and exclusion of '$OUTPUT_FILE']"
    fi
    echo ""
    echo "========================================"
    echo "File Contents:"
    echo "========================================"
    echo ""
} > "$OUTPUT_FILE"

# Process each file from the sorted list
if [ -s "$TMP_FILE_LIST_SORTED" ]; then # Check if there are any files to process
    while IFS= read -r -d $'\0' file_path; do
        if [ -z "$file_path" ]; then # Should not happen with -d $'\0' but as a safeguard
            continue
        fi

        # Append current file's processed content to the main output file
        {
            echo "--- File: $file_path ---"

            if [ ! -f "$file_path" ] && [ ! -L "$file_path" ]; then
                echo "[File listed by git but not found or not a regular file/symlink at processing time]"
            elif [ ! -s "$file_path" ]; then
                echo "[Empty file]"
            else
                mime_type=$(file -L -b --mime-type "$file_path")
                is_binary=0

                case "$mime_type" in
                    text/*|application/json|application/xml|application/javascript|application/rss+xml|application/atom+xml|image/svg+xml|application/ld+json|application/schema+json|application/geo+json|application/manifest+json|application/x-sh|application/x-python*|application/x-perl*|application/x-ruby*|inode/x-empty|application/x-empty)
                        is_binary=0
                        ;;
                    application/octet-stream|image/png|image/jpeg|image/gif|image/bmp|image/tiff|audio/*|video/*|application/pdf|application/zip|application/gzip|application/x-tar|application/java-archive|application/vnd.*|application/x-dosexec|application/x-sqlite3|font/*|application/wasm)
                        if [ "$mime_type" = "image/svg+xml" ]; then is_binary=0; else is_binary=1; fi
                        ;;
                    *)
                        if [ "$(tr -cd '\0' < "$file_path" | wc -c)" -gt 0 ]; then
                            is_binary=1
                            mime_type="$mime_type (heuristic: contains NULL bytes)"
                        elif [ "$mime_type" = "data" ]; then
                            is_binary=1
                            mime_type="$mime_type (heuristic: 'data' MIME type)"
                        else
                            is_binary=0
                            echo "[Note: Ambiguous MIME type '$mime_type' processed as text]"
                        fi
                        ;;
                esac

                if [ "$is_binary" -eq 1 ]; then
                    size_kb=$(get_file_size_kb "$file_path")
                    echo "[Binary file (MIME: ${mime_type:-unknown}, Size: ${size_kb}KB) - content not included]"
                else
                    if nl -b a -w 4 -s $'\t' "$file_path"; then
                        :
                    else
                        echo "[Warning: 'nl' command failed for this file. Attempting with awk...]"
                        awk '{printf "%04d\t%s\n", NR, $0}' "$file_path" || \
                        echo "[Error: Both 'nl' and 'awk' failed to process this file. Content omitted.]"
                    fi
                fi
            fi
            echo ""
            echo ""
        } >> "$OUTPUT_FILE"
    done < "$TMP_FILE_LIST_SORTED"
fi

# Clean up
rm "$TMP_FILE_LIST_SORTED"

echo ""
echo "Project snapshot generation complete: $OUTPUT_FILE"
echo "IMPORTANT: Please review '$OUTPUT_FILE' for any sensitive information (API keys, passwords, etc.) before sharing."
