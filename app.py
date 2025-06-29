import os
import shutil
import sys
import logging
import re
import tempfile
import zipfile
import threading
import traceback
from typing import Optional, Dict, Any, Callable, cast, List, Union, TypeVar, Tuple
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, send_from_directory, Response
from flask.typing import ResponseReturnValue  # This includes the tuple form of Response
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
from py.secure_browser import SecureFileBrowser
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
app_config = config_manager.get_config()  # Use a different name to avoid shadowing the function name

# Setup logging with correct level based on debug_mode
# Ensure 'general' section and 'debug_mode' option exist, providing fallbacks
if app_config and app_config.has_section('general') and app_config.has_option('general', 'debug_mode'):
    debug_mode = app_config.getboolean('general', 'debug_mode')
else:
    debug_mode = False # Default fallback
log_level = logging.DEBUG if debug_mode else logging.INFO
logger = setup_logger('app', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translator.log'), level=log_level)

if debug_mode:
    logger.debug("Debug mode is enabled - full LLM prompts will be logged")

# Translation jobs storage
translation_jobs: Dict[str, Dict[str, Any]] = {}

# Locks for thread-safe access to shared resources
progress_lock = threading.RLock()
jobs_lock = threading.Lock()

# Progress status file path
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translation_progress.json')

# Global variable for bulk translation progress tracking
bulk_translation_progress: Dict[str, Any] = {
    "mode": "idle",
    "status": "idle", 
    "message": "",
    "current_file": "",
    "done_files": 0,
    "total_files": 0,
    "zip_path": ""
}

# Progress data for individual transcription jobs
transcription_job_progress: Dict[str, Dict[str, Any]] = {}

def save_progress_state() -> None:
    """Save the current progress state to file."""
    try:
        with progress_lock:
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump(bulk_translation_progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save progress state: {e}")

def load_progress_state() -> None:
    """Load the saved progress state from file."""
    global bulk_translation_progress
    try:
        if os.path.exists(PROGRESS_FILE):
            with progress_lock:
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

# Initialize by loading any saved state
load_progress_state()

def process_video_transcription(job_id: str, video_path: str, language: Optional[str] = None) -> None:
    """
    Process video transcription with progress tracking.
    
    Args:
        job_id (str): Unique identifier for the transcription job
        video_path (str): Path to the video file
        language (Optional[str]): Language code for transcription, defaults to None for auto-detection
    """
    def update_progress(percent: float, message: str, status: str, current_job_id: str) -> None:
        """Update the main progress dictionary with transcription progress."""
        with progress_lock:
            transcription_job_progress[current_job_id] = {
                'percent': percent,
                'message': message,
                'status': status
            }
            save_progress_state()
    
    with jobs_lock:
        job = translation_jobs[job_id]
        job['status'] = 'processing'
        job['message'] = 'Starting transcription...'
    
    try:
        # Create output directory if it doesn't exist
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate output path
        output_filename = f"{os.path.splitext(os.path.basename(video_path))[0]}.srt"
        output_path = os.path.join(output_dir, output_filename)
        
        # Call the transcription function with proper type handling
        if language is None:
            language = "auto"  # Use auto-detection if no language specified
        
        # Get server URL from app_config
        whisper_server = app_config.get('whisper', 'server_url', fallback='http://10.0.10.23:10300')
        
        # Initialize transcriber and use it to transcribe the video
        transcriber = VideoTranscriber(server_url=whisper_server, logger=logger)
        transcriber.transcribe_video_to_srt(
            video_path,
            output_path,
            language=language,
            job_id=job_id,
            external_progress_updater=update_progress
        )
        
        with jobs_lock:
            job['status'] = 'completed'
            job['message'] = 'Transcription completed successfully'
            job['output_file'] = output_path
            
    except Exception as e:
        logger.error(f"Error in video transcription: {str(e)}")
        with jobs_lock:
            job['status'] = 'failed'
            job['message'] = f'Transcription failed: {str(e)}'

@app.route('/api/transcription_progress/<job_id>', methods=['GET'])
def get_transcription_progress(job_id: str) -> ResponseReturnValue:
    # VideoTranscriber should be imported at the top of the file
    progress_data = VideoTranscriber.get_progress(job_id) # Uses the class method
    if progress_data:
        return jsonify(progress_data)
    else:
        # Return a 200 response with a structured JSON response indicating no progress data
        return jsonify({
            "status": "unknown", 
            "job_id": job_id, 
            "message": "No transcription progress data found for this job ID.", 
            "percent": 0
        })

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
def index() -> ResponseReturnValue:
    """Render the home page with recent translations."""
    config = config_manager.get_config()
    # Use source_language and target_language from config instead of default_source_language and default_target_language
    default_source = config.get('general', 'source_language', fallback='en')
    default_target = config.get('general', 'target_language', fallback='da')
    
    # Get list of recent translations
    recent_files = get_recent_translations()
    
    # Use the global LANGUAGES list instead of getting from translation_service
    
    return render_template('index.html', 
                          languages=LANGUAGES, 
                          default_source=default_source,
                          default_target=default_target,
                          recent_files=recent_files,
                          debug=debug_mode)

@app.route('/transcribe')
def transcribe() -> ResponseReturnValue:
    """Render the video transcription page."""
    config = config_manager.get_config()
    default_source = config.get('general', 'source_language', fallback='en')
    # Use the global LANGUAGES list instead of getting from translation_service
    
    return render_template('transcribe.html',
                          languages=LANGUAGES,
                          debug=debug_mode)

@app.route('/bulk_translate')
def bulk_translate() -> ResponseReturnValue:
    """Render the bulk translation page."""
    config = config_manager.get_config()
    default_source = config.get('general', 'source_language', fallback='en')
    default_target = config.get('general', 'target_language', fallback='da')
    # Use the global LANGUAGES list instead of getting from translation_service
    
    return render_template('bulk_translate.html',
                          languages=LANGUAGES,
                          default_source=default_source,
                          default_target=default_target,
                          debug=debug_mode)

@app.route('/archive')
def archive() -> ResponseReturnValue:
    """Render the subtitle archive page."""
    return render_template('archive.html', debug=debug_mode)

@app.route('/logs')
def logs() -> ResponseReturnValue:
    """Render the log viewer page."""
    log_files = get_log_files()
    current_log = 'translator.log'
    log_content = get_log_content(current_log)
    
    return render_template('log_viewer.html',
                          log_files=log_files,
                          current_log=current_log,
                          log_content=log_content)

@app.route('/config')
def config_route() -> ResponseReturnValue:
    """Render the configuration editor page."""
    return render_template('config_editor.html')

@app.route('/api/config', methods=['GET', 'POST'])
def api_config() -> ResponseReturnValue: 
    if request.method == 'POST':
        try:
            config_data = request.get_json()
            if config_data is None:
                logger.error("Received empty JSON payload for config update.")
                return jsonify({"error": "Invalid JSON payload"}), 400
            
            config_manager.save_config(config_data)
            global app_config # Ensure we're updating the global app_config variable 
            app_config = cast(configparser.ConfigParser, config_manager.get_config()) # Re-cast after update
            logger.info("Configuration saved successfully.")
            return jsonify({"message": "Configuration saved successfully"})
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            return jsonify({"error": str(e)}), 500
    else: # GET request
        return jsonify(config_manager.get_config_as_dict())

@app.route('/api/logs') # Assuming GET method by default
def api_logs() -> ResponseReturnValue: 
    log_file_name = request.args.get('file', 'translator.log')
    # Ensure log_file_name is a string, even if it's from request.args.get
    content = get_log_content(str(log_file_name))
    return jsonify({'logs': content.splitlines() if content else []})

@app.route('/api/clear_log', methods=['POST'])
def api_clear_log() -> ResponseReturnValue: 
    data = request.get_json()
    if data is None:
        return jsonify({'success': False, 'message': 'Invalid JSON payload'}), 400
    log_file_name = data.get('file', 'translator.log')
    success = clear_log_file(str(log_file_name)) # Ensure string
    return jsonify({'success': success})

@app.route('/api/translate', methods=['POST'])
def api_translate() -> ResponseReturnValue:
    """Handle file upload and start translation."""
    try:
        # Check if a host file path was provided instead of a file upload
        host_file_path = request.form.get('host_file_path', '')
        
        if (host_file_path):
            # Get a secure browser instance for path validation
            secure_browser = get_secure_browser()
            
            # Normalize the path before validation
            normalized_path = os.path.abspath(os.path.normpath(host_file_path))
            
            # Validate the path is allowed using our secure browser
            if not secure_browser.is_path_allowed(normalized_path):
                logger.warning(f"Access denied for file: {normalized_path}. Not within allowed bases or matches denied pattern.")
                return jsonify({"error": "Access to this file is restricted."}), 403
            
            # After validation, use the normalized path
            requested_abs_path = normalized_path
            
            # Additional validations
            if not os.path.isfile(requested_abs_path):
                return jsonify({"error": "Invalid file path or file does not exist"}), 400
                
            # Check if it's a subtitle file
            if not requested_abs_path.lower().endswith(('.srt', '.ass', '.vtt')):
                return jsonify({"error": "Only subtitle files (.srt, .ass, .vtt) are supported"}), 400
                
            # Get the filename without path
            filename = os.path.basename(requested_abs_path)
            
            # Create a job ID based on the filename and timestamp
            timestamp = int(time.time())
            job_id = f"{timestamp}_{filename}"
            
            # Copy the file to the cache directory
            cache_path = os.path.join(CACHE_DIR, filename)
            shutil.copy2(requested_abs_path, cache_path)
            logger.info(f"Using host file: {requested_abs_path}, copied to {cache_path}")
            
        else:
            # Handle regular file upload
            if 'file' not in request.files:
                return jsonify({"error": "No file part in the request"}), 400
                
            file = request.files['file']
            
            # Check if a filename is present
            if not file.filename:
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

@app.route('/download/<job_id>')
def download_translation(job_id) -> ResponseReturnValue:
    """Endpoint for downloading a completed translation."""
    if job_id not in translation_jobs or translation_jobs[job_id]['status'] != 'completed':
        return redirect(url_for('index'))
    
    job = translation_jobs[job_id]
    return send_file(job['target_path'], 
                     as_attachment=True, 
                     download_name=f"translated_{job['original_filename']}")

@app.route('/api/view_subtitle/<path:file_or_job_id>')
def api_view_subtitle(file_or_job_id) -> ResponseReturnValue:
    """API endpoint for viewing a subtitle file. Can accept either a job ID or a filename."""
    # First, try handling it as a job ID
    if file_or_job_id in translation_jobs:
        job = translation_jobs[file_or_job_id]
        file_path = job['target_path'] if job['status'] == 'completed' else job['source_path']
        filename = os.path.basename(file_path)
    else:
        # If not a job ID, treat as a filename in the subs folder
        safe_filename = secure_filename(file_or_job_id)
        if safe_filename != file_or_job_id:
            return jsonify({'success': False, 'message': 'Invalid filename'})
        
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        filename = safe_filename
        
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'File not found: {filename}'})
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({
            'success': True,
            'content': content,
            'filename': filename
        })
    except Exception as e:
        logger.error(f"Error reading subtitle file: {str(e)}")
        return jsonify({'success': False, 'message': f"Error reading file: {str(e)}"})

