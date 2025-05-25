#!/usr/bin/env python
"""
Debug utility to help diagnose and fix issues with the directory browser.
This is a simplified version of app.py that focuses on just the directory browsing functionality.
"""

import os
import sys
import json
from flask import Flask, request, jsonify, render_template, send_from_directory

# Setup Flask app
app = Flask(__name__, static_folder='static', template_folder='templates')

@app.route('/')
def index():
    """Render the main page but with debug info."""
    languages = [
        ("auto", "Auto Detect"),
        ("en", "English"),
        ("es", "Spanish"),
        ("fr", "French"),
        ("de", "German"),
        ("it", "Italian"),
        ("ja", "Japanese"),
        ("ko", "Korean"),
        ("zh-cn", "Chinese (Simplified)"),
        ("zh-tw", "Chinese (Traditional)"),
        ("ru", "Russian"),
        ("pt", "Portuguese"),
        ("ar", "Arabic")
    ]
    
    return render_template('index.html', 
                          languages=languages, 
                          default_source="auto", 
                          default_target="en",
                          debug=True)

@app.route('/api/browse_dirs', methods=['GET'])
def api_browse_dirs():
    """API endpoint to list directories for the file browser with enhanced debug."""
    parent_path = request.args.get("path", "")
    
    print(f"DEBUG: Browse dirs request received for path: '{parent_path}'")
    
    # Default to system root directories if no path provided
    if not parent_path:
        if os.name == "nt":  # Windows
            import string
            # Get all drives
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            print(f"DEBUG: Listing Windows drives: {drives}")
            return jsonify({"directories": [{"name": d, "path": d} for d in drives], 
                           "current_path": "", "parent_path": ""})
        else:  # Unix-like
            parent_path = "/"
            print(f"DEBUG: Setting default path to root directory: {parent_path}")
    
    try:
        # Security check: normalize path to prevent directory traversal
        parent_path = os.path.normpath(parent_path)
        print(f"DEBUG: Normalized path: {parent_path}")
        
        # Get the parent of the current directory for "up one level" functionality
        parent_of_parent = os.path.dirname(parent_path) if parent_path != "/" else ""
        
        # List all directories in the parent path
        dirs = []
        if os.path.isdir(parent_path):
            print(f"DEBUG: Listing directories in {parent_path}")
            for item in os.listdir(parent_path):
                full_path = os.path.join(parent_path, item)
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
            
            # Sort directories by name
            dirs.sort(key=lambda x: x["name"].lower())
            
            print(f"DEBUG: Found {len(dirs)} directories")
            return jsonify({
                "directories": dirs,
                "current_path": parent_path,
                "parent_path": parent_of_parent
            })
        else:
            print(f"DEBUG: Not a valid directory: {parent_path}")
            return jsonify({"error": "Not a valid directory"}), 400
            
    except Exception as e:
        print(f"DEBUG ERROR: Exception in browse_dirs: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/debug/inspect_browser')
def inspect_browser():
    """Special debug endpoint to check browser visibility."""
    return """
    <html>
    <head><title>Debug Browser Visibility</title></head>
    <body>
        <h1>Browser Visibility Debugger</h1>
        <script>
            // Fetch page HTML
            fetch('/')
            .then(response => response.text())
            .then(html => {
                // Create a DOM parser and parse the HTML
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                
                // Extract the browser element
                const browser = doc.getElementById('inline-file-browser');
                const browseBtn = doc.getElementById('browse-btn');
                
                // Display info
                document.body.innerHTML += `
                    <h2>Inline File Browser Element</h2>
                    <pre>${browser ? browser.outerHTML.replace(/</g, '&lt;').replace(/>/g, '&gt;') : 'Not found'}</pre>
                    
                    <h2>Browse Button Element</h2>
                    <pre>${browseBtn ? browseBtn.outerHTML.replace(/</g, '&lt;').replace(/>/g, '&gt;') : 'Not found'}</pre>
                `;
            });
        </script>
    </body>
    </html>
    """

if __name__ == '__main__':
    print("Starting debug server...")
    app.run(host='0.0.0.0', port=5000, debug=True)
