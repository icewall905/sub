import os
import sys
import logging
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from werkzeug.utils import secure_filename
import configparser
import json
import time
from datetime import datetime
import uuid

# Add the project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import our modules
from py.config_manager import ConfigManager
from py.subtitle_processor import SubtitleProcessor
from py.translation_service import TranslationService
from py.logger import setup_logger

# Initialize Flask app
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'subs')

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Setup logging
logger = setup_logger('app', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translator.log'))

# Initialize the configuration manager
config_manager = ConfigManager(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini'))

# Translation jobs storage
translation_jobs = {}

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
    default_source = config.get('general', 'default_source_language', fallback='en')
    default_target = config.get('general', 'default_target_language', fallback='es')
    
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
    """API endpoint for starting a translation job."""
    if 'subtitle-file' not in request.files:
        return jsonify({'success': False, 'message': 'No file part'})
    
    file = request.files['subtitle-file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'})
    
    # Get source and target languages
    source_lang = request.form.get('source-language', 'en')
    target_lang = request.form.get('target-language', 'es')
    
    # Generate a unique job ID
    job_id = str(uuid.uuid4())
    
    # Secure the filename and save the file
    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base, ext = os.path.splitext(filename)
    save_filename = f"{base}_{source_lang}_to_{target_lang}_{timestamp}{ext}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], save_filename)
    file.save(save_path)
    
    # Create translation job
    translation_jobs[job_id] = {
        'id': job_id,
        'filename': save_filename,
        'original_filename': filename,
        'source_path': save_path,
        'target_path': None,
        'source_language': source_lang,
        'target_language': target_lang,
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
    """Process a translation job."""
    job = translation_jobs[job_id]
    logger.info(f"Starting translation job {job_id}: {job['original_filename']}")
    
    try:
        # Update job status
        job['status'] = 'processing'
        job['message'] = 'Reading subtitle file...'
        
        # Initialize subtitle processor
        subtitle_processor = SubtitleProcessor(logger)
        
        # Parse subtitle file
        job['message'] = 'Parsing subtitle file...'
        job['progress'] = 10
        subtitles = subtitle_processor.parse_file(job['source_path'])
        
        # Initialize translation service
        config = config_manager.get_config()
        translation_service = TranslationService(config, logger)
        
        # Translate subtitles
        job['message'] = 'Translating subtitles...'
        translated_subtitles = []
        total_subtitles = len(subtitles)
        
        for i, subtitle in enumerate(subtitles):
            # Update progress (from 20% to 90%)
            progress_percent = int(20 + (i / total_subtitles) * 70)
            job['progress'] = progress_percent
            job['message'] = f'Translating subtitle {i+1}/{total_subtitles}...'
            
            # Translate text
            translated_text = translation_service.translate(
                subtitle['text'], 
                job['source_language'], 
                job['target_language']
            )
            
            # Add translated subtitle
            translated_subtitle = subtitle.copy()
            translated_subtitle['text'] = translated_text
            translated_subtitles.append(translated_subtitle)
        
        # Generate output filename
        base, ext = os.path.splitext(job['filename'])
        output_filename = f"{base}_translated{ext}"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        
        # Write translated subtitles
        job['message'] = 'Writing translated subtitles...'
        job['progress'] = 95
        subtitle_processor.write_file(output_path, translated_subtitles)
        
        # Update job status
        job['status'] = 'completed'
        job['target_path'] = output_path
        job['progress'] = 100
        job['message'] = 'Translation completed'
        job['end_time'] = time.time()
        
        logger.info(f"Translation job {job_id} completed: {job['original_filename']}")
        
    except Exception as e:
        error_message = f"Error in translation job: {str(e)}"
        logger.error(error_message)
        job['status'] = 'failed'
        job['message'] = error_message
        job['progress'] = 0
        job['end_time'] = time.time()

if __name__ == '__main__':
    # Create default config if it doesn't exist
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')):
        config_manager.create_default_config()
    
    # Get host and port from config
    config = config_manager.get_config()
    host = config.get('webui', 'host', fallback='127.0.0.1')
    port = config.getint('webui', 'port', fallback=5000)
    debug = config.getboolean('webui', 'debug', fallback=False)
    
    # Start the app
    app.run(host=host, port=port, debug=debug)