@app.route('/api/delete_sub/<path:filename>', methods=['DELETE'])
def api_delete_subtitle(filename) -> ResponseReturnValue:
    """API endpoint for deleting a subtitle file."""
    # Ensure the filename is safe
    safe_filename = secure_filename(filename)
    if safe_filename != filename:
        return jsonify({'success': False, 'message': 'Invalid filename'})
    
    # Check if the file exists
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': f'File not found: {filename}'})
    
    try:
        # Delete the file
        os.remove(file_path)
        
        # Also delete any associated report files
        report_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{safe_filename}.report.txt")
        alt_report_path = os.path.join(app.config['UPLOAD_FOLDER'], f"report_{safe_filename}.txt")
        
        if os.path.exists(report_path):
            os.remove(report_path)
        
        if os.path.exists(alt_report_path):
            os.remove(alt_report_path)
        
        return jsonify({
            'success': True,
            'message': f'File {filename} successfully deleted'
        })
    except Exception as e:
        logger.error(f"Error deleting subtitle file: {str(e)}")
        return jsonify({'success': False, 'message': f"Error deleting file: {str(e)}"})

@app.route('/upload', methods=['POST'])
def upload() -> ResponseReturnValue: 
    if 'srtfile' not in request.files:
        flash("No SRT file part in the request.", "error")
        return redirect(url_for("index"))
        
    file = request.files['srtfile']
    
    # Check if a filename is present
    if not file.filename:
        flash("No selected file.", "error")
        return redirect(url_for("index"))
        
    # Now that we know file.filename is not None or empty, we can use it
    if not file.filename.lower().endswith('.srt'):
        flash("Invalid file type. Please upload an SRT file.", "error")
        return redirect(url_for("index"))

    # Save the uploaded file temporarily for processing
    # secure_filename is safe to call now
    temp_input_path = os.path.join(tempfile.gettempdir(), secure_filename(f"temp_{file.filename}"))
    try:
        file.save(temp_input_path)
        logger.info(f"Received SRT: {file.filename} -> {temp_input_path}")
    except Exception as e:
        logger.error(f"Failed to save temporary input file: {e}")
        flash("Server error saving uploaded file.", "error")
        return redirect(url_for("index"))

    # Get language codes from config
    # Ensure global config is used if not shadowed
    global_config = cast(configparser.ConfigParser, config_manager.get_config())
    src_lang = global_config.get("general", "source_language", fallback="en")
    tgt_lang = global_config.get("general", "target_language", fallback="da")
    
    # Determine output filename
    # file.filename is guaranteed to be a string here
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
        translation_service = TranslationService(app_config, logger)
        
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
def get_progress() -> ResponseReturnValue:
    """API endpoint for getting translation progress."""
    with progress_lock:
        return jsonify(bulk_translation_progress)

