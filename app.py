import os
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

# Initialize Flask app
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'subs')
app.secret_key = os.urandom(24)  # Add secret key for flash messages

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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
def translate_subtitle():
    # Debug information to help identify the issue
    print("Request received: ", request.files)
    print("Form data: ", request.form)
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file part"})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No file selected"})
    
    # Get languages from form
    source_language = request.form.get('source_language', config.get('general', 'source_language', fallback='en'))
    target_language = request.form.get('target_language', config.get('general', 'target_language', fallback='da'))
    
    # Generate a unique job ID
    job_id = str(uuid.uuid4())
    
    # Process the uploaded file
    try:
        # Rest of your code...
        # Secure the filename and save the file
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base, ext = os.path.splitext(filename)
        save_filename = f"{base}_{source_language}_to_{target_language}_{timestamp}{ext}"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], save_filename)
        file.save(save_path)
        
        # Create translation job
        translation_jobs[job_id] = {
            'id': job_id,
            'filename': save_filename,
            'original_filename': filename,
            'source_path': save_path,
            'target_path': None,
            'source_language': source_language,
            'target_language': target_language,
            'status': 'queued',
            'progress': 0,
            'message': 'Translation queued',
            'start_time': time.time(),
            'end_time': None
        }
        
        # Start translation in background thread
        from threading import Thread
        thread = Thread(target=process_translation, args=(job_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'job_id': job_id,
            'message': 'Translation started'
        })
    except Exception as e:
        logger.error(f"Error processing translation: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})

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
        if "status" in progress:
            response_data["status"] = progress["status"]
        
        # Add file and progress information
        if "current_file" in progress:
            response_data["filename"] = progress["current_file"]
        if "current_line" in progress:
            response_data["current_line"] = progress["current_line"]
        if "total_lines" in progress:
            response_data["total_lines"] = progress["total_lines"]
            
        if "current" in progress and isinstance(progress["current"], dict):
            current = progress["current"]
            response_data.update({
                "line_number": current.get("line_number", 0),
                "original": current.get("original", ""),
                "translations": current.get("suggestions", {}),
                "first_pass": current.get("first_pass", ""),
                "critic": current.get("standard_critic", ""),
                "final": current.get("final", "")
            })
            
            # Add timing information if available
            if "timing" in current:
                response_data["timing"] = current.get("timing", {})
            
            # If critic has changed the translation, indicate this in the response
            if "standard_critic" in current and "first_pass" in current:
                response_data["critic_changed"] = current["standard_critic"] != current["first_pass"]
                
            # Add detail about what the critic did
            if "critic_action" in current:
                response_data["critic_action"] = current.get("critic_action", "")
        
        # Add history of processed lines if available
        if "processed_lines" in progress:
            response_data["processed_lines"] = progress["processed_lines"]
    
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

def process_translation(job_id):
    """Process a translation job, updating the global progress dictionary."""
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
        
        # Get language settings
        src_lang = config.get("general", "source_language", fallback="en")
        tgt_lang = config.get("general", "target_language", fallback="da")
        
        # Scan directory for SRT files
        srt_files = []
        for root, _, files in os.walk(root_dir):
            for file in files:
                if file.lower().endswith('.srt'):
                    srt_files.append(os.path.join(root, file))
        
        if not srt_files:
            progress["status"] = "completed"
            progress["message"] = f"No subtitle files found in {root_dir}"
            return
        
        # Initialize translation components
        subtitle_processor = SubtitleProcessor(logger)
        translation_service = TranslationService(config, logger)
        
        # Update progress
        progress["total_files"] = len(srt_files)
        progress["status"] = "translating"
        
        # Create a temporary directory for the translated files
        temp_dir = tempfile.mkdtemp(prefix="srt_translate_")
        translated_files = []
        
        # Translate each file
        for i, srt_file in enumerate(srt_files):
            file_name = os.path.basename(srt_file)
            progress["current_file"] = file_name
            progress["message"] = f"Translating {file_name} ({i+1}/{len(srt_files)})"
            
            try:
                # Parse the subtitle file
                subtitles = subtitle_processor.parse_file(srt_file)
                
                # Generate translated filename
                base, ext = os.path.splitext(file_name)
                
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
                
                # Translate all subtitles
                translated_subtitles = []
                for j, subtitle in enumerate(subtitles):
                    # Update progress details
                    if j % 10 == 0:  # Update progress every 10 subtitles to reduce message frequency
                        progress["message"] = f"Translating {file_name}: {j+1}/{len(subtitles)} lines"
                    
                    # Translate text
                    translated_text = translation_service.translate(
                        subtitle['text'], 
                        src_lang, 
                        tgt_lang
                    )
                    
                    # Add translated subtitle
                    translated_subtitle = subtitle.copy()
                    translated_subtitle['text'] = translated_text
                    translated_subtitles.append(translated_subtitle)
                
                # Write translated file
                subtitle_processor.write_file(output_path, translated_subtitles)
                
                # Also save a copy to the subs folder for archive
                archive_path = os.path.join(app.config['UPLOAD_FOLDER'], translated_filename)
                subtitle_processor.write_file(archive_path, translated_subtitles)
                
                translated_files.append(output_path)
                progress["done_files"] += 1
                
            except Exception as e:
                error_msg = f"Error translating {file_name}: {str(e)}"
                logger.error(error_msg)
                progress["message"] = error_msg
                # Continue with next file
        
        # Create ZIP file with all translated subtitles
        if translated_files:
            zip_path = os.path.join(tempfile.gettempdir(), f"translated_subtitles_{int(time.time())}.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in translated_files:
                    zipf.write(file, os.path.basename(file))
            
            # Update progress
            progress["status"] = "completed"
            progress["message"] = f"Translated {progress['done_files']} subtitle files"
            progress["zip_path"] = zip_path
        else:
            progress["status"] = "completed"
            progress["message"] = "No files were successfully translated"
        
    except Exception as e:
        error_msg = f"Error during bulk translation: {str(e)}"
        logger.error(error_msg)
        progress["status"] = "failed"
        progress["message"] = error_msg

if __name__ == '__main__':
    # Create default config if it doesn't exist
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')):
        config_manager.create_default_config()
    
    # Get host and port from config
    config = config_manager.get_config()
    host = config.get('webui', 'host', fallback='127.0.0.1')
    port = config.getint('webui', 'port', fallback=5089)
    debug = config.getboolean('webui', 'debug', fallback=False)
    
    # Start the app
    app.run(host=host, port=port, debug=debug)