import os
import shutil
import sys
import logging
import re
import tempfile
import zipfile
import threading
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
import configparser
import json
import time
from datetime import datetime
import uuid

# Add the project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import modules
from py.config_manager import ConfigManager
from py.subtitle_processor import SubtitleProcessor
from py.translation_service import TranslationService
from py.critic_service import CriticService
from py.logger import setup_logger
from py.video_transcriber import VideoTranscriber

# Initialize Flask app
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'subs')
app.secret_key = os.urandom(24)  # Add secret key for flash messages

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Define the cache directory for temporary files
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)  # Ensure the cache folder exists

# Initialize config first
config_manager = ConfigManager(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini'))
config = config_manager.get_config()
 
# Setup logging with correct level based on debug_mode
debug_mode = config.getboolean('general', 'debug_mode', fallback=False)
log_level = logging.DEBUG if debug_mode else logging.INFO
logger = setup_logger('app', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translator.log'), level=log_level)

if debug_mode:
    logger.debug("Debug mode is enabled - full LLM prompts will be logged")

# Initialize the configuration manager
config_manager = ConfigManager(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini'))

# Translation jobs storage
translation_jobs = {}

# Progress status file path
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translation_progress.json')

# Global variable for bulk translation progress tracking
bulk_translation_progress = {
    "mode": "idle",
    "status": "idle", 
    "message": "",
    "current_file": "",
    "done_files": 0,
    "total_files": 0,
    "zip_path": ""
}

# Load saved progress state if it exists
def load_progress_state():
    global bulk_translation_progress
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                saved_progress = json.load(f)
                if saved_progress.get("status") in ["processing", "scanning", "translating"]:
                    # If the saved status shows an active process, set to failed
                    # as the process was likely interrupted
                    saved_progress["status"] = "failed"
                    saved_progress["message"] = "Translation was interrupted. Please start again."
                bulk_translation_progress = saved_progress
                logger.info("Loaded saved translation progress state")
    except Exception as e:
        logger.error(f"Failed to load translation progress state: {e}")

# Save current progress state to file
def save_progress_state():
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(bulk_translation_progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save translation progress state: {e}")

# Initialize by loading any saved state
load_progress_state()

# Language mapping
LANGUAGES = [
    ('en', 'English'),
    ('es', 'Spanish'),
    ('fr', 'French'),
    ('de', 'German'),
    ('it', 'Italian'),
    ('pt', 'Portuguese'),
    ('ru', 'Russian'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('zh', 'Chinese'),
    ('da', 'Danish'),
    ('nl', 'Dutch'),
    ('fi', 'Finnish'),
    ('sv', 'Swedish'),
    ('no', 'Norwegian'),
]

@app.route('/')
def index():
    """Render the home page with recent translations."""
    config = config_manager.get_config()
    # Use source_language and target_language from config instead of default_source_language and default_target_language
    default_source = config.get('general', 'source_language', fallback='en')
    default_target = config.get('general', 'target_language', fallback='da')
    
    # Get list of recent translations
    recent_files = get_recent_translations()
    
    return render_template('index.html', 
                          languages=LANGUAGES, 
                          default_source=default_source,
                          default_target=default_target,
                          recent_files=recent_files)

@app.route('/logs')
def logs():
    """Render the log viewer page."""
    log_files = get_log_files()
    current_log = 'translator.log'
    log_content = get_log_content(current_log)
    
    return render_template('log_viewer.html',
                          log_files=log_files,
                          current_log=current_log,
                          log_content=log_content)

@app.route('/config')
def config():
    """Render the configuration editor page."""
    return render_template('config_editor.html')

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """API endpoint for getting and setting configuration."""
    if request.method == 'GET':
        return jsonify(config_manager.get_config_as_dict())
    elif request.method == 'POST':
        try:
            config_data = request.json
            config_manager.save_config(config_data)
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Error saving configuration: {str(e)}")
            return jsonify({'success': False, 'message': str(e)})

@app.route('/api/logs')
def api_logs():
    """API endpoint for getting logs."""
    log_file = request.args.get('file', 'translator.log')
    content = get_log_content(log_file)
    return jsonify({'logs': content.splitlines() if content else []})

@app.route('/api/clear_log', methods=['POST'])
def api_clear_log():
    """API endpoint for clearing a log file."""
    log_file = request.json.get('file', 'translator.log')
    success = clear_log_file(log_file)
    return jsonify({'success': success})

@app.route('/api/translate', methods=['POST'])
def api_translate():
    """Handle file upload and start translation."""
    try:
        # Check if a host file path was provided instead of a file upload
        host_file_path = request.form.get('host_file_path', '')
        
        if (host_file_path):
            # Validate the host file path for security
            if not os.path.isfile(host_file_path):
                return jsonify({"error": "Invalid file path or file does not exist"}), 400
                
            # Check if it's a subtitle file
            if not host_file_path.lower().endswith(('.srt', '.ass', '.vtt')):
                return jsonify({"error": "Only subtitle files (.srt, .ass, .vtt) are supported"}), 400
                
            # Get the filename without path
            filename = os.path.basename(host_file_path)
            
            # Create a job ID based on the filename and timestamp
            timestamp = int(time.time())
            job_id = f"{timestamp}_{filename}"
            
            # Copy the file to the cache directory
            cache_path = os.path.join(CACHE_DIR, filename)
            shutil.copy2(host_file_path, cache_path)
            logger.info(f"Using host file: {host_file_path}, copied to {cache_path}")
            
        else:
            # Handle regular file upload
            if 'file' not in request.files:
                return jsonify({"error": "No file part in the request"}), 400
                
            file = request.files['file']
            
            if file.filename == '':
                return jsonify({"error": "No file selected"}), 400
                
            if not allowed_file(file.filename):
                return jsonify({"error": "File type not allowed. Only subtitle files (.srt, .ass, .vtt) are permitted"}), 400
                
            # Create a secure filename
            filename = secure_filename(file.filename)
            
            # Create a job ID based on the filename and timestamp
            timestamp = int(time.time())
            job_id = f"{timestamp}_{filename}"
            
            # Save the file to the cache directory
            cache_path = os.path.join(CACHE_DIR, filename)
            file.save(cache_path)
            logger.info(f"File uploaded: {filename}, saved to {cache_path}")

        # Get language options
        source_language = request.form.get('source_language', 'en')
        target_language = request.form.get('target_language', 'da')
        
        # Process special meanings if provided
        special_meanings = []
        if 'special_meanings' in request.form:
            try:
                special_meanings = json.loads(request.form['special_meanings'])
                logger.info(f"Special meanings provided: {len(special_meanings)} items")
            except:
                logger.warning("Failed to parse special meanings JSON")

        # Start the translation in a background thread
        threading.Thread(
            target=process_translation,
            args=(job_id, cache_path, filename, source_language, target_language, special_meanings)
        ).start()

        return jsonify({
            "status": "success",
            "message": "File uploaded and translation started",
            "job_id": job_id
        })

    except Exception as e:
        logger.exception("Error in file upload and translation")
        return jsonify({"error": str(e)}), 500

@app.route('/api/job_status/<job_id>')
def api_job_status(job_id):
    """API endpoint for checking translation job status."""
    if job_id not in translation_jobs:
        return jsonify({'success': False, 'message': 'Job not found'})
    
    job = translation_jobs[job_id]
    return jsonify({
        'success': True,
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message']
    })

@app.route('/download/<job_id>')
def download_translation(job_id):
    """Endpoint for downloading a completed translation."""
    if job_id not in translation_jobs or translation_jobs[job_id]['status'] != 'completed':
        return redirect(url_for('index'))
    
    job = translation_jobs[job_id]
    return send_file(job['target_path'], 
                     as_attachment=True, 
                     download_name=f"translated_{job['original_filename']}")

@app.route('/api/view_subtitle/<job_id>')
def api_view_subtitle(job_id):
    """API endpoint for viewing a subtitle file."""
    if job_id not in translation_jobs:
        return jsonify({'success': False, 'message': 'Job not found'})
    
    job = translation_jobs[job_id]
    file_path = job['target_path'] if job['status'] == 'completed' else job['source_path']
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({
            'success': True,
            'content': content,
            'is_translated': job['status'] == 'completed'
        })
    except Exception as e:
        logger.error(f"Error reading subtitle file: {str(e)}")
        return jsonify({'success': False, 'message': f"Error reading file: {str(e)}"})

@app.route('/upload', methods=['POST'])
def upload():
    """Handle subtitle file upload and translation."""
    if 'srtfile' not in request.files:
        flash("No SRT file part in the request.", "error")
        return redirect(url_for("index"))
        
    file = request.files['srtfile']
    if file.filename == '':
        flash("No selected file.", "error")
        return redirect(url_for("index"))
        
    if not file.filename.lower().endswith('.srt'):
        flash("Invalid file type. Please upload an SRT file.", "error")
        return redirect(url_for("index"))

    # Save the uploaded file temporarily for processing
    temp_input_path = os.path.join(tempfile.gettempdir(), secure_filename(f"temp_{file.filename}"))
    try:
        file.save(temp_input_path)
        logger.info(f"Received SRT: {file.filename} -> {temp_input_path}")
    except Exception as e:
        logger.error(f"Failed to save temporary input file: {e}")
        flash("Server error saving uploaded file.", "error")
        return redirect(url_for("index"))

    # Get language codes from config
    config = config_manager.get_config()
    src_lang = config.get("general", "source_language", fallback="en")
    tgt_lang = config.get("general", "target_language", fallback="da")
    
    # Determine output filename
    base, ext = os.path.splitext(file.filename)
    out_base = base
    replaced = False
    
    # Try to replace language code in filename if it exists
    patterns = [
        f'.{src_lang}.', f'.{src_lang}-', f'.{src_lang}_',
        f'{src_lang}.', f'-{src_lang}.', f'_{src_lang}.'
    ]
    import re
    for pat in patterns:
        if pat in base.lower():
            newpat = pat.replace(src_lang, tgt_lang)
            out_base = re.sub(pat, newpat, base, flags=re.IGNORECASE)
            replaced = True
            break
    if not replaced:
        out_base = f"{base}.{tgt_lang}"
    
    # Ensure the output filename is secure and save to SUBS_FOLDER
    out_filename = secure_filename(out_base + ext)
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], out_filename)

    try:
        # Initialize subtitle processor and translation service
        subtitle_processor = SubtitleProcessor(logger)
        translation_service = TranslationService(config, logger)
        
        # Process the subtitles
        subtitles = subtitle_processor.parse_file(temp_input_path)
        translated_subtitles = []
        
        for subtitle in subtitles:
            translated_text = translation_service.translate(
                subtitle['text'],
                src_lang,
                tgt_lang
            )
            translated_subtitle = subtitle.copy()
            translated_subtitle['text'] = translated_text
            translated_subtitles.append(translated_subtitle)
        
        # Write translated subtitles to file
        subtitle_processor.write_file(output_path, translated_subtitles)
        
        flash(f"Translation complete! Saved as '{out_filename}' in the subs archive.", "success")
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        flash(f"Translation failed: {e}", "error")
    finally:
        # Clean up the temporary input file
        if os.path.exists(temp_input_path):
            try:
                os.remove(temp_input_path)
                logger.info(f"Cleaned up temporary input file: {temp_input_path}")
            except Exception as e_clean:
                logger.warning(f"Failed to clean up temp input file {temp_input_path}: {e_clean}")

    # Redirect back to the index page instead of download
    return redirect(url_for("index"))

@app.route('/api/progress')
def get_progress():
    """API endpoint for getting translation progress."""
    return jsonify(bulk_translation_progress)

@app.route('/api/list_subs')
def api_list_subs():
    """API endpoint for listing subtitle files in the subs folder."""
    try:
        logger.info(f"Listing subtitle files in {app.config['UPLOAD_FOLDER']}")
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            logger.warning(f"Subs folder {app.config['UPLOAD_FOLDER']} does not exist")
            return jsonify({"files": [], "warning": f"Subs folder does not exist"})
        
        files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) 
                if f.lower().endswith('.srt') and os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f))]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(app.config['UPLOAD_FOLDER'], f)), reverse=True)
        
        logger.info(f"Found {len(files)} subtitle files in archive")
        return jsonify({"files": files})
    except Exception as e:
        logger.error(f"Failed to list subs folder: {e}")
        return jsonify({"files": [], "error": str(e)}), 500