@app.route('/api/list_subs')
def api_list_subs() -> ResponseReturnValue:
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

@app.route('/api/recent_files')
def api_recent_files() -> ResponseReturnValue:
    """API endpoint for getting recent subtitle files."""
    try:
        # Get the recent files from the get_recent_translations function
        recent_files = get_recent_translations()
        
        # Format the response to match what the frontend expects
        formatted_files = []
        for file in recent_files:
            # Assuming the recent_files function returns filenames
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], file)
            if os.path.exists(file_path):
                mod_time = os.path.getmtime(file_path)
                formatted_files.append({
                    'name': file,
                    'path': file,
                    'date': datetime.fromtimestamp(mod_time).isoformat()
                })
        
        return jsonify({"status": "success", "files": formatted_files})
    except Exception as e:
        logger.error(f"Failed to get recent files: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/download_sub/<path:filename>')
def download_sub_file(filename) -> ResponseReturnValue:
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
def api_delete_sub(filename) -> ResponseReturnValue:
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
def api_browse_dirs() -> ResponseReturnValue:
    """API endpoint to list directories for the file browser."""
    parent_path = request.args.get("path", "")
    
    # Get secure browser instance
    secure_browser = get_secure_browser()
    
    # If no allowed paths configured, restrict access
    if not secure_browser.allowed_paths:
        logger.warning("File browsing attempted but no 'allowed_paths' configured in [file_browser] section.")
        return jsonify({"error": "File browsing is not configured or no paths are allowed."}), 403
    
    # Default to first allowed path if no path provided
    if not parent_path:
        # If no path provided, return configured allowed_paths as root entries
        roots = [{"name": os.path.basename(p) or p, "path": p} for p in secure_browser.allowed_paths]
        resp = jsonify({"directories": roots, "files": [], "current_path": "", "parent_path": ""})
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp
    
    try:
        # Validate the requested path
        if not secure_browser.is_path_allowed(parent_path):
            logger.warning(f"Access denied for path: {parent_path}. Not within allowed bases.")
            return jsonify({"error": "Access to this path is restricted."}), 403
        
        # Normalize path
        requested_abs_path = os.path.abspath(os.path.normpath(parent_path))
        
        # Get the parent path if navigation is allowed
        parent_of_parent = secure_browser.get_safe_parent_path(requested_abs_path)
        
        # List all directories in the parent path
        dirs = []
        if os.path.isdir(requested_abs_path):
            # Get all items in the directory
            items = os.listdir(requested_abs_path)
            
            # Filter items based on security rules
            filtered_items = secure_browser.filter_items(requested_abs_path, items)
            
            # Add all directories to the result
            for item in filtered_items:
                full_path = os.path.join(requested_abs_path, item)
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
            
            # Sort directories by name
            dirs.sort(key=lambda x: x["name"].lower())
            
            # Add security headers
            resp = jsonify({
                "directories": dirs,
                "current_path": requested_abs_path,
                "parent_path": parent_of_parent
            })
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["X-Frame-Options"] = "DENY"
            return resp
        else:
            return jsonify({"error": "Not a valid directory"}), 400
    except PermissionError:
        logger.warning(f"Permission denied accessing directory: {parent_path}")
        return jsonify({"error": "Permission denied accessing this directory"}), 403
    except Exception as e:
        logger.error(f"Error browsing directory {parent_path}: {str(e)}")
        return jsonify({"error": f"Error accessing directory: {str(e)}"}), 500

@app.route('/api/browse_files', methods=['GET'])
def api_browse_files() -> ResponseReturnValue:
    """API endpoint to list files in a directory for the host file browser."""
    parent_path = request.args.get("path", "")
    
    # Get secure browser instance
    secure_browser = get_secure_browser()
    
    # If no allowed paths configured, restrict access
    if not secure_browser.allowed_paths:
        logger.warning("File browsing attempted but no 'allowed_paths' configured in [file_browser] section.")
        return jsonify({"error": "File browsing is not configured or no paths are allowed."}), 403
    
    # If no path provided, return configured allowed_paths as root entries
    if not parent_path:
        roots = [{"name": os.path.basename(p) or p, "path": p} for p in secure_browser.allowed_paths]
        resp = jsonify({"directories": roots, "files": [], "current_path": "", "parent_path": ""})
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp
    
    try:
        # Validate the requested path
        if not secure_browser.is_path_allowed(parent_path):
            logger.warning(f"Access denied for path: {parent_path}. Not within allowed bases.")
            return jsonify({"error": "Access to this path is restricted."}), 403
        
        # Normalize path
        requested_abs_path = os.path.abspath(os.path.normpath(parent_path))
        
        # Get the parent path if navigation is allowed
        parent_of_parent = secure_browser.get_safe_parent_path(requested_abs_path)
        
        # List all items in the parent path
        files = []
        dirs = []
        
        if os.path.isdir(requested_abs_path):
            # Get all items in the directory
            items = os.listdir(requested_abs_path)
            
            # Filter items based on security rules
            filtered_items = secure_browser.filter_items(requested_abs_path, items)
            
            for item in filtered_items:
                full_path = os.path.join(requested_abs_path, item)
                
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
                else:
                    # Only include files with certain extensions
                    if item.lower().endswith(('.srt', '.ass', '.vtt')):
                        files.append({"name": item, "path": full_path})
            
            # Sort directories and files by name
            dirs.sort(key=lambda x: x["name"].lower())
            files.sort(key=lambda x: x["name"].lower())
            
            # Add security headers
            resp = jsonify({
                "files": files,
                "directories": dirs,
                "current_path": requested_abs_path,
                "parent_path": parent_of_parent
            })
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["X-Frame-Options"] = "DENY"
            return resp
        else:
            return jsonify({"error": "Not a valid directory"}), 400
    except PermissionError:
        logger.warning(f"Permission denied accessing directory: {parent_path}")
        return jsonify({"error": "Permission denied accessing this directory"}), 403
    except Exception as e:
        logger.error(f"Error browsing files in directory {parent_path}: {str(e)}")
        return jsonify({"error": f"Error accessing directory: {str(e)}"}), 500

@app.route("/api/start-scan", methods=["POST"])
def api_start_scan() -> ResponseReturnValue:
    """API endpoint to start a bulk scan and translation of a directory."""
    data = request.get_json(silent=True) or {}
    root = data.get("path", "").strip()
    if not root or not os.path.isdir(root):
        logger.error(f"Invalid or missing folder path: {root}")
        return jsonify({"ok": False, "error": "Folder not found or path is invalid"}), 400

    logger.info(f"[api_start_scan] Received start-scan request for: {root} (force={data.get('force', False)})")

    force = bool(data.get("force", False))
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
    with progress_lock:
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
        args=(root, config, bulk_translation_progress, logger, force),
        daemon=True
    ).start()

    logger.info("[api_start_scan] Background scan thread started")

    return jsonify({"ok": True})

