#!/usr/bin/env python3
"""
Simple test script to verify the browser functionality.
"""
from flask import Flask, render_template, request, jsonify
import os

app = Flask(__name__, 
           static_folder='static', 
           template_folder='templates')

@app.route('/')
def index():
    """Simple test page."""
    languages = [("en", "English"), ("es", "Spanish")]
    return render_template('index.html', 
                          languages=languages, 
                          default_source="en", 
                          default_target="es",
                          debug=True)

@app.route('/api/browse_dirs', methods=['GET'])
def browse_dirs():
    """Simple directory browser."""
    path = request.args.get('path', '')
    print(f"Browsing directory: {path}")
    
    if not path:
        path = '/'
    
    try:
        dirs = []
        parent = os.path.dirname(path) if path != '/' else ''
        
        if os.path.isdir(path):
            for item in os.listdir(path):
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    dirs.append({"name": item, "path": full_path})
        
        return jsonify({
            "directories": dirs,
            "current_path": path,
            "parent_path": parent
        })
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("Starting test server on port 5001...")
    app.run(host='0.0.0.0', port=5001, debug=True)