@app.route('/download_sub/<path:filename>')
def download_sub_file(filename):
    """Endpoint for downloading a specific subtitle file from the subs folder."""
    safe_filename = secure_filename(filename)
    if safe_filename != filename:  # Basic check against directory traversal attempts
        logger.error(f"Invalid filename requested for download: {filename}")
        return "Invalid filename", 400
        
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
    
    if not os.path.isfile(file_path):
        logger.error(f"File not found in subs archive: {file_path}")
        return "File not found in archive", 404
        
    logger.info(f"Serving file from subs archive: {file_path}")
    try:
        return send_from_directory(
            app.config['UPLOAD_FOLDER'], 
            safe_filename, 
            as_attachment=True
        )
    except Exception as e:
        logger.error(f"Failed to send file from subs archive {file_path}: {e}")
        return "Error serving file", 500

@app.route('/api/delete_sub/<path:filename>', methods=['DELETE'])
def api_delete_sub(filename):
    """API endpoint for deleting a subtitle file from the subs folder."""
    try:
        safe_filename = secure_filename(filename)
        if safe_filename != filename:
            return jsonify({"success": False, "error": "Invalid filename"}), 400
            
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        
        if not os.path.isfile(file_path):
            return jsonify({"success": False, "error": "File not found"}), 404
            
        os.remove(file_path)
        logger.info(f"Deleted subtitle file: {safe_filename}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Failed to delete subtitle file: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/browse_dirs', methods=['GET'])
def api_browse_dirs():
    """API endpoint to list directories for the file browser."""
    parent_path = request.args.get("path", "")
    
    # Default to system root directories if no path provided
    if not parent_path:
        if os.name == "nt":  # Windows
            import string
            # Get all drives
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return jsonify({"directories": drives, "current_path": "", "parent_path": ""})
        else:  # Unix-like
            parent_path = "/"
    
    try:
        # Security check: normalize path to prevent directory traversal
        parent_path = os.path.normpath(parent_path)
        
        # Get the parent of the current directory for "up one level" functionality
        parent_of_parent = os.path.dirname(parent_path) if parent_path != "/" else ""
        
        # List all directories in the parent path
        dirs = []
        if os.path.isdir(parent_path):
            for item in os.listdir(parent_path):
                full_path = os.path.join(parent_path, item)
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
            
            # Sort directories by name
            dirs.sort(key=lambda x: x["name"].lower())
            
            return jsonify({
                "directories": dirs,
                "current_path": parent_path,
                "parent_path": parent_of_parent
            })
        else:
            return jsonify({"error": "Not a valid directory"}), 400
    except PermissionError:
        return jsonify({"error": "Permission denied accessing this directory"}), 403
    except Exception as e:
        logger.error(f"Error browsing directory {parent_path}: {str(e)}")
        return jsonify({"error": f"Error accessing directory: {str(e)}"}), 500

@app.route('/api/browse_files', methods=['GET'])
def api_browse_files():
    """API endpoint to list files in a directory for the host file browser."""
    parent_path = request.args.get("path", "")
    
    # Default to system root directories if no path provided
    if not parent_path:
        if os.name == "nt":  # Windows
            import string
            # Get all drives
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return jsonify({
                "files": [],
                "directories": [{"name": d, "path": d} for d in drives],
                "current_path": "",
                "parent_path": ""
            })
        else:  # Unix-like
            parent_path = "/"
    
    try:
        # Security check: normalize path to prevent directory traversal
        parent_path = os.path.normpath(parent_path)
        
        # Get the parent of the current directory for "up one level" functionality
        parent_of_parent = os.path.dirname(parent_path) if parent_path != "/" else ""
        
        # List all items in the parent path
        files = []
        dirs = []
        
        if os.path.isdir(parent_path):
            for item in os.listdir(parent_path):
                full_path = os.path.join(parent_path, item)
                
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
                else:
                    # Only include files with certain extensions
                    if item.lower().endswith(('.srt', '.ass', '.vtt')):
                        files.append({"name": item, "path": full_path})
            
            # Sort directories and files by name
            dirs.sort(key=lambda x: x["name"].lower())
            files.sort(key=lambda x: x["name"].lower())
            
            return jsonify({
                "files": files,
                "directories": dirs,
                "current_path": parent_path,
                "parent_path": parent_of_parent
            })
        else:
            return jsonify({"error": "Not a valid directory"}), 400
    except PermissionError:
        return jsonify({"error": "Permission denied accessing this directory"}), 403
    except Exception as e:
        logger.error(f"Error browsing files in directory {parent_path}: {str(e)}")
        return jsonify({"error": f"Error accessing directory: {str(e)}"}), 500

@app.route("/api/start-scan", methods=["POST"])
def api_start_scan():
    """API endpoint to start a bulk scan and translation of a directory."""
    data = request.get_json(silent=True) or {}
    root = data.get("path", "").strip()
    if not root or not os.path.isdir(root):
        logger.error(f"Invalid or missing folder path: {root}")
        return jsonify({"ok": False, "error": "Folder not found or path is invalid"}), 400

    config = config_manager.get_config()

    # Optional: Whitelist check
    allowed_bases = config.get("bulk_scan", "allowed_base", fallback="").split(',')
    allowed_bases = [os.path.abspath(b.strip()) for b in allowed_bases if b.strip()]
    if allowed_bases:  # Only check if allowed_base is configured
        try:
            abs_root = os.path.abspath(root)
            base_ok = any(os.path.commonpath([abs_root, b]) == b for b in allowed_bases)
            if not base_ok:
                logger.error(f"Path '{root}' is outside allowed base paths: {allowed_bases}")
                return jsonify({"ok": False, "error": "Folder is outside the allowed base paths configured in config.ini"}), 403
        except ValueError as e:
            # Handle cases where paths might be on different drives (Windows)
            logger.error(f"Error checking common path for '{root}': {e}")
            return jsonify({"ok": False, "error": "Error validating folder path against allowed bases."}), 400

    # Reset global progress dict for bulk mode
    bulk_translation_progress.clear()
    bulk_translation_progress.update({
        "mode": "bulk",
        "status": "queued",
        "message": "",
        "current_file": "",
        "done_files": 0,
        "total_files": 0,
        "zip_path": ""
    })
    
    # Start bulk translation in background thread
    threading.Thread(
        target=scan_and_translate_directory,
        args=(root, config, bulk_translation_progress, logger),
        daemon=True
    ).start()

    return jsonify({"ok": True})

@app.route("/download-zip")
def download_zip():
    """Endpoint for downloading a zip file of translated subtitles."""
    temp_path = request.args.get("temp", "")
    # Security check: Ensure the path is within an expected temp directory structure
    if not temp_path or not temp_path.startswith(tempfile.gettempdir()) or '..' in temp_path:
        logger.error(f"Invalid or potentially unsafe temp path requested: {temp_path}")
        return "Invalid or potentially unsafe file path", 400
        
    if not os.path.isfile(temp_path):
        logger.error(f"Zip file not found or expired: {temp_path}")
        return "File expired or missing", 404
        
    logger.info(f"Serving zip file: {temp_path}")
    try:
        return send_from_directory(
            directory=os.path.dirname(temp_path),
            path=os.path.basename(temp_path),
            as_attachment=True,
            download_name="translated_subtitles.zip"
        )
    except Exception as e:
        logger.error(f"Failed to send file {temp_path}: {e}")
        return "Error serving file", 500

@app.route('/api/live_status')
def live_status():
    """API endpoint to get the current live translation status."""
    # Get translation progress from the existing progress tracker
    progress_response = get_progress()
    
    # Convert the response to a dictionary if it's a Response object
    if hasattr(progress_response, 'get_json'):
        progress = progress_response.get_json()
    else:
        progress = progress_response
    
    # Build response with detailed translation information
    response_data = {"status": "idle"}
    
    if progress and isinstance(progress, dict):
        # First, copy the basic progress information to response
        response_data["status"] = progress.get("status", "idle")
        response_data["filename"] = progress.get("current_file", "")
        response_data["current_line"] = progress.get("current_line", 0)
        response_data["total_lines"] = progress.get("total_lines", 0)
        
        # Add the mode (bulk or single)
        if "mode" in progress:
            response_data["mode"] = progress.get("mode", "single")
        
        # If there's detailed current line information, include it
        if "current" in progress and isinstance(progress["current"], dict):
            # Include the entire current object for detailed line information
            response_data["current"] = progress["current"]
            
            # Also include top-level fields for backwards compatibility
            response_data["line_number"] = progress["current"].get("line_number", 0)
            response_data["original"] = progress["current"].get("original", "")
            response_data["first_pass"] = progress["current"].get("first_pass", "")
            response_data["critic"] = progress["current"].get("standard_critic", "")
            response_data["final"] = progress["current"].get("final", "")
            
            # Add timing information if available
            if "timing" in progress["current"]:
                response_data["timing"] = progress["current"].get("timing", {})
            
            # If critic has changed the translation, indicate this in the response
            if "standard_critic" in progress["current"] and "first_pass" in progress["current"]:
                response_data["critic_changed"] = progress["current"]["standard_critic"] != progress["current"]["first_pass"]
                
            # Add detail about what the critic did
            if "critic_action" in progress["current"]:
                response_data["critic_action"] = progress["current"].get("critic_action", "")
        
        # Add history of processed lines if available
        if "processed_lines" in progress:
            response_data["processed_lines"] = progress["processed_lines"]
        
        # Only log the detailed response if log_live_status is enabled
        if config.getboolean('logging', 'log_live_status', fallback=False):
            logger.debug(f"Live status response: {response_data}")
    
    return jsonify(response_data)

@app.route('/api/translation_report/<path:filename>')
def api_translation_report(filename):
    """API endpoint for getting a detailed report of a translated subtitle file."""
    try:
        safe_filename = secure_filename(filename)
        if safe_filename != filename:  # Basic check against directory traversal attempts
            logger.error(f"Invalid filename requested for report: {filename}")
            return jsonify({"success": False, "message": "Invalid filename"}), 400
            
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        
        if not os.path.isfile(file_path):
            logger.error(f"File not found for report: {file_path}")
            return jsonify({"success": False, "message": "File not found"}), 404
            
        # Get file info
        file_stats = os.stat(file_path)
        creation_time = datetime.fromtimestamp(file_stats.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
        file_size = file_stats.st_size
        
        # Parse language codes from filename (assuming format like xxx_en_to_da_xxx.srt)
        source_lang = "unknown"
        target_lang = "unknown"
        lang_pattern = re.compile(r'_([a-z]{2})_to_([a-z]{2})_')
        lang_match = lang_pattern.search(filename)
        if lang_match:
            source_lang = lang_match.group(1)
            target_lang = lang_match.group(2)
            
        # Read the file to get subtitle details
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Basic subtitle analysis
            subtitle_processor = SubtitleProcessor(logger)
            subtitles = subtitle_processor.parse_file(file_path)
            
            # Calculate statistics
            total_lines = len(subtitles)
            total_words = 0
            total_chars = 0
            avg_line_length = 0
            longest_line = 0
            longest_line_content = ""
            
            for subtitle in subtitles:
                text = subtitle.get('text', '')
                words = len(text.split())
                chars = len(text)
                total_words += words
                total_chars += chars
                
                if chars > longest_line:
                    longest_line = chars
                    longest_line_content = text
            
            if total_lines > 0:
                avg_line_length = total_chars / total_lines
            
            # Get a few sample subtitles for preview
            sample_count = min(5, total_lines)
            samples = []
            step = max(1, total_lines // sample_count)
            for i in range(0, total_lines, step):
                if len(samples) < sample_count and i < total_lines:
                    samples.append(subtitles[i])
            
            report = {
                "success": True,
                "filename": safe_filename,
                "source_language": source_lang,
                "target_language": target_lang,
                "creation_time": creation_time,
                "file_size_bytes": file_size,
                "file_size_formatted": format_file_size(file_size),
                "total_subtitles": total_lines,
                "total_words": total_words,
                "total_chars": total_chars,
                "avg_line_length": round(avg_line_length, 1),
                "longest_line": longest_line,
                "longest_line_content": longest_line_content,
                "samples": [
                    {
                        "index": s.get('index', '?'),
                        "time": f"{s.get('start_time', '00:00:00')} --> {s.get('end_time', '00:00:00')}",
                        "text": s.get('text', '')
                    } for s in samples
                ],
                "content_preview": content[:1000] + ("..." if len(content) > 1000 else "")
            }
            
            return jsonify(report)
            
        except Exception as e:
            logger.error(f"Error analyzing subtitle file {filename}: {e}")
            return jsonify({
                "success": True,
                "filename": safe_filename,
                "source_language": source_lang,
                "target_language": target_lang,
                "creation_time": creation_time,
                "file_size_bytes": file_size,
                "file_size_formatted": format_file_size(file_size),
                "error": f"Could not fully analyze file: {str(e)}",
                "content_preview": content[:1000] + ("..." if len(content) > 1000 else "") if 'content' in locals() else "Error reading file"
            })
    
    except Exception as e:
        logger.error(f"Error generating translation report for {filename}: {str(e)}")
        return jsonify({"success": False, "message": f"Error generating report: {str(e)}"}), 500

def format_file_size(size_bytes):
    """Convert bytes to human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.1f} KB"
    else:
        return f"{size_bytes/(1024*1024):.1f} MB"

# Helper functions

def get_recent_translations():
    """Get list of recent translations."""
    recent_files = []
    for job_id, job in translation_jobs.items():
        if job['status'] == 'completed':
            recent_files.append({
                'id': job_id,
                'name': job['original_filename'],
                'date': datetime.fromtimestamp(job['end_time']).strftime('%Y-%m-%d %H:%M:%S'),
                'source_language': job['source_language'],
                'target_language': job['target_language']
            })
    
    # Sort by date, newest first
    recent_files.sort(key=lambda x: x['date'], reverse=True)
    
    # Limit to 10 most recent
    return recent_files[:10]

def get_log_files():
    """Get list of available log files."""
    log_dir = os.path.dirname(os.path.abspath(__file__))
    log_files = [f for f in os.listdir(log_dir) if f.startswith('translator.log')]
    return sorted(log_files)

def get_log_content(log_file):
    """Get content of a log file."""
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), log_file)
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error reading log file {log_file}: {str(e)}")
        return f"Error reading log file: {str(e)}"

def clear_log_file(log_file):
    """Clear a log file."""
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), log_file)
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"Log cleared at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        return True
    except Exception as e:
        logger.error(f"Error clearing log file {log_file}: {str(e)}")
        return False

def process_translation(job_id, cache_path, filename, source_language, target_language, special_meanings):
    """Process a translation job, updating the global progress dictionary."""
    # Create a job record if it doesn't exist already
    if job_id not in translation_jobs:
        translation_jobs[job_id] = {
            'status': 'queued',
            'source_path': cache_path,
            'original_filename': filename,
            'source_language': source_language,
            'target_language': target_language,
            'progress': 0,
            'message': 'Queued for translation',
            'start_time': time.time(),
            'end_time': None,
            'special_meanings': special_meanings
        }
    
    job = translation_jobs[job_id]
    logger.info(f"Starting translation job {job_id}: {job['original_filename']}")
    
    # Use the global progress dictionary for live status updates
    global bulk_translation_progress
    progress_dict = bulk_translation_progress
    
    try:
        # Reset and update global progress status for this job
        progress_dict.clear()
        progress_dict.update({
            "mode": "single",
            "status": "processing",
            "message": f'Starting translation for {job["original_filename"]}',
            "current_file": job["original_filename"],
            "total_lines": 0, # Will be updated by translate_srt
            "current_line": 0,
            "job_id": job_id # Add job_id for reference
        })
        
        # Get special meanings if they were provided with the job
        special_meanings = job.get('special_meanings', [])
        if special_meanings:
            logger.info(f"Job {job_id} includes {len(special_meanings)} special word meanings")
            # Add special meanings to progress dict so they can be used by the translation service
            progress_dict["special_meanings"] = special_meanings
        
        # Save progress state to file
        save_progress_state()
        
        # Update job status in translation_jobs as well
        job['status'] = 'processing'
        job['message'] = 'Initializing...'
        
        # Initialize subtitle processor
        subtitle_processor = SubtitleProcessor(logger)
        
        # Get config
        config = config_manager.get_config()
        
        # Update languages in config for this specific job
        config.set('general', 'source_language', job['source_language'])
        config.set('general', 'target_language', job['target_language'])
        
        # Call translate_srt, passing the global progress dictionary
        success = subtitle_processor.translate_srt(
            job['source_path'], 
            job['source_path'].replace('.srt', f'_translated_{job["target_language"]}.srt'), # Generate target path
            config, 
            progress_dict=progress_dict
        )
        
        if success:
            # Update job status upon successful completion
            job['status'] = 'completed'
            job['target_path'] = progress_dict.get("output_path", job['source_path'].replace('.srt', f'_translated_{job["target_language"]}.srt')) # Get actual output path if set
            job['progress'] = 100 # Mark as 100% in job-specific dict
            job['message'] = 'Translation completed'
            job['end_time'] = time.time()
            logger.info(f"Translation job {job_id} completed: {job['original_filename']}")
            
            # Update global progress status to completed
            progress_dict["status"] = "completed"
            progress_dict["message"] = f"Translation completed for {job['original_filename']}"
            
            # Save final progress state to file
            save_progress_state()
        else:
            raise Exception(progress_dict.get("message", "Translation failed in subtitle_processor"))

    except Exception as e:
        error_message = f"Error in translation job {job_id}: {str(e)}"
        logger.error(error_message)
        import traceback
        logger.error(traceback.format_exc())
        
        # Update job status in translation_jobs
        job['status'] = 'failed'
        job['message'] = error_message
        job['progress'] = 0
        job['end_time'] = time.time()
        
        # Update global progress status to failed
        progress_dict["status"] = "failed"
        progress_dict["message"] = error_message
        
        # Save error state to file
        save_progress_state()
    finally:
        # Optionally clear current details from global progress after a short delay
        # to allow the UI to fetch the final status
        # threading.Timer(10.0, clear_global_progress, args=[progress_dict]).start()
        pass # Keep final status until next job starts

def clear_global_progress(progress_dict):
    """Resets the global progress dict to idle state."""
    progress_dict.clear()
    progress_dict.update({
        "mode": "idle",
        "status": "idle", 
        "message": "",
        "current_file": "",
        "done_files": 0,
        "total_files": 0,
        "zip_path": ""
    })
    logger.info("Global progress dictionary reset to idle.")

def scan_and_translate_directory(root_dir, config, progress, logger):
    """Scan a directory for subtitle files and translate them in bulk."""
    try:
        progress["status"] = "scanning"
        progress["message"] = f"Scanning {root_dir} for subtitle files..."
        # Initialize the current field for line-by-line data
        progress["current"] = {}
        # Create empty processed_lines history
        progress["processed_lines"] = []
        # Save progress state to file after status change
        save_progress_state()
        
        # Get language settings
        src_lang = config.get("general", "source_language", fallback="en")
        tgt_lang = config.get("general", "target_language", fallback="da")
        
        # Initialize subtitle processor
        from py.subtitle_processor import SubtitleProcessor
        subtitle_processor = SubtitleProcessor(logger)
        
        # Find all subtitle files in the directory - including both .srt and .ass files
        all_subtitle_files = []
        all_video_files = []
        
        # Temporary directory for extracted subtitles
        import tempfile
        temp_extract_dir = tempfile.mkdtemp(prefix="subtitle_extracted_")
        logger.info(f"Created temporary directory for extracted subtitles: {temp_extract_dir}")
        
        # First pass: Find all subtitle and video files
        for root, _, files in os.walk(root_dir):
            for file in files:
                file_path = os.path.join(root, file)
                if file.lower().endswith(('.srt', '.ass')):
                    all_subtitle_files.append(file_path)
                elif subtitle_processor.is_video_file(file_path):
                    all_video_files.append(file_path)
        
        logger.info(f"Found {len(all_subtitle_files)} total subtitle files (.srt and .ass) in {root_dir}")
        logger.info(f"Found {len(all_video_files)} video files that may contain embedded subtitles")
        
        # Update progress to show we're extracting subtitles
        progress["status"] = "extracting"
        progress["message"] = f"Scanning {len(all_video_files)} video files for embedded subtitles..."
        save_progress_state()
        
        # Get source language code (convert full language name to ISO code if needed)
        src_lang_code = src_lang.lower()
        if src_lang_code in ["english", "danish", "spanish", "german", "french"]:
            # Convert common language names to codes
            lang_map = {"english": "en", "danish": "da", "spanish": "es", "german": "de", "french": "fr"}
            src_lang_code = lang_map.get(src_lang_code, src_lang_code)
            logger.debug(f"Converted source language '{src_lang}' to language code '{src_lang_code}'")
        
        # Do the same for target language
        tgt_lang_code = tgt_lang.lower()
        if tgt_lang_code in ["english", "danish", "spanish", "german", "french"]:
            lang_map = {"english": "en", "danish": "da", "spanish": "es", "german": "de", "french": "fr"}
            tgt_lang_code = lang_map.get(tgt_lang_code, tgt_lang_code)
            logger.debug(f"Converted target language '{tgt_lang}' to language code '{tgt_lang_code}'")
        
        # Also handle potential 3-letter language codes in the embedded subtitles
        src_lang_code_3letter = None
        if src_lang_code == "en":
            src_lang_code_3letter = "eng"
        elif src_lang_code == "da":
            src_lang_code_3letter = "dan"
        elif src_lang_code == "es":
            src_lang_code_3letter = "spa"
        elif src_lang_code == "de":
            src_lang_code_3letter = "deu" 
        elif src_lang_code == "fr":
            src_lang_code_3letter = "fre"
            
        logger.debug(f"Using source language code '{src_lang_code}' and 3-letter code '{src_lang_code_3letter}'")
        
        # Process video files to extract embedded subtitles
        extracted_subtitle_files = []
        
        for i, video_file in enumerate(all_video_files):
            progress["message"] = f"Extracting subtitles from video file {i+1}/{len(all_video_files)}: {os.path.basename(video_file)}"
            save_progress_state()
            
            try:
                # Extract embedded subtitles matching source language
                extracted_files = subtitle_processor.detect_and_extract_embedded_subtitles(
                    video_file, 
                    temp_extract_dir,
                    src_lang_code
                )
                
                if extracted_files:
                    logger.info(f"Extracted {len(extracted_files)} subtitle files from {os.path.basename(video_file)}")
                    extracted_subtitle_files.extend(extracted_files)
                    
                    # Add extracted language information to each file for proper translation queue handling
                    for extracted_file in extracted_files:
                        # The filename should contain language info from the extraction process
                        file_basename = os.path.basename(extracted_file)
                        if src_lang_code in file_basename.lower() or (src_lang_code_3letter and src_lang_code_3letter in file_basename.lower()):
                            logger.info(f"Marking extracted file as source language: {file_basename}")
                        else:
                            # If source language not in filename, check if it's in the extracted file's content
                            # For now, trust the extraction process which should have properly detected languages
                            logger.debug(f"Assuming extracted file is source language: {file_basename}")
                else:
                    logger.info(f"No matching subtitles found in {os.path.basename(video_file)}")
            except Exception as e:
                logger.error(f"Error extracting subtitles from {os.path.basename(video_file)}: {e}")
        
        # Add extracted subtitle files to the main list
        logger.info(f"Total extracted subtitle files: {len(extracted_subtitle_files)}")
        all_subtitle_files.extend(extracted_subtitle_files)
        
        # Update progress status to show we're now processing the regular subtitle files
        progress["status"] = "scanning"
        progress["message"] = f"Processing {len(all_subtitle_files)} subtitle files..."
        save_progress_state()
        
        # Group files by their base name (removing language codes)
        # This helps us identify which files already have translations
        file_groups = {}
        
        # Various language code patterns that might appear in filenames
        lang_patterns = [
            # More specific patterns for complex subtitle filenames
            r'\.([a-z]{2,3})\..*\.(srt|ass)$',    # matches .en.anything.srt or .eng.anything.srt or .en.anything.ass
            r'\.([a-z]{2,3})\.(srt|ass)$',        # matches .en.srt or .eng.srt or .en.ass
            r'\.([a-z]{2,3})\.hi\.(srt|ass)$',    # matches .en.hi.srt specifically
            r'\.([a-z]{2,3})-hi\.(srt|ass)$',     # matches .en-hi.srt
            
            # Inside filename patterns
            r'\.([a-z]{2,3})\.(?!(srt|ass)$)',    # .en. or .eng. followed by something other than srt/ass at the end
            r'\.([a-z]{2,3})-(?!(srt|ass)$)',     # .en- or .eng- followed by something
            r'\.([a-z]{2,3})_(?!(srt|ass)$)',     # .en_ or .eng_ followed by something
            r'_([a-z]{2,3})_',                    # _en_ or _eng_ (surrounded by underscores)
            r'-([a-z]{2,3})-',                    # -en- or -eng- (surrounded by hyphens)
            
            # Added for subtle variations
            r'([a-z]{2,3})\.(?!(srt|ass)$)',      # en. or eng. (at the start of filename or after a separator)
            r'([a-z]{2,3})-(?!(srt|ass)$)',       # en- or eng- (similar to above)
            r'(?<![a-z])([a-z]{2,3})(?![a-z])'    # standalone en or eng (if surrounded by non-letters)
        ]
        
        # Track files that don't match any pattern
        unmatched_files = []
        
        # This tracks which files we should skip based on language patterns
        skip_these_files = []
        
        # Process each file and group them
        for file_path in all_subtitle_files:
            file_name = os.path.basename(file_path)
            file_dir = os.path.dirname(file_path)
            
            # Check if this is one of our extracted subtitle files - handle them specially
            is_extracted = file_path in extracted_subtitle_files
            
            # If this is an extracted file, apply special handling
            if is_extracted:
                # For extracted files, we can rely on the extraction process to have named files correctly
                # They should be named with language code in format: filename.lang.streamX.title.srt
                
                # If it contains our source language, mark it for translation
                if src_lang_code in file_name.lower() or (src_lang_code_3letter and src_lang_code_3letter in file_name.lower()):
                    # Get the base part of the filename to use as a group key
                    # For extracted files, just use the video filename part as base
                    base_parts = file_name.split('.')
                    if len(base_parts) > 2:
                        base_name = '.'.join(base_parts[:-3])  # Remove lang, stream and extension
                    else:
                        base_name = base_parts[0]  # Just use the first part as base
                        
                    # Create a group key that differentiates extracted files from regular files
                    group_key = f"extracted:{file_dir}/{base_name}"
                    
                    if group_key not in file_groups:
                        file_groups[group_key] = {}
                    
                    # Store this as source language file
                    file_groups[group_key][src_lang_code] = file_path
                    logger.debug(f"Added extracted file to group '{group_key}' as source language: {file_name}")
                    
                # Skip any non-source language extracted files - we only want to translate source language
                elif tgt_lang_code in file_name.lower() or (tgt_lang_code == "da" and "dan" in file_name.lower()):
                    skip_these_files.append(file_path)
                    logger.debug(f"Skipping extracted file with target language: {file_name}")
                else:
                    # For other languages, just skip them
                    skip_these_files.append(file_path)
                    logger.debug(f"Skipping extracted file with non-target language: {file_name}")
                    
                # Continue to next file after handling extracted file
                continue
            
            # Standard processing for non-extracted files...
            
            # Critical fix: Special case for target language files with complex patterns
            # This explicitly checks for target language files first and marks them for skipping
            if f".{tgt_lang_code}." in file_name.lower():
                logger.debug(f"Skipping file with target language code in filename: {file_name}")
                skip_these_files.append(file_path)
                continue
                
            # Try to extract language code from filename
            detected_lang = None
            matching_pattern = None
            
            for pattern in lang_patterns:
                match = re.search(pattern, file_name.lower())
                if match:
                    detected_lang = match.group(1)
                    matching_pattern = pattern
                    logger.debug(f"Detected language '{detected_lang}' in file: {file_name} using pattern {pattern}")
                    break
            
            # If we couldn't detect a language, track it but continue to next file
            if not detected_lang:
                unmatched_files.append(file_path)
                logger.debug(f"No language detected in file: {file_name}")
                continue
            
            # Check for 3-letter codes and convert them to 2-letter for consistency
            if detected_lang == "eng":
                detected_lang = "en"
            elif detected_lang == "dan":
                detected_lang = "da"
            elif detected_lang == "spa":
                detected_lang = "es"
            elif detected_lang == "deu" or detected_lang == "ger":
                detected_lang = "de"
            elif detected_lang == "fre":
                detected_lang = "fr"
            
            # Skip if the language doesn't match either source or target language
            if detected_lang != src_lang_code and detected_lang != tgt_lang_code:
                logger.debug(f"Skipping file with non-relevant language '{detected_lang}': {file_name}")
                continue
                
            # If it's a target language file, skip it immediately - we don't want to translate these
            if detected_lang == tgt_lang_code:
                logger.debug(f"Skipping file with target language code: {file_name}")
                skip_these_files.append(file_path)
                continue
                
            # Create a normalized base name for grouping related files
            # For complex patterns, we'll use a more aggressive replacement approach
            
            # Start with filename as base
            base_name = file_name.lower()
            
            # For files matching complex patterns like .en.hi.srt, remove the language code and any additional markers
            if ".hi." in base_name or "-hi." in base_name:
                # Remove language code with hi marker
                base_name = re.sub(r'\.' + detected_lang + r'\.hi\.', '.*.hi.', base_name)
                base_name = re.sub(r'\.' + detected_lang + r'-hi\.', '.*-hi.', base_name)
            else:
                # Standard replacements for other patterns
                base_name = re.sub(r'\.' + detected_lang + r'\.', '.*.', base_name)
                base_name = re.sub(r'\.' + detected_lang + r'-', '.*-', base_name)
                base_name = re.sub(r'\.' + detected_lang + r'_', '.*_', base_name)
                base_name = re.sub(r'_' + detected_lang + r'_', '_*_', base_name)
                base_name = re.sub(r'-' + detected_lang + r'-', '-*-', base_name)
                # For languages at the start of filename
                base_name = re.sub(r'^' + detected_lang + r'\.', '*.', base_name)
                base_name = re.sub(r'^' + detected_lang + r'-', '*-', base_name)
            
            # Use a combination of directory and base name as the group key
            # This handles cases where the same filename appears in different directories
            group_key = os.path.join(file_dir, base_name)
            
            if group_key not in file_groups:
                file_groups[group_key] = {}
            
            file_groups[group_key][detected_lang] = file_path
        
        # Log summary of unmatched files
        if unmatched_files:
            logger.info(f"Found {len(unmatched_files)} files without detectable language code")
            if len(unmatched_files) < 10:  # Only log details if there are a few files
                for file_path in unmatched_files:
                    logger.debug(f"Unmatched file: {os.path.basename(file_path)}")
        
        # Log summary of files we're skipping because they already have target language
        if skip_these_files:
            logger.info(f"Found {len(skip_these_files)} files that already have target language '{tgt_lang_code}'")
            if len(skip_these_files) < 10:  # Only log details if there are a few files
                for file_path in skip_these_files:
                    logger.debug(f"Skipping target language file: {os.path.basename(file_path)}")
        
        # Now select files for translation (source lang exists, target lang doesn't)
        srt_files = []
        skipped_files = []
        
        for group_key, lang_files in file_groups.items():
            # Check if we have a source language file but no target language file
            if src_lang_code in lang_files and tgt_lang_code not in lang_files:
                source_file = lang_files[src_lang_code]
                # Double-check we haven't already flagged this file to skip
                if source_file not in skip_these_files:
                    logger.info(f"Adding {os.path.basename(lang_files[src_lang_code])} to translation queue")
                    srt_files.append(lang_files[src_lang_code])
                else:
                    logger.info(f"Skipping {os.path.basename(lang_files[src_lang_code])} - flagged as target language file")
                    skipped_files.append(lang_files[src_lang_code])
            elif src_lang_code in lang_files and tgt_lang_code in lang_files:
                logger.info(f"Skipping {os.path.basename(lang_files[src_lang_code])} - target version already exists: {os.path.basename(lang_files[tgt_lang_code])}")
                skipped_files.append(lang_files[src_lang_code])
            elif src_lang_code not in lang_files and tgt_lang_code in lang_files:
                logger.debug(f"Skipping {os.path.basename(lang_files[tgt_lang_code])} - target only, no source")
                # Not counted as skipped since we don't have a source file
        
        if not srt_files:
            if skipped_files:
                progress["status"] = "completed"
                progress["message"] = f"No new subtitle files to translate. {len(skipped_files)} files already have target language versions."
            else:
                progress["status"] = "completed"
                progress["message"] = f"No subtitle files found in {root_dir}"
            # Save progress state to file after status change
            save_progress_state()
            # Cleanup temp directory
            try:
                import shutil
                shutil.rmtree(temp_extract_dir)
                logger.info(f"Cleaned up temporary extraction directory: {temp_extract_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {temp_extract_dir}: {e}")
            return
        
        # Initialize translation components
        subtitle_processor = SubtitleProcessor(logger)
        
        # Update progress
        progress["total_files"] = len(srt_files)
        progress["status"] = "translating"
        progress["message"] = f"Found {len(srt_files)} files to translate. Skipped {len(skipped_files)} files that already have {tgt_lang} versions."
        # Save progress state to file after status change
        save_progress_state()
        
        # Create a temporary directory for the translated files
        temp_dir = tempfile.mkdtemp(prefix="srt_translate_")
        translated_files = []
        
        # Translate each file
        for i, srt_file in enumerate(srt_files):
            file_name = os.path.basename(srt_file)
            progress["current_file"] = file_name
            progress["message"] = f"Translating {file_name} ({i+1}/{len(srt_files)})"
            # Reset current and processed_lines for the new file
            progress["current"] = {}
            progress["processed_lines"] = []
            # Save progress state to file at the start of each file
            save_progress_state()
            
            try:
                # Generate translated filename
                base, ext = os.path.splitext(file_name)
                
                # Determine if this is an extracted file (extract from the path, not the filename)
                is_extracted = srt_file in extracted_subtitle_files
                
                # Different filename handling for extracted vs regular files
                if is_extracted:
                    # For extracted files, replace the language code directly
                    if src_lang_code in base.lower():
                        out_base = base.replace(src_lang_code, tgt_lang_code)
                    elif src_lang_code_3letter and src_lang_code_3letter in base.lower():
                        # Handle 3-letter codes like 'eng' to 'dan'
                        tgt_lang_code_3letter = "dan" if tgt_lang_code == "da" else tgt_lang_code 
                        out_base = base.replace(src_lang_code_3letter, tgt_lang_code_3letter)
                    else:
                        # If can't find language code, just append target language
                        out_base = f"{base}.{tgt_lang_code}"
                else:
                    # Try to replace language code in filename if it exists
                    out_base = base
                    replaced = False
                    patterns = [
                        f'.{src_lang}.', f'.{src_lang}-', f'.{src_lang}_',
                        f'{src_lang}.', f'-{src_lang}.', f'_{src_lang}.'
                    ]
                    for pat in patterns:
                        if pat in base.lower():
                            newpat = pat.replace(src_lang, tgt_lang)
                            out_base = re.sub(pat, newpat, base, flags=re.IGNORECASE)
                            replaced = True
                            break
                    if not replaced:
                        out_base = f"{base}.{tgt_lang}"
                
                translated_filename = secure_filename(f"{out_base}{ext}")
                output_path = os.path.join(temp_dir, translated_filename)
                archive_path = os.path.join(app.config['UPLOAD_FOLDER'], translated_filename)
                
                # Check if the output file already exists in the archive
                if (os.path.exists(archive_path)):
                    logger.info(f"Output file {translated_filename} already exists in archive, skipping translation")
                    # Copy the existing file to the temp directory for inclusion in the zip
                    import shutil
                    shutil.copy2(archive_path, output_path)
                    translated_files.append(output_path)
                    progress["done_files"] += 1
                    progress["message"] = f"Skipped {file_name}: target version already exists in archive"
                    save_progress_state()
                    continue
                
                # Use the translate_srt method which handles detailed progress reporting
                success = subtitle_processor.translate_srt(
                    srt_file,
                    archive_path,
                    config,
                    progress_dict=progress  # Pass the progress dict for detailed tracking
                )
                
                if success:
                    # Copy the file to the temporary directory for the ZIP file
                    import shutil
                    shutil.copy2(archive_path, output_path)
                    
                    # NEW CODE: Also save alongside the original file
                    original_dir = os.path.dirname(srt_file)
                    alongside_path = os.path.join(original_dir, translated_filename)
                    try:
                        # Copy the translated file to the original directory
                        shutil.copy2(archive_path, alongside_path)
                        logger.info(f"Also saved translation alongside original: {alongside_path}")
                    except Exception as e:
                        logger.error(f"Failed to save alongside original: {e}")
                    
                    translated_files.append(output_path)
                    progress["done_files"] += 1
                    # Save progress state after completing each file
                    save_progress_state()
                else:
                    logger.error(f"Failed to translate {file_name}")
                    progress["message"] = f"Error translating {file_name}"
                    save_progress_state()
                
            except Exception as e:
                error_msg = f"Error translating {file_name}: {str(e)}"
                logger.error(error_msg)
                progress["message"] = error_msg
                # Save progress state after error
                save_progress_state()
                # Continue with next file
        
        # Create ZIP file with all translated subtitles
        if translated_files:
            zip_path = os.path.join(tempfile.gettempdir(), f"translated_subtitles_{int(time.time())}.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in translated_files:
                    zipf.write(file, os.path.basename(file))
            
            # Update progress
            progress["status"] = "completed"
            progress["message"] = f"Translated {progress['done_files']} subtitle files. Skipped {len(skipped_files)} files that already had {tgt_lang} versions."
            progress["zip_path"] = zip_path
            # Save final progress state to file
            save_progress_state()
        else:
            progress["status"] = "completed"
            progress["message"] = "No files were successfully translated"
            # Save final progress state to file
            save_progress_state()
        
        # Cleanup temp directories
        try:
            import shutil
            shutil.rmtree(temp_extract_dir)
            logger.info(f"Cleaned up temporary extraction directory: {temp_extract_dir}")
            
            # Only remove the temp_dir after we've created the zip file
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temporary translation directory: {temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean up temporary directories: {e}")
        
    except Exception as e:
        error_msg = f"Error during bulk translation: {str(e)}"
        logger.error(error_msg)
        progress["status"] = "failed"
        progress["message"] = error_msg
        # Save error state to file
        save_progress_state()
        
        # Cleanup any temp directories even on error
        try:
            import shutil
            if 'temp_extract_dir' in locals():
                shutil.rmtree(temp_extract_dir)
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir)
        except Exception as cleanup_err:
            logger.warning(f"Failed to clean up temporary directories: {cleanup_err}")

@app.route('/api/special_meanings', methods=['GET'])
def api_special_meanings():
    """API endpoint to get special word meanings from the file."""
    try:
        # Initialize translation service to load meanings
        config = config_manager.get_config()
        translation_service = TranslationService(config, logger)
        
        # Get meanings from the translation service
        meanings = translation_service.special_meanings
        if not meanings:
            # Try to load directly from file as fallback
            meanings_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'files', 'meaning.json')
            if os.path.exists(meanings_file):
                with open(meanings_file, 'r', encoding='utf-8') as f:
                    meanings = json.load(f)
            else:
                meanings = []
                
        logger.info(f"Retrieved {len(meanings)} special meanings from file")
        return jsonify({"success": True, "meanings": meanings})
    except Exception as e:
        logger.error(f"Error retrieving special meanings: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/special_meanings', methods=['POST'])
def api_update_special_meanings():
    """API endpoint to update special word meanings in the file."""
    try:
        meanings = request.json.get('meanings', [])
        
        # Initialize translation service
        config = config_manager.get_config()
        translation_service = TranslationService(config, logger)
        
        # Save to file
        success = translation_service.save_special_meanings(meanings)
        
        # Update the in-memory meanings
        translation_service.special_meanings = meanings
        
        if success:
            logger.info(f"Updated {len(meanings)} special meanings")
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "message": "Failed to save meanings"}), 500
    except Exception as e:
        logger.error(f"Error updating special meanings: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/browse_videos', methods=['GET'])
def api_browse_videos():
    """API endpoint to list video files in a directory for the host file browser."""
    parent_path = request.args.get("path", "")
    
    # Default to system root directories if no path provided
    if not parent_path:
        if os.name == "nt":  # Windows
            import string
            # Get all drives
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return jsonify({
                "files": [],
                "directories": [{"name": d, "path": d} for d in drives],
                "current_path": "",
                "parent_path": ""
            })
        else:  # Unix-like
            parent_path = "/"
    
    try:
        # Security check: normalize path to prevent directory traversal
        parent_path = os.path.normpath(parent_path)
        
        # Get the parent of the current directory for "up one level" functionality
        parent_of_parent = os.path.dirname(parent_path) if parent_path != "/" else ""
        
        # List all items in the parent path
        files = []
        dirs = []
        
        if os.path.isdir(parent_path):
            for item in os.listdir(parent_path):
                full_path = os.path.join(parent_path, item)
                
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
                else:
                    # Only include video files
                    if item.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')):
                        files.append({"name": item, "path": full_path})
            
            # Sort directories and files by name
            dirs.sort(key=lambda x: x["name"].lower())
            files.sort(key=lambda x: x["name"].lower())
            
            return jsonify({
                "files": files,
                "directories": dirs,
                "current_path": parent_path,
                "parent_path": parent_of_parent
            })
        else:
            return jsonify({"error": "Not a valid directory"}), 400
    except PermissionError:
        return jsonify({"error": "Permission denied accessing this directory"}), 403
    except Exception as e:
        logger.error(f"Error browsing files in directory {parent_path}: {str(e)}")
        return jsonify({"error": f"Error accessing directory: {str(e)}"}), 500

@app.route('/api/video_to_srt', methods=['POST'])
def api_video_to_srt():
    """API endpoint to transcribe a video file to SRT format using faster-whisper."""
    try:
        # Check if a host file path was provided
        video_file_path = request.form.get('video_file_path', '')
        
        if not video_file_path:
            return jsonify({"error": "No video file path provided"}), 400
            
        if not os.path.isfile(video_file_path):
            return jsonify({"error": "Invalid file path or file does not exist"}), 400
            
        # Check if it's a video file
        if not video_file_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')):
            return jsonify({"error": "Only video files are supported"}), 400
            
        # Get the filename without path
        filename = os.path.basename(video_file_path)
        
        # Create a job ID based on the filename and timestamp
        timestamp = int(time.time())
        job_id = f"whisper_{timestamp}_{filename}"
        
        # Get language if provided (optional)
        language = request.form.get('language', None)
        
        # Create/update the job record
        translation_jobs[job_id] = {
            'status': 'queued',
            'source_path': video_file_path,
            'original_filename': filename,
            'progress': 0,
            'message': 'Queued for transcription with faster-whisper',
            'start_time': time.time(),
            'end_time': None,
            'language': language,
            'type': 'transcription'  # Mark this as a transcription job
        }
        
        # Start the transcription in a background thread
        threading.Thread(
            target=process_video_transcription,
            args=(job_id, video_file_path, language)
        ).start()

        return jsonify({
            "status": "success",
            "message": "Video queued for transcription",
            "job_id": job_id
        })

    except Exception as e:
        logger.exception("Error in video transcription")
        return jsonify({"error": str(e)}), 500

@app.route('/api/whisper/check_server', methods=['GET'])
def api_check_whisper_server():
    """API endpoint to check if the faster-whisper server is reachable."""
    try:
        # Get server URL from config
        config = config_manager.get_config()
        whisper_server = config.get('whisper', 'server_url', fallback='http://10.0.10.23:10300')
        
        # Initialize transcriber and check server
        transcriber = VideoTranscriber(server_url=whisper_server, logger=logger)
        success, message = transcriber.ping_server()
        
        # If the TCP check passes but HTTP health check fails, still consider it a partial success
        if not success and "TCP connection" in message:
            # This is a total connectivity failure
            logger.error(f"Failed to connect to whisper server: {message}")
            return jsonify({
                "success": False,
                "message": message,
                "server_url": whisper_server
            })
        elif "port is open but" in message:
            # TCP connection succeeded but HTTP check failed - consider this a partial success
            logger.warning(f"Partial connection to whisper server: {message}")
            return jsonify({
                "success": True,
                "message": message,
                "server_url": whisper_server,
                "partial": True  # Flag to indicate partial connectivity
            })
        elif success:
            # Full success
            logger.info(f"Successfully connected to whisper server at {whisper_server}")
            return jsonify({
                "success": True,
                "message": message,
                "server_url": whisper_server
            })
        else:
            # Any other failure
            logger.error(f"Failed to connect to whisper server: {message}")
            return jsonify({
                "success": False,
                "message": message,
                "server_url": whisper_server
            })
    except Exception as e:
        logger.exception(f"Error checking whisper server: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"Error checking server: {str(e)}",
            "error_type": "exception"
        }), 500

def process_video_transcription(job_id, video_path, language=None):
    """Process a video transcription job using faster-whisper."""
    job = translation_jobs[job_id]
    logger.info(f"Starting transcription job {job_id}: {job['original_filename']}")
    
    # Use the global progress dictionary for status updates
    global bulk_translation_progress
    progress_dict = bulk_translation_progress
    
    try:
        # Reset and update global progress status for this job
        progress_dict.clear()
        progress_dict.update({
            "mode": "transcription",
            "status": "processing",
            "message": f'Starting transcription for {job["original_filename"]}',
            "current_file": job["original_filename"],
            "job_id": job_id
        })
        
        # Save progress state to file
        save_progress_state()
        
        # Update job status
        job['status'] = 'processing'
        job['message'] = 'Extracting audio from video...'
        
        # Initialize the video transcriber
        config = config_manager.get_config()
        whisper_server = config.get('whisper', 'server_url', fallback='http://10.0.10.23:10300')
        transcriber = VideoTranscriber(server_url=whisper_server, logger=logger)
        
        # Transcribe the video - use chunked mode by default
        job['message'] = 'Transcribing video with faster-whisper using chunked processing...'
        success, message, result = transcriber.transcribe_video(
            video_path, 
            language=language,
            use_chunks=True,      # Enable chunking for better server compatibility
            chunk_duration=30     # 30-second chunks
        )
        
        if success:
            if result.get('transcription_type') == 'chunked':
                # This is a chunked transcription result
                job['chunks'] = result.get('chunks', [])
                job['status'] = 'processing'
                job['message'] = f'Processed {len(job["chunks"])}/{result.get("num_chunks", 0)} chunks'
                job['progress'] = 80
                
                # Store the local job ID
                local_job_id = result.get('job_id')
                job['whisper_job_id'] = local_job_id
                
                # Generate output filename
                file_base = os.path.splitext(job['original_filename'])[0]
                output_filename = f"{file_base}_whisper.srt"
                output_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(output_filename))
                
                # Download (generate) the SRT file from chunks
                download_success, download_message = transcriber.download_srt(local_job_id, output_path)
                
                if download_success:
                    job['status'] = 'completed'
                    job['target_path'] = output_path
                    job['output_filename'] = output_filename
                    job['message'] = 'Chunked transcription and SRT generation complete'
                    job['progress'] = 100
                    job['end_time'] = time.time()
                    logger.info(f"Chunked transcription job {job_id} completed successfully")
                    
                    # Update global progress
                    progress_dict["status"] = "completed"
                    progress_dict["message"] = f"Chunked transcription completed for {job['original_filename']}"
                else:
                    raise Exception(f"Failed to generate SRT from chunks: {download_message}")
                
            elif 'job_id' in result:
                # This is a server-side job that needs polling
                job_id_from_server = result.get('job_id')
                job['whisper_job_id'] = job_id_from_server
                job['status'] = 'processing'
                job['message'] = f'Transcription in progress: {message}'
                job['progress'] = 30
                
                # Poll for completion
                completed = False
                retry_count = 0
                max_retries = 120  # 10 minutes (5s * 120)
                
                while not completed and retry_count < max_retries:
                    time.sleep(5)  # Wait 5 seconds between status checks
                    retry_count += 1
                    
                    # Update progress with linear approximation
                    progress = min(30 + (retry_count * 50 / max_retries), 80)
                    job['progress'] = progress
                    job['message'] = f'Transcription in progress ({progress:.0f}%)...'
                    
                    # Check status
                    status_success, status_message, status_data = transcriber.get_transcription_status(job_id_from_server)
                    
                    if status_success:
                        status = status_data.get('status')
                        if status == 'completed':
                            completed = True
                            job['status'] = 'processing'
                            job['message'] = 'Transcription complete. Downloading SRT file...'
                            job['progress'] = 90
                            
                            # Generate output filename
                            file_base = os.path.splitext(job['original_filename'])[0]
                            output_filename = f"{file_base}_whisper.srt"
                            output_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(output_filename))
                            
                            # Download the SRT file
                            download_success, download_message = transcriber.download_srt(job_id_from_server, output_path)
                            
                            if download_success:
                                job['status'] = 'completed'
                                job['target_path'] = output_path
                                job['output_filename'] = output_filename
                                job['message'] = 'Transcription and download complete'
                                job['progress'] = 100
                                job['end_time'] = time.time()
                                logger.info(f"Transcription job {job_id} completed successfully")
                                
                                # Update global progress
                                progress_dict["status"] = "completed"
                                progress_dict["message"] = f"Transcription completed for {job['original_filename']}"
                            else:
                                raise Exception(f"Failed to download SRT: {download_message}")
                        elif status == 'failed':
                            raise Exception(f"Transcription failed on server: {status_data.get('error', 'Unknown error')}")
                        else:
                            # Still processing
                            progress_info = status_data.get('progress', {})
                            if progress_info:
                                task_progress = progress_info.get('progress', 0) * 100
                                job['message'] = f"Transcribing: {task_progress:.1f}% - {progress_info.get('task', 'processing')}"
                                job['progress'] = 30 + (task_progress * 0.5)  # Scale server progress to 30-80% range
                    else:
                        logger.warning(f"Failed to get transcription status: {status_message}")
                
                if not completed:
                    raise Exception("Transcription timed out after 10 minutes")
            else:
                # Direct transcription result with text
                job['status'] = 'processing'
                job['message'] = 'Generating SRT from transcription...'
                job['progress'] = 90
                
                # Create an SRT file from the result
                file_base = os.path.splitext(job['original_filename'])[0]
                output_filename = f"{file_base}_whisper.srt"
                output_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(output_filename))
                
                # Extract text from result
                text = result.get('text', '')
                if not text:
                    # Try to find text in other formats
                    if 'result' in result:
                        text = result['result'].get('text', '')
                    elif 'transcripts' in result:
                        text = result['transcripts'][0] if result['transcripts'] else ''
                
                # Generate a basic SRT file
                import datetime
                with open(output_path, 'w', encoding='utf-8') as f:
                    # Create a simple SRT with the text as a single subtitle
                    f.write("1\n00:00:00,000 --> 00:05:00,000\n" + text.strip() + "\n\n")
                
                job['status'] = 'completed'
                job['target_path'] = output_path
                job['output_filename'] = output_filename
                job['message'] = 'Transcription and SRT generation complete'
                job['progress'] = 100
                job['end_time'] = time.time()
                logger.info(f"Direct transcription job {job_id} completed successfully")
                
                # Update global progress
                progress_dict["status"] = "completed"
                progress_dict["message"] = f"Transcription completed for {job['original_filename']}"
        else:
            raise Exception(f"Failed to start transcription: {message}")
            
    except Exception as e:
        error_message = f"Error in transcription job {job_id}: {str(e)}"
        logger.error(error_message)
        import traceback
        logger.error(traceback.format_exc())
        
        # Update job status
        job['status'] = 'failed'
        job['message'] = error_message
        job['progress'] = 0
        job['end_time'] = time.time()
        
        # Update global progress
        progress_dict["status"] = "failed"
        progress_dict["message"] = error_message
        
        # Save error state
        save_progress_state()

def allowed_file(filename):
    """Check if file has an allowed extension."""
    return '.' in filename and \
           filename.lower().endswith(('.srt', '.ass', '.vtt'))

if __name__ == '__main__':
    # Create default config if it doesn't exist
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')):
        config_manager.create_default_config()
    
    # Get host and port from config
    config = config_manager.get_config()
    host = config.get('general', 'host', fallback='127.0.0.1')
    port = config.getint('general', 'port', fallback=5089)
    debug = config.getboolean('webui', 'debug', fallback=False)
    
    # Start the app with a more accurate welcome message
    print("==========================================")
    print("Starting Subtitle Translator...")
    print("==========================================")
    print(f"If your browser doesn't open automatically, navigate to http://{host}:{port}")
    print("Press Ctrl+C to stop the application.")
    print("==========================================")
    
    # Start the app
    app.run(host=host, port=port, debug=debug)