@app.route("/download-zip")
def download_zip() -> ResponseReturnValue:
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
def live_status() -> ResponseReturnValue:
    """API endpoint to get the current live translation status.
    This now primarily relies on bulk_translation_progress which is updated by all job types.
    """
    # bulk_translation_progress is the global dictionary
    # Ensure a consistent structure for the response
    with progress_lock:
        response_data = {
            "mode": bulk_translation_progress.get("mode", "idle"),
            "status": bulk_translation_progress.get("status", "idle"),
            "message": bulk_translation_progress.get("message", "System is idle."),
            "filename": bulk_translation_progress.get("current_file", ""),
            "percent": bulk_translation_progress.get("percent", 0), # Percent is now directly in bulk_translation_progress
            "job_id": bulk_translation_progress.get("job_id"), # job_id is useful for all modes
            "current_line": bulk_translation_progress.get("current_line", 0),
            "total_lines": bulk_translation_progress.get("total_lines", 0),
            "done_files": bulk_translation_progress.get("done_files", 0),
            "total_files": bulk_translation_progress.get("total_files", 0),
            "current": bulk_translation_progress.get("current", {}),
            "processed_lines": bulk_translation_progress.get("processed_lines", [])
        }
    
    # No mode-specific logic needed here anymore if bulk_translation_progress is always up-to-date.
    # The background threads (process_translation, process_video_transcription, scan_and_translate_directory)
    # are responsible for keeping bulk_translation_progress accurate.

    # Check if log_live_status is set to true in config
    try:
        # Get the config from config_manager instead of using config directly
        current_config = config_manager.get_config()
        log_live_status = current_config.getboolean('logging', 'log_live_status', fallback=False)
        if log_live_status:
            logger.debug(f"Live status API response: {json.dumps(response_data)}")
    except ValueError:
        # Handle case where config value is malformed
        logger.warning("Invalid value for log_live_status in config.ini. Should be 'true' or 'false'.")
    
    return jsonify(response_data)

@app.route('/api/translation_report/<path:filename>')
def api_translation_report(filename) -> ResponseReturnValue:
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
            longest_line_content = "";
            
            for subtitle in subtitles:
                text = subtitle.get('text', '')
                words = len(text.split())
                chars = len(text)
                total_words += words
                total_chars += chars;
                
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
    """Get list of recent translations and transcriptions."""
    recent_files = []
    for job_id, job in translation_jobs.items():
        # Ensure job is completed and has an end_time before processing
        if job.get('status') == 'completed' and job.get('end_time') is not None:
            entry = {
                'id': job_id,
                'name': job.get('original_filename', 'Unknown File'), # Default for name
                'date': datetime.fromtimestamp(job['end_time']).strftime('%Y-%m-%d %H:%M:%S'),
            }
            
            job_type = job.get('type')
            
            if job_type == 'transcription':
                entry['source_language'] = job.get('language', 'N/A') # Video's language
                entry['target_language'] = '(Transcription)'
                recent_files.append(entry)
            elif 'source_language' in job and 'target_language' in job: # Assumed to be a translation
                entry['source_language'] = job['source_language']
                entry['target_language'] = job['target_language']
                recent_files.append(entry)
            else:
                # This job is completed but doesn't fit the expected structures.
                logger.warning(
                    f"Job {job_id} (type: {job_type}) is completed but lacks expected language fields. Skipping from recent list."
                )
                # No append, so it's skipped

    # Sort by date, newest first
    recent_files.sort(key=lambda x: x['date'], reverse=True)
    
    # Return all recent files, no limit
    return recent_files

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
    with jobs_lock:
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
        with progress_lock:
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
        
        # Save progress state to file
        save_progress_state()
        
        # Update job status in translation_jobs as well
        job['status'] = 'processing'
        job['message'] = 'Initializing...'
        
        # Initialize subtitle processor
        subtitle_processor = SubtitleProcessor(logger) # Ensure subtitle_processor is initialized here
        
        # Get config
        config = config_manager.get_config()
        
        # Update languages in config for this specific job
        config.set('general', 'source_language', job['source_language'])
        config.set('general', 'target_language', job['target_language'])
        
        # --- MODIFICATION START ---
        # Construct the target path for the 'subs' folder (app.config['UPLOAD_FOLDER'])
        original_filename_base, original_filename_ext = os.path.splitext(job['original_filename'])
        
        # Construct the new filename by replacing language code or adding target language code
        # Try to identify and replace the language code in the original filename
        src_lang = job['source_language']
        tgt_lang = job['target_language']
        
        # Try to replace language code in filename if it exists
        out_base = original_filename_base
        replaced = False
        patterns = [
            f'.{src_lang}.', f'.{src_lang}-', f'.{src_lang}_',
            f'{src_lang}.', f'-{src_lang}.', f'_{src_lang}.'
        ]
        import re
        for pat in patterns:
            if pat.lower() in original_filename_base.lower():
                newpat = pat.replace(src_lang, tgt_lang)
                out_base = re.sub(re.escape(pat), newpat, original_filename_base, flags=re.IGNORECASE)
                replaced = True
                break
                
        # If no language code pattern was found, add the target language code at the end
        if not replaced:
            out_base = f"{original_filename_base}.{tgt_lang}"
            
        # Preserve original extension if it's .ass or .vtt, otherwise default to .srt
        output_extension = original_filename_ext if original_filename_ext.lower() in ['.ass', '.vtt'] else '.srt'
        final_translated_filename = secure_filename(f"{out_base}{output_extension}")

        # Ensure UPLOAD_FOLDER (app.config['UPLOAD_FOLDER']) is used for the output path
        final_output_path = os.path.join(app.config['UPLOAD_FOLDER'], final_translated_filename)
        logger.info(f"Target save path for single translation will be: {final_output_path}")
        # --- MODIFICATION END ---
        
        # Call translate_srt, passing the global progress dictionary
        success = subtitle_processor.translate_srt(
            job['source_path'],      # Source is still the cached file (cache_path)
            final_output_path,       # <<<< MODIFIED: Save to 'subs' folder
            config, 
            progress_dict=progress_dict
        )
        
        if success:
            # Update job status upon successful completion
            job['status'] = 'completed'
            # Ensure job['target_path'] points to the new location in 'subs'
            # progress_dict["output_path"] should be set by translate_srt to final_output_path
            job['target_path'] = progress_dict.get("output_path", final_output_path) 
            job['progress'] = 100 # Mark as 100% in job-specific dict
            job['message'] = 'Translation completed'
            job['end_time'] = time.time()
            logger.info(f"Translation job {job_id} completed: {job['original_filename']}. Saved to: {job['target_path']}") # Log the correct save path
            
            # Update global progress status to completed
            progress_dict["status"] = "completed"
            progress_dict["message"] = f"Translation completed for {job['original_filename']}"
            # Ensure the global progress also reflects the correct final path in 'subs'
            progress_dict["output_path"] = job['target_path']
            
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
        # Optionally clean up the original uploaded file from CACHE_DIR
        # after successful translation and saving to UPLOAD_FOLDER.
        # For example:
        # if job.get('status') == 'completed' and os.path.exists(job['source_path']):
        #     try:
        #         os.remove(job['source_path'])
        #         logger.info(f"Cleaned up cached source file: {job['source_path']}")
        #     except Exception as e_clean:
        #         logger.warning(f"Failed to clean up cached source file {job['source_path']}: {e_clean}")
        pass # Keep final status until next job starts

def clear_global_progress(progress_dict):
    """Resets the global progress dict to idle state."""
    with progress_lock:
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

def scan_and_translate_directory(root_dir, config, progress, logger, force=False):
    logger.info(f"[scan_and_translate_directory] Thread started for root: {root_dir}")
    """Scan a directory for subtitle files and translate them in bulk."""
    try:
        with progress_lock:
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
                if file.lower().endswith(('.srt', '.ass', '.vtt')):
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
            with progress_lock:
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
                        has_src_lang = (src_lang_code is not None and src_lang_code in file_basename.lower())
                        has_src_lang_3letter = (src_lang_code_3letter is not None and src_lang_code_3letter in file_basename.lower())
                        
                        if has_src_lang or has_src_lang_3letter:
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
            if not force and f".{tgt_lang_code}." in file_name.lower():
                logger.debug(f"Skipping file with target language code in filename: {file_name}")
                skip_these_files.append(file_path)
                continue
                
            # Try to extract language code from filename
            detected_lang = None
            matching_pattern = None
            
            allowed_lang_codes = {src_lang_code, tgt_lang_code, 'en', 'eng', 'da', 'dan', 'es', 'spa', 'de', 'deu', 'ger', 'fr', 'fre', 'hi'}
            
            for pattern in lang_patterns:
                match = re.search(pattern, file_name.lower())
                if match:
                    candidate = match.group(1)
                    if candidate not in allowed_lang_codes:
                        # Ignore spurious 3-letter words like "the", "jet", etc.
                        logger.debug(f"Ignoring false language candidate '{candidate}' in {file_name}")
                        continue
                    detected_lang = candidate
                    matching_pattern = pattern
                    logger.debug(f"Detected language '{detected_lang}' in file: {file_name} using pattern {pattern}")
                    break
            
            # If we couldn't detect a language, track it but continue to next file
            if not detected_lang:
                # ---------------- Fallback token scan ----------------
                tokens = file_name.lower().split('.')
                if len(tokens) > 1:
                    # Drop extension (srt/ass)
                    tokens = tokens[:-1]
                    for tok in reversed(tokens):  # check nearest to extension first
                        if tok in allowed_lang_codes:
                            detected_lang = tok
                            logger.debug(
                                f"[fallback] Detected language '{detected_lang}' in file: {file_name} using token scan"
                            )
                            break

            # If still nothing, record unmatched and move on
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
            if not force and detected_lang == tgt_lang_code:
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
                base_name = re.sub(r'\.(' + re.escape(detected_lang) + r')\.hi\.', '.*.hi.', base_name)
                base_name = re.sub(r'\.(' + re.escape(detected_lang) + r')-hi\.', '.*-hi.', base_name)
            else:
            # Standard replacements for other patterns
                base_name = re.sub(r'\.(' + re.escape(detected_lang) + r')\.', '.*.', base_name)
                base_name = re.sub(r'\.(' + re.escape(detected_lang) + r')-', '.*-', base_name)
                base_name = re.sub(r'\.(' + re.escape(detected_lang) + r')_', '.*_', base_name)
                base_name = re.sub(r'_(' + re.escape(detected_lang) + r')_', '_*_', base_name)
                base_name = re.sub(r'-(' + re.escape(detected_lang) + r')-', '-*-', base_name)
                # For languages at the start of filename
                base_name = re.sub(r'^(' + re.escape(detected_lang) + r')\.', '*.', base_name)
                base_name = re.sub(r'^(' + re.escape(detected_lang) + r')-', '*-', base_name)
            
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
                if force:
                    logger.info(f"Adding {os.path.basename(lang_files[src_lang_code])} to translation queue despite existing target (force)")
                    srt_files.append(lang_files[src_lang_code])
                else:
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
            with progress_lock:
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

                # If force is enabled and the target file already exists, generate a unique alternative name
                if force:
                    alt_base, alt_ext = os.path.splitext(translated_filename)
                    counter = 1
                    alt_filename = translated_filename
                    alt_archive_path = os.path.join(app.config['UPLOAD_FOLDER'], alt_filename)
                    while os.path.exists(alt_archive_path):
                        alt_filename = f"{alt_base}_alt{counter}{alt_ext}"
                        alt_archive_path = os.path.join(app.config['UPLOAD_FOLDER'], alt_filename)
                        counter += 1

                    if alt_filename != translated_filename:
                        logger.info(f"[force] Existing target file detected – saving as alternate '{alt_filename}' instead of overwriting")
                        translated_filename = alt_filename

                output_path = os.path.join(temp_dir, translated_filename)
                archive_path = os.path.join(app.config['UPLOAD_FOLDER'], translated_filename)
                
                # Check if the output file already exists in the archive
                if (not force) and os.path.exists(archive_path):
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
                    # Get the original source directory (not the temporary working directory)
                    original_dir = os.path.dirname(srt_file)
                    alongside_path = os.path.join(original_dir, translated_filename)
                    
                    # Add detailed logging to debug the path construction
                    logger.info(f"Attempting to save alongside original:")
                    logger.info(f"  - Original file: {srt_file}")
                    logger.info(f"  - Original directory: {original_dir}")
                    logger.info(f"  - Translated filename: {translated_filename}")
                    logger.info(f"  - Alongside path: {alongside_path}")
                    
                    try:
                        # Check if the target directory exists and is writable
                        if not os.path.exists(original_dir):
                            logger.error(f"Original directory does not exist: {original_dir}")
                            raise Exception(f"Directory does not exist: {original_dir}")
                        
                        if not os.access(original_dir, os.W_OK):
                            logger.error(f"No write permission to directory: {original_dir}")
                            raise Exception(f"No write permission to directory: {original_dir}")
                        
                        # Copy the translated file to the original directory
                        shutil.copy2(archive_path, alongside_path)
                        logger.info(f"Successfully saved translation alongside original: {alongside_path}")
                        
                        # Verify the file was actually created
                        if os.path.exists(alongside_path):
                            file_size = os.path.getsize(alongside_path)
                            logger.info(f"Verified file creation: {alongside_path} ({file_size} bytes)")
                        else:
                            logger.error(f"File was not created despite no exception: {alongside_path}")
                            
                    except Exception as e:
                        logger.error(f"Failed to save alongside original: {e}")
                        logger.error(f"Exception type: {type(e).__name__}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                    
                    translated_files.append(output_path)
                    with progress_lock:
                        progress["done_files"] += 1
                        # Save progress state after completing each file
                        save_progress_state()
                else:
                    logger.error(f"Failed to translate {file_name}")
                    with progress_lock:
                        progress["message"] = f"Error translating {file_name}"
                        save_progress_state()
                
            except Exception as e:
                error_msg = f"Error translating {file_name}: {str(e)}"
                logger.error(error_msg)
                with progress_lock:
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
            with progress_lock:
                progress["status"] = "completed"
                progress["message"] = f"Translated {progress['done_files']} subtitle files. Skipped {len(skipped_files)} files that already had {tgt_lang} versions."
                progress["zip_path"] = zip_path
                # Save final progress state to file
                save_progress_state()
        else:
            with progress_lock:
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
def api_special_meanings() -> ResponseReturnValue:
    """API endpoint to get special word meanings from the file."""
    try:
        # Initialize translation service to load meanings
        config = config_manager.get_config()
        translation_service = TranslationService(app_config, logger)
        
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
def api_update_special_meanings() -> ResponseReturnValue:
    """API endpoint to update special word meanings in the file."""
    try:
        if request.json is None:
            return jsonify({"error": "No JSON data provided"}), 400
            
        meanings = request.json.get('meanings', [])
        
        # Initialize translation service
        config = config_manager.get_config()
        translation_service = TranslationService(app_config, logger)
        
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
def api_browse_videos() -> ResponseReturnValue:
    """API endpoint to list video files in a directory for the host file browser."""
    parent_path = request.args.get("path", "")
    
    # Get secure browser instance
    secure_browser = get_secure_browser()
    
    # If no allowed paths configured, restrict access
    if not secure_browser.allowed_paths:
        logger.warning("Video file browsing attempted but no 'allowed_paths' configured in [file_browser] section.")
        return jsonify({"error": "File browsing is not configured or no paths are allowed."}), 403
    
    # If no path provided, return configured allowed_paths as root entries
    if not parent_path:
        roots = [{"name": os.path.basename(p) or p, "path": p} for p in secure_browser.allowed_paths]
        resp = jsonify({"directories": roots, "files": [], "current_path": "", "parent_path": ""})
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp
    
    try:
        # Validate the requested path
        if not secure_browser.is_path_allowed(parent_path):
            logger.warning(f"Access denied for path: {parent_path}. Not within allowed bases.")
            return jsonify({"error": "Access to this path is restricted."}), 403
        
        # Normalize path
        requested_abs_path = os.path.abspath(os.path.normpath(parent_path))
        
        # Get the parent path if navigation is allowed
        parent_of_parent = secure_browser.get_safe_parent_path(requested_abs_path)
        
        # List all items in the parent path
        files = []
        dirs = []
        
        if os.path.isdir(requested_abs_path):
            # Get all items in the directory
            items = os.listdir(requested_abs_path)
            
            # Filter items based on security rules
            filtered_items = secure_browser.filter_items(requested_abs_path, items)
            
            # Video file extensions
            video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', 
                              '.ts', '.mts', '.m2ts', '.vob', '.3gp', '.ogv', '.divx', '.xvid')
            
            for item in filtered_items:
                full_path = os.path.join(requested_abs_path, item)
                
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
                else:
                    # Only include video files
                    if item.lower().endswith(video_extensions):
                        files.append({"name": item, "path": full_path})
            
            # Sort directories and files by name
            dirs.sort(key=lambda x: x["name"].lower())
            files.sort(key=lambda x: x["name"].lower())
            
            # Add security headers
            resp = jsonify({
                "files": files,
                "directories": dirs,
                "current_path": requested_abs_path,
                "parent_path": parent_of_parent
            })
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["X-Frame-Options"] = "DENY"
            return resp
        else:
            return jsonify({"error": "Not a valid directory"}), 400
    except PermissionError:
        logger.warning(f"Permission denied accessing directory: {parent_path}")
        return jsonify({"error": "Permission denied accessing this directory"}), 403
    except Exception as e:
        logger.error(f"Error browsing files in directory {parent_path}: {str(e)}")
        return jsonify({"error": f"Error accessing directory: {str(e)}"}), 500

@app.route('/api/transcribe', methods=['POST'])
def api_transcribe() -> ResponseReturnValue:
    """API endpoint to transcribe a video file to SRT format using faster-whisper."""
    try:
        # Check if a host file path was provided
       
        video_file_path = request.form.get('video_file_path', '')
        
        if not video_file_path:
            return jsonify({"error": "No video file path provided"}), 400
        
        # Get a secure browser instance for path validation
        secure_browser = get_secure_browser()
        
        # Normalize the path before validation
        normalized_path = os.path.abspath(os.path.normpath(video_file_path))
        
        # Validate the path is allowed using our secure browser
        if not secure_browser.is_path_allowed(normalized_path):
            logger.warning(f"Access denied for video file: {normalized_path}. Not within allowed bases or matches denied pattern.")
            return jsonify({"error": "Access to this file is restricted."}), 403
        
        # After validation, use the normalized path
        requested_abs_path = normalized_path
            
        if not os.path.isfile(requested_abs_path):
            return jsonify({"error": "Invalid file path or file does not exist"}), 400
            
        # Check if it's a video file
        if not requested_abs_path.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v')):
            return jsonify({"error": "Only video files are supported"}), 400
            
        # Get the filename without path
        filename = os.path.basename(requested_abs_path)
        
        # Create a job ID based on the filename and timestamp
        timestamp = int(time.time())
        job_id = f"whisper_{timestamp}_{filename}"
        
        # Get language if provided (optional)
        language = request.form.get('language', None)
        
        # Create/update the job record
        translation_jobs[job_id] = {
            'status': 'queued',
            'source_path': requested_abs_path,
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
def api_check_whisper_server() -> ResponseReturnValue:
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

@app.route('/api/job_status/<job_id>', methods=['GET'])
def get_job_status(job_id) -> ResponseReturnValue:
    """API endpoint for checking translation job status."""
    # Check bulk_translation_progress first if it's for the requested job_id and is a transcription
    with progress_lock:
        is_active_transcription = (bulk_translation_progress.get('job_id') == job_id and 
                                   bulk_translation_progress.get('mode') == 'transcription' and
                                   bulk_translation_progress.get('status') in ['processing', 'queued'])
        
        if is_active_transcription:
            # If the job is active in bulk_translation_progress, report from there
            # as it's the most up-to-date for transcriptions.
            logger.debug(f"Job {job_id} is an active transcription. Reporting from bulk_translation_progress.")
            return jsonify({
                'success': True,
                'status': bulk_translation_progress['status'],
                'progress': bulk_translation_progress.get('percent', 0),
                'message': bulk_translation_progress.get('message', 'Processing transcription...')
            })

    # Fallback to checking translation_jobs or if bulk_translation_progress is not for this active transcription
    with jobs_lock:
        if job_id not in translation_jobs:
            # This case handles if job is not in translation_jobs but was caught by the above block
            # or if it's genuinely not found anywhere.
            logger.warning(f"Job {job_id} not found in translation_jobs dictionary.")
            return jsonify({'success': False, 'message': 'Job not found'})

        job = translation_jobs[job_id]
    
    # If it's a transcription job, and the bulk progress is for THIS job,
    # we might need to cross-reference with bulk_translation_progress for the latest state
    with progress_lock, jobs_lock:
        is_transcription = (job.get('type') == 'transcription' and 
                           bulk_translation_progress.get('job_id') == job_id and 
                           bulk_translation_progress.get('mode') == 'transcription')
        
        if is_transcription:
            # This will mostly catch 'completed' or 'failed' states from bulk if job object is lagging
            logger.debug(f"Job {job_id} is a transcription. Cross-referencing with bulk_translation_progress for final state if needed.")
            return jsonify({
                'success': True,
                'status': bulk_translation_progress.get('status', job['status']), # Prefer bulk if available
                'progress': bulk_translation_progress.get('percent', job['progress']), # Prefer bulk if available
                'message': bulk_translation_progress.get('message', job['message']) # Prefer bulk if available
            })

    # Default return for non-transcription jobs, or if no specific active transcription logic applied.
    logger.debug(f"Job {job_id} (type: {job.get('type')}) reporting from translation_jobs.")
    return jsonify({
        'success': True,
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message']
    })

def allowed_file(filename):
    """Check if file has an allowed extension."""
    return '.' in filename and \
           filename.lower().endswith(('.srt', '.ass', '.vtt'))

# Initialize global objects - use the already initialized config_manager from above
subtitle_processor = SubtitleProcessor()

# Global storage for translation progress
bulk_translation_progress = {}
translation_jobs = {}

# Locks for thread safety
progress_lock = threading.RLock()
jobs_lock = threading.Lock()

# Create secure file browser instance
def get_secure_browser():
    """Initialize and return a SecureFileBrowser instance with config settings."""
    config = config_manager.get_config()
    
    # Get allowed paths
    allowed_paths_str = config.get('file_browser', 'allowed_paths', fallback='')
    allowed_paths = [p.strip() for p in allowed_paths_str.split(',') if p.strip()]
    
    # Get denied patterns
    denied_patterns_str = config.get('file_browser', 'denied_patterns', fallback='')
    denied_patterns = [p.strip() for p in denied_patterns_str.split(',') if p.strip()]
    
    # Get security settings
    enable_parent = config.getboolean('file_browser', 'enable_parent_navigation', fallback=True)
    max_depth = config.getint('file_browser', 'max_depth', fallback=10)
    hide_dot_files = config.getboolean('file_browser', 'hide_dot_files', fallback=True)
    restrict_to_media = config.getboolean('file_browser', 'restrict_to_media_dirs', fallback=False)
    
    return SecureFileBrowser(
        allowed_paths=allowed_paths,
        denied_patterns=denied_patterns,
        enable_parent_navigation=enable_parent,
        max_depth=max_depth,
        hide_dot_files=hide_dot_files,
        restrict_to_media_dirs=restrict_to_media
    )

# Set up security middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses."""
    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Enable XSS protection in browsers
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Restrict access to the current domain
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    return response

@app.errorhandler(404)
def handle_404(error) -> ResponseReturnValue:
    logger.error(f"404 error: {error}")
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def handle_500(error) -> ResponseReturnValue:
    logger.error(f"500 error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Define the config file path
    config_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
    
    # Create default config if it doesn't exist
    if not os.path.exists(config_file_path):
        # Initialize a ConfigManager instance specifically for creating the default config
        temp_config_manager = ConfigManager(config_file_path)
        temp_config_manager.create_default_config()
    
    # Get host and port from config using the already initialized global config_manager
    config_data = config_manager.get_config() # Call get_config() and assign to config_data
    host = config_data.get('general', 'host', fallback='127.0.0.1')
    port = config_data.getint('general', 'port', fallback=5089)
    debug = config_data.getboolean('webui', 'debug', fallback=False)
    
    # Start the app with a more accurate welcome message
    print("==========================================")
    print("Starting Subtitle Translator...")
    print("==========================================")
    print(f"If your browser doesn't open automatically, navigate to http://{host}:{port}")
    print("Press Ctrl+C to stop the application.")
    print("==========================================")
    
    # Start the app
    app.run(host=host, port=port, debug=debug)