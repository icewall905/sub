#!/usr/bin/env python3
"""
A single-file "installer" + web UI for translating SRT subtitles, now with an optional Agent Critic pass.

** Key Changes / Fixes in This Version **
- (Existing changes omitted for brevity.)
- Added Agent Critic feature.
- Added a live console box on the home page to watch the logs as soon as you click "Upload & Translate."
"""

import os
import sys
import subprocess
import shutil
import time
import atexit
import tempfile
import signal
import platform
import logging
import threading
import configparser
import webbrowser
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from collections import deque
import argparse

def call_translation_service_with_retry(translate_func, *args, max_retries=3, base_delay=2, log_func=print, service_name=None, **kwargs):
    """
    Generic retry wrapper for translation service calls with exponential backoff.
    
    Args:
        translate_func: The translation function to call
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds (will be multiplied exponentially)
        log_func: Function to use for logging messages (defaults to print)
        service_name: Optional name of the service for better logging
        *args, **kwargs: Arguments to pass to the translation function
    
    Returns:
        The translation result or empty string if all retries fail
    """
    import random
    import time
    
    service_label = f"[{service_name}]" if service_name else ""
    
    for attempt in range(max_retries + 1):
        try:
            result = translate_func(*args, **kwargs)
            if result:  # If we got a valid result, return it
                return result
            
            # If the result is empty but no exception occurred, we might still want to retry
            if attempt < max_retries:
                log_func(f"[WARNING] {service_label} Empty result from translation service. Retrying ({attempt + 1}/{max_retries})...")
            else:
                log_func(f"[WARNING] {service_label} Empty result from translation service after {max_retries} retries.")
                return ""
                
        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                if attempt < max_retries:
                    # Calculate delay with exponential backoff and jitter
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    log_func(f"[WARNING] {service_label} Rate limit exceeded. Retrying in {delay:.2f} seconds ({attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                else:
                    log_func(f"[ERROR] {service_label} Rate limit exceeded after {max_retries} retries.")
                    return ""
            else:
                # For other types of errors, we might still want to retry
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    log_func(f"[WARNING] {service_label} Translation error: {e}. Retrying in {delay:.2f} seconds ({attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                else:
                    log_func(f"[ERROR] {service_label} Translation failed after {max_retries} retries: {e}")
                    return ""
    
    return ""

# HTML template for viewing logs in a separate tab
LOG_VIEWER_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Log Viewer - SRT Subtitle Translator</title>
    <style>
        body { 
            font-family: sans-serif; 
            margin: 0; 
            padding: 0; 
            background-color: #1e1e2e; 
            color: #cdd6f4;
        }
        .container {
            max-width: 1200px;
            margin: 2em auto;
            background: #282a36;
            padding: 2em;
            border-radius: 8px;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3);
        }
        h1 { text-align: center; color: #f5f5f5; margin-top: 0; }
        #log-container { 
            background-color: #1a1a24; 
            color: #cdd6f4; 
            padding: 1em; 
            border-radius: 6px; 
            font-family: monospace; 
            height: 70vh; 
            overflow-y: auto;
            white-space: pre-wrap;
            margin-top: 1em;
            border: 1px solid #444;
        }
        .error { color: #f38ba8; }
        .warning { color: #f9e2af; }
        .info { color: #89b4fa; }
        .debug { color: #a6e3a1; }
        .controls { 
            margin-top: 1em; 
            display: flex; 
            align-items: center;
            background: #313244;
            padding: 1em;
            border-radius: 6px;
            border: 1px solid #444;
        }
        .controls button { 
            background-color: #74c7ec; 
            border: none; 
            color: #1e1e2e; 
            padding: 0.5em 1em; 
            margin-right: 1em;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
        }
        .controls button:hover { background-color: #89dceb; }
        .controls label { margin-right: 0.5em; }
        .nav-links {
            text-align: center;
            margin-bottom: 1.5em;
        }
        .nav-links a {
            display: inline-block;
            padding: 0.5em 1em;
            margin: 0 0.5em;
            color: #89b4fa;
            text-decoration: none;
            border-radius: 4px;
        }
        .nav-links a:hover {
            background-color: #313244;
            text-decoration: underline;
        }
        .nav-links a.active {
            font-weight: bold;
            border-bottom: 2px solid #89b4fa;
        }
        .settings-panel {
            margin-top: 2em;
            padding: 1em;
            background-color: #313244;
            border-radius: 6px;
            border: 1px solid #444;
        }
        .settings-panel h3 {
            margin-top: 0;
            color: #f5f5f5;
            border-bottom: 1px solid #444;
            padding-bottom: 0.5em;
        }
        .checkbox-container {
            display: flex;
            align-items: center;
            margin-bottom: 0.5em;
        }
        .checkbox-container input[type="checkbox"] {
            margin-right: 0.5em;
        }
        select, input[type="checkbox"] {
            background-color: #1e1e2e;
            color: #cdd6f4;
            border: 1px solid #444;
            border-radius: 4px;
            padding: 0.3em;
        }
        select option {
            background-color: #1e1e2e;
            color: #cdd6f4;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>SRT Subtitle Translator</h1>
        
        <!-- Navigation Links -->
        <div class="nav-links">
            <a href="/">Home</a>
            <a href="/config">Full Configuration</a>
            <a href="/logs" class="active">Log Viewer</a>
        </div>

        <div class="controls">
            <button id="refresh-btn">Refresh Now</button>
            <div class="checkbox-container">
                <input type="checkbox" id="auto-refresh" checked>
                <label for="auto-refresh">Auto-refresh</label>
            </div>
            <span id="status" style="margin-left: 1em; color: #bac2de;"></span>
        </div>
        
        <div id="log-container">Loading logs...</div>
        
        <div class="settings-panel">
            <h3>Log View Settings</h3>
            <div style="display: flex; align-items: center; gap: 20px;">
                <div class="checkbox-container">
                    <input type="checkbox" id="follow-logs" checked>
                    <label for="follow-logs">Auto-scroll to newest logs</label>
                </div>
                <div style="display: flex; align-items: center;">
                    <label for="log-level" style="margin-right: 8px;">Show log level:</label>
                    <select id="log-level">
                        <option value="all">All logs</option>
                        <option value="info">Info and above</option>
                        <option value="warning">Warnings and errors</option>
                        <option value="error">Errors only</option>
                    </select>
                </div>
            </div>
        </div>
    </div>

    <script>
        const logContainer = document.getElementById('log-container');
        const refreshBtn = document.getElementById('refresh-btn');
        const autoRefreshCheckbox = document.getElementById('auto-refresh');
        const statusElement = document.getElementById('status');
        const followLogsCheckbox = document.getElementById('follow-logs');
        const logLevelSelect = document.getElementById('log-level');
        let isScrolledToBottom = true;
        let refreshInterval;

        // Detect if user manually scrolled up
        logContainer.addEventListener('scroll', () => {
            isScrolledToBottom = Math.abs(
                (logContainer.scrollHeight - logContainer.clientHeight) - 
                logContainer.scrollTop
            ) < 10;
            // Update follow logs checkbox based on scrolling behavior
            followLogsCheckbox.checked = isScrolledToBottom;
        });

        // Update scroll behavior when follow logs checkbox changes
        followLogsCheckbox.addEventListener('change', () => {
            isScrolledToBottom = followLogsCheckbox.checked;
            if (isScrolledToBottom) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        });

        function formatLogs(logText) {
            if (!logText) return "No logs available";
            
            // Apply syntax highlighting
            return logText
                .replace(/\[ERROR\].*$/gm, match => `<span class="error">${match}</span>`)
                .replace(/\[WARNING\].*$/gm, match => `<span class="warning">${match}</span>`)
                .replace(/\[INFO\].*$/gm, match => `<span class="info">${match}</span>`)
                .replace(/\[DEBUG\].*$/gm, match => `<span class="debug">${match}</span>`);
        }

        function filterLogsByLevel(logText) {
            const level = logLevelSelect.value;
            if (level === 'all') return logText;
            
            const lines = logText.split('\n');
            let filteredLines = [];
            
            for (const line of lines) {
                if (level === 'error' && line.includes('[ERROR]')) {
                    filteredLines.push(line);
                } else if (level === 'warning' && (line.includes('[ERROR]') || line.includes('[WARNING]'))) {
                    filteredLines.push(line);
                } else if (level === 'info' && (line.includes('[ERROR]') || line.includes('[WARNING]') || line.includes('[INFO]'))) {
                    filteredLines.push(line);
                } else if (level === 'all') {
                    filteredLines.push(line);
                }
            }
            
            return filteredLines.join('\n');
        }

        function fetchLogs() {
            statusElement.textContent = "Fetching logs...";
            
            fetch('/api/logs')
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! Status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    const filteredLogs = filterLogsByLevel(data.logs);
                    logContainer.innerHTML = formatLogs(filteredLogs);
                    
                    if (isScrolledToBottom) {
                        logContainer.scrollTop = logContainer.scrollHeight;
                    }
                    
                    statusElement.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
                })
                .catch(error => {
                    console.error('Error fetching logs:', error);
                    statusElement.textContent = `Error: ${error.message}`;
                });
        }

        function toggleAutoRefresh() {
            if (autoRefreshCheckbox.checked) {
                refreshInterval = setInterval(fetchLogs, 3000);
                statusElement.textContent = "Auto-refresh enabled";
            } else {
                clearInterval(refreshInterval);
                statusElement.textContent = "Auto-refresh disabled";
            }
        }

        // Log level filter change
        logLevelSelect.addEventListener('change', fetchLogs);

        // Initial load
        fetchLogs();
        
        // Setup event listeners
        refreshBtn.addEventListener('click', fetchLogs);
        autoRefreshCheckbox.addEventListener('change', toggleAutoRefresh);
        
        // Start auto-refresh
        toggleAutoRefresh();
    </script>
</body>
</html>
"""

CONFIG_EDITOR_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Configuration Editor - SRT Subtitle Translator</title>
    <style>
        /* Updated theme to match Home/Logs page */
        body { 
            font-family: sans-serif; 
            margin: 0; 
            padding: 0; 
            background-color: #1e1e2e; /* Changed */
            color: #cdd6f4; /* Changed */
        }
        .container { 
            max-width: 1200px; 
            margin: 2em auto; 
            background: #282a36; /* Changed */
            padding: 2em;
            border-radius: 8px;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3); /* Adjusted shadow slightly */
        }
        h1 { text-align: center; color: #f5f5f5; margin-top: 0; } /* Changed */
        h2 { 
            color: #f5f5f5; /* Changed */
            border-bottom: 1px solid #444; /* Changed */
            padding-bottom: 0.5em; 
        }
        .config-container {
            background: #313244; /* Changed */
            border-radius: 6px;
            padding: 1em;
            border: 1px solid #444;
            margin-top: 1em;
        }
        .form-group {
            margin-bottom: 1em;
        }
        .form-group label {
            display: block;
            margin-bottom: 0.5em;
            font-weight: bold;
            color: #bac2de; /* Added */
        }
        .form-group input, .form-group select {
            width: 100%;
            padding: 0.5em;
            border: 1px solid #444; /* Changed */
            border-radius: 4px;
            box-sizing: border-box;
            background-color: #1a1a24; /* Changed */
            color: #cdd6f4; /* Changed */
        }
        .buttons {
            margin-top: 1.5em;
            text-align: right;
        }
        .buttons button {
            padding: 0.6em 1.2em;
            border-radius: 4px;
            cursor: pointer;
            margin-left: 0.5em;
            font-weight: bold; /* Added */
        }
        .cancel {
            background-color: #6c757d; /* Changed */
            border: 1px solid #5a6268;
            color: #f8f9fa;
        }
        .cancel:hover {
            background-color: #5a6268;
        }
        button[type="submit"] {
            background-color: #74c7ec; /* Changed */
            border: none;
            color: #1e1e2e; /* Changed */
        }
        button[type="submit"]:hover {
            background-color: #89dceb; /* Changed */
        }
        .notification {
            padding: 1em;
            margin-bottom: 1em;
            border-radius: 4px;
            display: none;
            border-left: 5px solid; /* Added */
        }
        .notification.success {
            background-color: #3c4a4f; /* Changed */
            border-color: #a6e3a1; /* Changed */
            color: #a6e3a1; /* Changed */
        }
        .notification.error {
            background-color: #382a37; /* Changed */
            border-color: #f38ba8; /* Changed */
            color: #f38ba8; /* Changed */
        }
        .section {
            background-color: #282a36; /* Changed */
            padding: 1em;
            border-radius: 4px;
            margin-bottom: 1em;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2); /* Adjusted */
            border: 1px solid #444; /* Added */
        }
        .loading {
            text-align: center;
            padding: 2em;
            color: #bac2de; /* Changed */
        }
        .nav-links {
            text-align: center;
            margin-bottom: 1.5em;
        }
        .nav-links a {
            display: inline-block;
            padding: 0.5em 1em;
            margin: 0 0.5em;
            color: #89b4fa; /* Changed */
            text-decoration: none;
            border-radius: 4px;
        }
        .nav-links a:hover {
            background-color: #313244; /* Changed */
            text-decoration: underline;
        }
        .nav-links a.active {
            font-weight: bold;
            border-bottom: 2px solid #89b4fa; /* Changed */
        }
        .settings-panel {
            margin-top: 2em;
            padding: 1em;
            background-color: #313244; /* Changed */
            border-radius: 6px;
            border: 1px solid #444;
        }
        .settings-panel h3 {
            margin-top: 0;
            color: #f5f5f5; /* Changed */
            border-bottom: 1px solid #444;
            padding-bottom: 0.5em;
        }
        .search-box {
            padding: 0.5em;
            margin-bottom: 1em;
            width: 100%;
            border: 1px solid #444; /* Changed */
            border-radius: 4px;
            box-sizing: border-box;
            background-color: #1a1a24; /* Changed */
            color: #cdd6f4; /* Changed */
        }
        /* Highlight for search results */
        .highlight-search {
            background-color: #45475a !important; /* Use a subtle highlight color from the theme */
            transition: background-color 0.3s ease;
        }
        small {
            color: #bac2de; /* Style tooltips */
            font-weight: normal;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>SRT Subtitle Translator</h1>
        
        <div class="nav-links">
            <a href="/">Home</a>
            <a href="/config" class="active">Full Configuration</a>
            <a href="/logs">Log Viewer</a>
        </div>
        
        <div id="notification" class="notification"></div>
        
        <div class="config-container">
            <input type="text" class="search-box" id="search-config" placeholder="Search configuration settings...">
            
            <form id="config-form">
                <div id="config-sections">
                    <!-- Config sections will be dynamically generated here -->
                    <div class="loading">Loading configuration...</div>
                </div>
                
                <div class="buttons">
                    <button type="button" class="cancel" id="reset-btn">Reset Changes</button>
                    <button type="submit" id="save-btn">Save Configuration</button>
                </div>
            </form>
        </div>
        
        <div class="settings-panel">
            <h3>Configuration Editor Help</h3>
            <p>This page allows you to configure all settings for the SRT Subtitle Translator. Changes will be saved to the config.ini file.</p>
            <ul>
                <li><strong>API Keys</strong>: To use services like DeepL or OpenAI, enter your API keys in the appropriate sections.</li>
                <li><strong>Ollama Settings</strong>: Configure GPU usage, thread count, and model parameters for local Ollama translation.</li>
                <li><strong>Translation Services</strong>: Enable or disable various translation services and set their priority.</li>
            </ul>
            <p>After making changes, click <strong>Save Configuration</strong> to apply them. Use <strong>Reset Changes</strong> to revert to the previous state.</p>
        </div>
    </div>

    <script>
        let originalConfig = {};
        const searchBox = document.getElementById('search-config');
        
        // Search functionality
        searchBox.addEventListener('input', function() {
            const searchTerm = this.value.toLowerCase().trim();
            const sections = document.querySelectorAll('.section');
            let firstMatch = null;

            sections.forEach(section => {
                let sectionVisible = false;
                const sectionTitle = section.querySelector('h2');
                const formGroups = section.querySelectorAll('.form-group');

                // Check section title
                if (sectionTitle && sectionTitle.textContent.toLowerCase().includes(searchTerm)) {
                    sectionVisible = true;
                    sectionTitle.classList.add('highlight-search'); // Highlight title
                    if (!firstMatch) firstMatch = sectionTitle;
                } else if (sectionTitle) {
                    sectionTitle.classList.remove('highlight-search');
                }

                // Check form groups within the section
                formGroups.forEach(group => {
                    const label = group.querySelector('label');
                    let fieldVisible = false;
                    if (label && label.textContent.toLowerCase().includes(searchTerm)) {
                        sectionVisible = true; // Make section visible if any field matches
                        fieldVisible = true;
                        group.classList.add('highlight-search'); // Highlight matching fields
                        if (!firstMatch) firstMatch = group;
                    } else {
                        group.classList.remove('highlight-search');
                    }
                    // Show/hide individual fields based on match (optional, can be noisy)
                    // group.style.display = searchTerm && !fieldVisible ? 'none' : 'block'; 
                });

                // Show/hide the entire section based on whether the title or any field matched
                section.style.display = searchTerm && !sectionVisible ? 'none' : 'block';
            });
            
            // Scroll to the first match
            if (firstMatch) {
                firstMatch.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        });
        
        // ... rest of the script ...

        // Fetch the current configuration
        function fetchConfig() {
            fetch('/api/config')
                .then(response => {
                    if (!response.ok) {
                        // Try to get error text from response body
                        return response.text().then(text => {
                            throw new Error(`HTTP error! Status: ${response.status} ${response.statusText} - ${text}`);
                        });
                    }
                    return response.json();
                })
                .then(data => {
                    originalConfig = data; // Store the whole response {config: {...}}
                    renderConfigForm(data.config);
                })
                .catch(error => {
                    console.error('Error fetching configuration:', error);
                    showNotification('error', `Error loading configuration: ${error.message}`);
                    document.getElementById('config-sections').innerHTML = 
                        '<div class="notification error">Failed to load configuration. Check logs.</div>';
                });
        }
        
        // Render the configuration form
        function renderConfigForm(config) {
            const configSections = document.getElementById('config-sections');
            configSections.innerHTML = ''; // Clear previous content
            
            if (!config || Object.keys(config).length === 0) {
                 configSections.innerHTML = '<div class="notification error">Configuration data is empty or invalid.</div>';
                 return;
            }

            // Sort sections for consistent display
            const sortedSections = Object.keys(config).sort();
            
            for (const section of sortedSections) {
                const sectionDiv = document.createElement('div');
                sectionDiv.className = 'section';
                
                const sectionTitle = document.createElement('h2');
                sectionTitle.textContent = section.toUpperCase();
                sectionDiv.appendChild(sectionTitle);
                
                // Add section description for important sections
                // ... (descriptions remain the same) ...
                if (section === 'ollama') {
                    const description = document.createElement('p');
                    description.style.fontStyle = 'italic';
                    description.style.marginBottom = '1em';
                    description.style.color = '#bac2de'; // Use theme color
                    description.textContent = 'Configure Ollama performance settings like GPU count, thread count, and memory options.';
                    sectionDiv.appendChild(description);
                } else if (section === 'translation') {
                    const description = document.createElement('p');
                    description.style.fontStyle = 'italic';
                    description.style.marginBottom = '1em';
                    description.style.color = '#bac2de'; // Use theme color
                    description.textContent = 'Configure service priority and retry settings for translation services.';
                    sectionDiv.appendChild(description);
                }
                
                const settings = config[section];
                // Handle cases where a section might exist but be empty
                if (!settings || typeof settings !== 'object') continue; 

                const sortedKeys = Object.keys(settings).sort();
                
                for (const key of sortedKeys) {
                    const value = settings[key];
                    const formGroup = document.createElement('div');
                    formGroup.className = 'form-group';
                    
                    const label = document.createElement('label');
                    label.setAttribute('for', `${section}-${key}`);
                    label.textContent = formatSettingName(key);
                    
                    // Add tooltips/descriptions for specific settings
                    // ... (tooltips remain the same) ...
                    if (section === 'ollama' && key === 'num_gpu') {
                        const tooltip = document.createElement('small');
                        tooltip.textContent = ' (Number of GPUs to use for processing)';
                        label.appendChild(tooltip);
                    } else if (section === 'ollama' && key === 'num_thread') {
                        const tooltip = document.createElement('small');
                        tooltip.textContent = ' (Number of CPU threads to use)';
                        label.appendChild(tooltip);
                    } else if (section === 'ollama' && key === 'use_mmap') {
                        const tooltip = document.createElement('small');
                        tooltip.textContent = ' (Memory-map the model for better performance)';
                        label.appendChild(tooltip);
                    } else if (section === 'ollama' && key === 'use_mlock') {
                        const tooltip = document.createElement('small');
                        tooltip.textContent = ' (Lock the model in RAM to prevent swapping)';
                        label.appendChild(tooltip);
                    }
                    
                    let input;
                    
                    // Create appropriate input based on value type
                    if (typeof value === 'boolean') {
                        input = document.createElement('input');
                        input.type = 'checkbox';
                        input.checked = value;
                    } else if (typeof value === 'number') {
                        input = document.createElement('input');
                        input.type = 'number';
                        input.step = Number.isInteger(value) ? '1' : '0.1'; // Handle float/int steps
                        input.value = value;
                    } else if (key.includes('api_key') || key.includes('password')) {
                        input = document.createElement('input');
                        input.type = 'password';
                        input.value = value;
                    } else if (key === 'enabled' && typeof value === 'string') { // Handle boolean as string from configparser
                        input = document.createElement('input');
                        input.type = 'checkbox';
                        input.checked = value.toLowerCase() === 'true';
                    } else if (key === 'model' && section === 'ollama') {
                        // ... (Ollama model select logic remains the same) ...
                        input = document.createElement('select');
                        
                        // Fetch available models from Ollama if we're on the Home page
                        fetch('/api/models', { method: 'GET' })
                            .then(response => response.json())
                            .then(data => {
                                const models = data.models || [];
                                
                                // Add current value if not in list
                                if (value && !models.includes(value)) {
                                    models.unshift(value);
                                }
                                
                                for (const model of models) {
                                    const option = document.createElement('option');
                                    option.value = model;
                                    option.textContent = model;
                                    option.selected = model === value;
                                    input.appendChild(option);
                                }
                            })
                            .catch(error => {
                                console.error('Error fetching models:', error);
                                // Still create an option for the current value
                                const option = document.createElement('option');
                                option.value = value;
                                option.textContent = value;
                                option.selected = true;
                                input.appendChild(option);
                            });
                    } else if (key === 'service_priority' && section === 'translation') {
                        // ... (Service priority input remains the same) ...
                        input = document.createElement('input');
                        input.type = 'text';
                        input.value = value;
                        input.placeholder = 'e.g., deepl,google,libretranslate,mymemory';
                        const hint = document.createElement('small');
                        hint.textContent = ' (Comma-separated list of services in preferred order)';
                        label.appendChild(hint);
                    } else {
                        input = document.createElement('input');
                        input.type = 'text';
                        input.value = value;
                    }
                    
                    input.id = `${section}-${key}`;
                    input.name = `${section}-${key}`; // Use name for potential form submission (though we use JS)
                    // input.className = 'form-control'; // Removed class, using direct styling
                    input.setAttribute('data-section', section);
                    input.setAttribute('data-key', key);
                    
                    formGroup.appendChild(label);
                    formGroup.appendChild(input);
                    sectionDiv.appendChild(formGroup);
                }
                
                configSections.appendChild(sectionDiv);
            }
        }
        
        // Format setting name for display (e.g., "api_key" -> "API Key")
        function formatSettingName(key) {
            // ... (remains the same) ...
            return key
                .split('_')
                .map(word => word.charAt(0).toUpperCase() + word.slice(1))
                .join(' ');
        }
        
        // Save the configuration
        function saveConfig() {
            const updatedConfig = {};
            
            // Get all input elements with data attributes
            const inputs = document.querySelectorAll('#config-form [data-section][data-key]');
            
            // Build updated config object
            for (const input of inputs) {
                const section = input.getAttribute('data-section');
                const key = input.getAttribute('data-key');
                
                if (!updatedConfig[section]) {
                    updatedConfig[section] = {};
                }
                
                let value;
                if (input.type === 'checkbox') {
                    value = input.checked;
                } else if (input.type === 'number') {
                    // Ensure number is stored as number, not string
                    value = input.value === '' ? null : Number(input.value); 
                } else {
                    value = input.value;
                }
                
                updatedConfig[section][key] = value;
            }
            
            console.log("Saving config:", JSON.stringify(updatedConfig, null, 2)); // Debug log

            // Send updated config to server
            fetch('/api/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ config: updatedConfig }), // Send in expected format
            })
                .then(response => {
                    // Check status before parsing JSON
                    if (!response.ok) {
                         return response.text().then(text => {
                            throw new Error(`HTTP error! Status: ${response.status} ${response.statusText} - ${text}`);
                        });
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        showNotification('success', 'Configuration saved successfully!');
                        // Update original config to match new config
                        // Need to re-fetch or carefully merge? Re-fetch is safer.
                        fetchConfig(); // Re-fetch to ensure consistency
                    } else {
                        showNotification('error', `Error saving configuration: ${data.message || 'Unknown error'}`);
                    }
                })
                .catch(error => {
                    console.error('Error saving configuration:', error);
                    showNotification('error', `Error saving configuration: ${error.message}`);
                });
        }
        
        // Show notification message
        function showNotification(type, message) {
            // ... (remains the same) ...
            const notification = document.getElementById('notification');
            notification.textContent = message;
            notification.className = `notification ${type}`;
            notification.style.display = 'block';
            
            // Hide notification after 5 seconds
            setTimeout(() => {
                notification.style.display = 'none';
            }, 5000);
        }
        
        // Reset form to original values
        function resetForm() {
            if (confirm('Are you sure you want to reset all changes to the last saved state?')) {
                // Re-render using the stored originalConfig
                if (originalConfig && originalConfig.config) {
                    renderConfigForm(originalConfig.config);
                    showNotification('info', 'Form reset to last saved values.'); // Use info color
                } else {
                     showNotification('error', 'Could not reset form, original config not loaded.');
                }
            }
        }
        
        // Initialize the page
        document.addEventListener('DOMContentLoaded', () => {
            // Fetch configuration
            fetchConfig();
            
            // Set up event listeners
            document.getElementById('config-form').addEventListener('submit', event => {
                event.preventDefault(); // Prevent default form submission
                saveConfig();
            });
            
            document.getElementById('reset-btn').addEventListener('click', resetForm);
        });
    </script>
</body>
</html>
"""

# Global constants
CONFIG_FILENAME = "config.ini"
CONFIG_EXAMPLE_FILENAME = "config.ini.example"
LOG_FILENAME = "translator.log"
MAX_LOG_BUFFER_SIZE = 1000
LOG_BUFFER = deque(maxlen=MAX_LOG_BUFFER_SIZE)
TEMP_DIRS_TO_CLEAN = set()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILENAME),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

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

def live_stream_translation_info(stage, original, translation, current_idx, total_lines, deepl_translation=None, critic_type=None):
    """Display live translation information in a colorful, formatted way."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    
    supports_color = Colors.terminal_supports_color()
    if not supports_color:
        RESET = BOLD = GREEN = BLUE = YELLOW = CYAN = MAGENTA = RED = ""

    separator = f"{CYAN}{'=' * 30} TRANSLATION PROCESS {'=' * 30}{RESET}"
    sub_separator = f"{CYAN}{'-' * 80}{RESET}"
    progress = f"{current_idx}/{total_lines}"

    print(f"\n{separator}", flush=True)
    print(f"{BOLD}{GREEN}[{progress}] STAGE: {stage.upper()}{RESET}", flush=True)
    print(f"{sub_separator}", flush=True)
    
    print(f"{BOLD}Original Text:{RESET}")
    print(f"{BLUE}{original}{RESET}")
    
    if deepl_translation and stage.upper() != "DEEPL TRANSLATION":
        print(f"\n{BOLD}DeepL Translation:{RESET}")
        print(f"{YELLOW}{deepl_translation}{RESET}")
    
    if translation:
        if translation != "No changes":
            label = "Translation"
            if critic_type:
                label += f" ({critic_type})"
            print(f"\n{BOLD}{label}:{RESET}")
            print(f"{MAGENTA}{translation}{RESET}")
        else:
            print(f"\n{BOLD}Result:{RESET} {RED}No changes made{RESET}")
    
    print(f"\n{sub_separator}")
    print(f"{BOLD}{GREEN}✓ {stage.upper()} COMPLETE{RESET}")
    
    timestamp = time.strftime("%H:%M:%S")
    print(f"{CYAN}[Timestamp: {timestamp}]{RESET}")
    print(f"{separator}\n", flush=True)
    
    log_msg = f"[LIVE] [{stage}] Original: \"{original}\" | Translation: \"{translation}\""
    if file_logger:
        file_logger.info(log_msg)
    
    sys.stdout.flush()

LANGUAGE_MAPPING = {
    "english": "en",
    "danish": "da",
    "spanish": "es",
    "german": "de",
    "french": "fr",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "swedish": "sv",
    "norwegian": "no",
    "finnish": "fi",
    "polish": "pl",
    "russian": "ru",
    "japanese": "ja",
    "chinese": "zh",
    "korean": "ko",
    "arabic": "ar",
    "hindi": "hi",
    "turkish": "tr",
}

def get_iso_code(language_name: str) -> str:
    language_name = language_name.lower().strip('"\' ')
    return LANGUAGE_MAPPING.get(language_name, language_name)

TEMP_DIRS_TO_CLEAN = set()

def cleanup_temp_dirs():
    print("[INFO] Cleaning up temporary directories...")
    for temp_dir in TEMP_DIRS_TO_CLEAN:
        if os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                print(f"[INFO] Removed temporary directory: {temp_dir}")
            except Exception as e:
                print(f"[WARNING] Failed to remove temp directory {temp_dir}: {e}")
    TEMP_DIRS_TO_CLEAN.clear()

atexit.register(cleanup_temp_dirs)
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))

def which(cmd):
    return shutil.which(cmd) is not None

def run_cmd(cmd_list):
    print(f"[CMD] {' '.join(cmd_list)}")
    process = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if process.stdout:
        for line in iter(process.stdout.readline, b''):
            print(line.decode().strip())
        process.stdout.close()
    return process.wait()

def ensure_python3_venv_available():
    try:
        import venv  # noqa: F401
        print("[INFO] Python 'venv' module is available.")
        return True
    except ImportError:
        print("[ERROR] Python 'venv' module not found.")
        print("Please install the package for your Python distribution that provides the 'venv' module.")
        return False

REQUIRED_PYTHON_PACKAGES = ["Flask", "pysrt", "requests", "colorama"]

def create_and_populate_venv(venv_dir="venv"):
    if not os.path.exists(venv_dir):
        print(f"[INFO] Creating virtual environment in {venv_dir} ...")
        rc = subprocess.call([sys.executable, "-m", "venv", venv_dir])
        if (rc != 0):
            print(f"[ERROR] Failed to create venv using '{sys.executable}'.")
            sys.exit(1)
    else:
        print(f"[INFO] Virtual environment '{venv_dir}' already exists.")

    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "bin", "pip")

    if not os.path.exists(pip_exe):
         print(f"[ERROR] pip executable not found at {pip_exe}")
         sys.exit(1)

    print("[INFO] Installing/Updating Python packages in the virtual environment...")
    cmd_list = [pip_exe, "install", "--upgrade"] + REQUIRED_PYTHON_PACKAGES
    rc = run_cmd(cmd_list)
    if rc != 0:
        print("[ERROR] Failed to install required packages in the venv.")
        sys.exit(1)
    print("[INFO] Required packages installed successfully.")

def is_running_in_venv():
    return (
        hasattr(sys, 'real_prefix') or
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    )

def re_run_in_venv(venv_dir="venv"):
    if os.name == "nt":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")

    if not os.path.exists(python_exe):
        print(f"[ERROR] Python executable not found in venv at {python_exe}")
        sys.exit(1)

    print(f"[INFO] Re-running script inside venv: {python_exe} {__file__}")
    rc = subprocess.call([python_exe, __file__] + sys.argv[1:])
    sys.exit(rc)

def setup_environment_and_run():
    if sys.platform == "darwin":  # macOS
        print("[INFO] Detected macOS system")
        has_venv = ensure_python3_venv_available()
        if not has_venv:
            if is_brew_installed():
                print("[INFO] Homebrew is installed. Will use it to setup environment.")
                if install_dependencies_with_brew():
                    print("[INFO] Successfully installed dependencies with Homebrew.")
                    has_venv = ensure_python3_venv_available()
                else:
                    print("[ERROR] Failed to install dependencies with Homebrew.")
                    sys.exit(1)
            else:
                print("[INFO] Homebrew not found. Recommending installation...")
                print("\nTo install Homebrew on macOS, run this command in your terminal:")
                print('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
                sys.exit(1)
    else:
        if not ensure_python3_venv_available():
            sys.exit(1)

    VENV_DIR = "venv_subtrans"
    if not is_running_in_venv():
        print("[INFO] Not running inside a virtual environment.")
        create_and_populate_venv(VENV_DIR)
        re_run_in_venv(VENV_DIR)
        sys.exit(0)  # This line should not be reached if re_run_in_venv works correctly
    else:
        print(f"[INFO] Running inside virtual environment: {sys.prefix}")
        
        # Check if required packages are installed
        missing_packages = []
        for package in REQUIRED_PYTHON_PACKAGES:
            try:
                __import__(package.lower())
                print(f"[INFO] Found required package: {package}")
            except ImportError:
                missing_packages.append(package)
                
        # Install missing packages if any
        if missing_packages:
            print(f"[INFO] Installing missing packages: {', '.join(missing_packages)}")
            pip_path = os.path.join(sys.prefix, "bin", "pip") if sys.platform != "win32" else os.path.join(sys.prefix, "Scripts", "pip.exe")
            cmd = [pip_path, "install"] + missing_packages
            result = run_cmd(cmd)
            if result != 0:
                print(f"[ERROR] Failed to install required packages. Exit code: {result}")
                sys.exit(1)
            print("[INFO] Successfully installed required packages.")
        
        run_web_ui()

def is_brew_installed():
    return which("brew")

def install_dependencies_with_brew():
    print("[INFO] Installing dependencies using Homebrew...")
    run_cmd(["brew", "update"])
    if not which("python3"):
        print("[INFO] Installing Python 3 with Homebrew...")
        run_cmd(["brew", "install", "python3"])
    else:
        print("[INFO] Python 3 is already installed.")

    if not which("pip3"):
        print("[WARNING] pip3 not found. Trying to install...")
        run_cmd(["brew", "reinstall", "python3"])

    return which("python3") and which("pip3")

def run_web_ui():
    import pysrt
    import requests
    import configparser
    import time
    import json
    import flask
    import os
    import logging
    from logging.handlers import RotatingFileHandler
    from flask import Flask, request, render_template_string, send_from_directory, redirect, url_for, jsonify

    LOG_BUFFER = []
    MAX_LOG_LINES = 500
    SUBS_FOLDER = "subs" # Define the folder for saved subtitles
    os.makedirs(SUBS_FOLDER, exist_ok=True) # Ensure the subs folder exists

    # --- Logging Setup ---
    global file_logger
    file_logger = None
    
    def append_log(msg: str):
        ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
        log_line = f"{ts} {msg}"
        LOG_BUFFER.append(log_line)
        if len(LOG_BUFFER) > MAX_LOG_LINES:
            LOG_BUFFER.pop(0)
        print(log_line, flush=True)
        
        if file_logger:
            log_level = "INFO"
            if msg.startswith("[ERROR]"):
                log_level = "ERROR"
            elif msg.startswith("[WARNING]"):
                log_level = "WARNING"
            elif msg.startswith("[DEBUG]"):
                log_level = "DEBUG"
            
            # Strip any prior timestamp if present
            if msg.startswith("[20"):  
                msg = msg[21:]  
            
            if log_level == "ERROR":
                file_logger.error(msg)
            elif log_level == "WARNING":
                file_logger.warning(msg)
            elif log_level == "DEBUG":
                file_logger.debug(msg)
            else:
                file_logger.info(msg)

    def get_logs():
        try:
            if os.path.exists(LOG_FILENAME):
                with open(LOG_FILENAME, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    return ''.join(lines[-500:])
            return '\n'.join(LOG_BUFFER)
        except Exception as e:
            return f"Error reading logs: {str(e)}\n\n" + '\n'.join(LOG_BUFFER)

    # Load config to set up any file logging
    cfg_for_logger = configparser.ConfigParser()
    try:
        cfg_for_logger.read(CONFIG_FILENAME)
    except:
        pass
    if cfg_for_logger.has_section("logging"):
        file_enabled = cfg_for_logger.getboolean("logging", "file_enabled", fallback=True)
        log_file = cfg_for_logger.get("logging", "log_file", fallback="translator.log")
        max_size_mb = cfg_for_logger.getint("logging", "max_size_mb", fallback=5)
        backup_count = cfg_for_logger.getint("logging", "backup_count", fallback=2)
        if file_enabled:
            max_bytes = max_size_mb * 1024 * 1024
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir)
                except Exception as e:
                    pass
            try:
                file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
                file_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                file_logger = logging.getLogger("subtitle_translator")
                file_logger.setLevel(logging.DEBUG)
                file_logger.addHandler(file_handler)
            except Exception as e:
                pass

    append_log("=== Starting Subtitle Translator Session ===")

    def load_config(config_path: str = CONFIG_FILENAME) -> configparser.ConfigParser:
        if not os.path.exists(config_path):
            append_log(f"[ERROR] Configuration file '{config_path}' not found!")
            sys.exit(f"Error: {config_path} not found.")
        cfg = configparser.ConfigParser()
        try:
            cfg.read(config_path)
            append_log(f"Loaded configuration from '{config_path}'")
        except configparser.Error as e:
            append_log(f"[ERROR] Failed to parse config: {e}")
            sys.exit(f"Error parsing {config_path}.")
        return cfg

    def call_ollama(server_url: str, endpoint_path: str, model: str, prompt: str, temperature: float = 0.2, cfg=None) -> str:
        url = f"{server_url.rstrip('/')}{endpoint_path}"

        # Default num_gpu value
        num_gpu = 20 # Default if not found in config or env

        # Prioritize config.ini for num_gpu
        if cfg is not None and cfg.has_section("ollama") and cfg.has_option("ollama", "num_gpu"):
            try:
                num_gpu = cfg.getint("ollama", "num_gpu")
            except ValueError:
                append_log(f"[WARNING] Invalid integer value for num_gpu in config.ini. Using default: {num_gpu}")
        else:
            # Fallback to environment variable if not in config
            num_gpu = int(os.environ.get("OLLAMA_NUM_GPU", num_gpu))

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_gpu": num_gpu
            }
        }

        # Optionally add other performance options from config if present
        if cfg is not None and cfg.has_section("ollama"):
            if cfg.has_option("ollama", "num_thread"):
                payload["options"]["num_thread"] = cfg.getint("ollama", "num_thread")
            if cfg.has_option("ollama", "use_mmap"):
                payload["options"]["use_mmap"] = cfg.getboolean("ollama", "use_mmap")
            if cfg.has_option("ollama", "use_mlock"):
                payload["options"]["use_mlock"] = cfg.getboolean("ollama", "use_mlock")
        
        append_log(f"[DEBUG] Calling Ollama: POST {url} | Model: {model} | Temperature: {temperature} | num_gpu: {num_gpu}")
        try:
            response = requests.post(url, json=payload, timeout=180)
            response.raise_for_status()
            response_json = response.json()
            return response_json.get("response", "")
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Ollama API request failed: {str(e)}")
            return f"Error: {str(e)}"

    def call_openai(api_key: str, api_base_url: str, model: str, prompt: str, temperature: float = 0.2) -> str:
        url = f"{api_base_url.rstrip('/')}/chat/completions"
        append_log(f"[DEBUG] Calling OpenAI: POST {url} | Model: {model} | Temperature: {temperature}")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature
        }

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=300)
            resp.raise_for_status()
            j = resp.json()
            if ("choices" in j and len(j["choices"]) > 0 and
                "message" in j["choices"][0] and "content" in j["choices"][0]["message"]):
                return j["choices"][0]["message"]["content"]
            else:
                append_log(f"[ERROR] Unexpected OpenAI response format.")
                return ""
        except requests.exceptions.Timeout:
            append_log("[ERROR] OpenAI request timed out.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] OpenAI request failed: {e}")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Invalid JSON from OpenAI: {e}")
            return ""

    def call_deepl(api_key: str, api_url: str, text: str, source_lang: str, target_lang: str) -> str:
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        params = {
            "auth_key": api_key,
            "text": text,
            "source_lang": source_iso.upper(),
            "target_lang": target_iso.upper(),
        }
        append_log(f"[DEBUG] Calling DeepL: {api_url} / {source_iso} -> {target_iso}")
        try:
            r = requests.post(api_url, data=params, timeout=120)
            r.raise_for_status()
            j = r.json()
            translations = j.get("translations", [])
            if translations:
                return translations[0].get("text", "")
            append_log("[WARNING] No translations from DeepL response.")
            return ""
        except requests.exceptions.Timeout:
            append_log("[ERROR] DeepL request timed out.")
            return ""
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] DeepL request failed: {e}")
            return ""
        except json.JSONDecodeError as e:
            append_log(f"[ERROR] Invalid JSON from DeepL: {e}")
            return ""

    def call_google_translate(text: str, source_lang: str, target_lang: str) -> str:
        """
        Uses the Google Translate API (free web API approach) for translation.
        """
        import urllib.parse
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling Google Translate: {source_iso} -> {target_iso}")
        
        base_url = "https://translate.googleapis.com/translate_a/single"
        
        params = {
            "client": "gtx",
            "sl": source_iso,
            "tl": target_iso,
            "dt": "t",
            "q": text
        }
        
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            # Parse the Google Translate response format
            result = response.json()
            translated_text = ""
            
            # Google returns an array of translated segments
            for segment in result[0]:
                if (segment[0]):
                    translated_text += segment[0]
            
            append_log(f"[DEBUG] Google Translate result: \"{translated_text}\"")
            return translated_text
            
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Google Translate request failed: {e}")
            return ""
        except (ValueError, KeyError, IndexError) as e:
            append_log(f"[ERROR] Failed to parse Google Translate response: {e}")
            return ""

    def call_libretranslate(text: str, source_lang: str, target_lang: str) -> str:
        """Call LibreTranslate service"""
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling LibreTranslate: {source_iso} -> {target_iso}")
        
        # Track which servers have failed recently
        global libre_failed_servers
        if not hasattr(call_libretranslate, "failed_servers"):
            call_libretranslate.failed_servers = {}
        
        # Configuration for LibreTranslate servers
        servers = [
            "https://libretranslate.de/translate",
            "https://translate.argosopentech.com/translate",
            "https://libretranslate.com/translate"
        ]
        
        current_time = time.time()
        # Clear servers that have been in failed state for more than 10 minutes
        for server, fail_time in list(call_libretranslate.failed_servers.items()):
            if current_time - fail_time > 600:  # 10 minutes
                del call_libretranslate.failed_servers[server]
        
        # Try each server, skipping recently failed ones
        for server in servers:
            # Skip servers that failed in the last 10 minutes
            if server in call_libretranslate.failed_servers:
                append_log(f"[INFO] Skipping recently failed LibreTranslate server: {server}")
                continue
                
            append_log(f"[DEBUG] Trying LibreTranslate server: {server}")
            try:
                payload = {
                    "q": text,
                    "source": source_iso,
                    "target": target_iso,
                    "format": "text"
                }
                
                # Reduced timeout from 8 seconds to 3 seconds
                response = requests.post(server, json=payload, timeout=3)
                response.raise_for_status()
                
                data = response.json()
                if "translatedText" in data:
                    result = data["translatedText"]
                    return result
                    
            except requests.exceptions.RequestException as e:
                append_log(f"[WARNING] LibreTranslate server {server} failed: {e}")
                # Mark this server as failed
                call_libretranslate.failed_servers[server] = current_time
                continue
            except (json.JSONDecodeError, KeyError) as e:
                append_log(f"[WARNING] Failed to parse LibreTranslate response from {server}: {e}")
                continue
        
        append_log("[ERROR] All LibreTranslate servers failed. Unable to get translation.")
        return ""

    def call_mymemory_translate(text: str, source_lang: str, target_lang: str) -> str:
        """Call MyMemory translation API"""
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling MyMemory Translation: {source_iso} -> {target_iso}")
        
        # Track recent failures
        if not hasattr(call_mymemory_translate, "last_failure_time"):
            call_mymemory_translate.last_failure_time = 0
        
        current_time = time.time()
        # Skip MyMemory if it failed recently (within the last 5 minutes)
        if current_time - call_mymemory_translate.last_failure_time < 300:
            append_log(f"[INFO] Skipping MyMemory due to recent failures")
            return ""
        
        try:
            url = "https://api.mymemory.translated.net/get"
            params = {
                "q": text,
                "langpair": f"{source_iso}|{target_iso}"
            }
            
            # Reduced timeout from default to 3 seconds
            response = requests.get(url, params=params, timeout=3)
            response.raise_for_status()
            
            data = response.json()
            if data and "responseData" in data and "translatedText" in data["responseData"]:
                result = data["responseData"]["translatedText"]
                append_log(f"[DEBUG] MyMemory result: \"{result}\"")
                return result
            else:
                append_log(f"[WARNING] Unexpected response format from MyMemory: {data}")
                return ""
                
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] MyMemory request failed: {e}")
            call_mymemory_translate.last_failure_time = current_time
            return ""
        except (json.JSONDecodeError, KeyError) as e:
            append_log(f"[ERROR] Failed to parse MyMemory response: {e}")
            call_mymemory_translate.last_failure_time = current_time
            return ""

    def get_multiple_translations(text: str, source_lang: str, target_lang: str, cfg) -> dict:
        """
        Calls multiple translation services in parallel and returns all available translations.
        Ollama is used to evaluate and decide on the best translation, not as a fallback.
        
        Args:
            text: The text to translate
            source_lang: Source language
            target_lang: Target language
            cfg: Configuration object
            
        Returns:
            dict: Dictionary with service names as keys and translations as values
        """
        translations = {}
        service_errors = {}
        service_priority = cfg.get("translation", "service_priority", fallback="deepl,google,libretranslate,mymemory,azure,yandex")
        max_retries = cfg.getint("translation", "max_retries", fallback=2)
        base_delay = cfg.getfloat("translation", "base_delay", fallback=1.0)
        timeout = cfg.getfloat("translation", "timeout", fallback=3.0)  # Default timeout for services
        
        # Parse priority list
        service_priority = [s.strip().lower() for s in service_priority.split(',')]
        
        # Only try services that are enabled in the config
        use_deepl = cfg.getboolean("general", "use_deepl", fallback=False)
        use_google = cfg.getboolean("general", "use_google", fallback=True)
        use_libretranslate = cfg.getboolean("general", "use_libretranslate", fallback=True)
        use_mymemory = cfg.getboolean("general", "use_mymemory", fallback=True)
        use_azure = cfg.getboolean("general", "use_azure", fallback=False)
        use_yandex = cfg.getboolean("general", "use_yandex", fallback=False)
        
        # Process each online translation service according to priority
        import concurrent.futures
        import time
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {}
            
            # Submit tasks for each enabled service
            for service in service_priority:
                if service == "deepl" and use_deepl and cfg.getboolean("deepl", "enabled", fallback=False):
                    d_key = cfg.get("deepl", "api_key", fallback="")
                    d_url = cfg.get("deepl", "api_url", fallback="")
                    if d_key and d_url:
                        futures[executor.submit(
                            call_translation_service_with_retry,
                            call_deepl, d_key, d_url, text, source_lang, target_lang, 
                            max_retries=max_retries, base_delay=base_delay,
                            service_name="DeepL", log_func=append_log
                        )] = "DeepL"
                
                elif service == "google" and use_google:
                    # Google Translate - always available as it doesn't require API key
                    futures[executor.submit(
                        call_translation_service_with_retry,
                        call_google_translate, text, source_lang, target_lang,
                        max_retries=max_retries, base_delay=base_delay,
                        service_name="Google", log_func=append_log
                    )] = "Google"
                
                elif service == "libretranslate" and use_libretranslate:
                    futures[executor.submit(
                        call_translation_service_with_retry,
                        call_libretranslate, text, source_lang, target_lang,
                        max_retries=max_retries, base_delay=base_delay,
                        service_name="LibreTranslate", log_func=append_log
                    )] = "LibreTranslate"
                
                elif service == "mymemory" and use_mymemory:
                    futures[executor.submit(
                        call_translation_service_with_retry,
                        call_mymemory_translate, text, source_lang, target_lang,
                        max_retries=max_retries, base_delay=base_delay,
                        service_name="MyMemory", log_func=append_log
                    )] = "MyMemory"
                    
                elif service == "azure" and use_azure and cfg.getboolean("azure", "enabled", fallback=False):
                    a_key = cfg.get("azure", "api_key", fallback="")
                    a_region = cfg.get("azure", "region", fallback="")
                    if a_key and a_region:
                        futures[executor.submit(
                            call_translation_service_with_retry,
                            call_azure_translate, a_key, a_region, text, source_lang, target_lang, 
                            max_retries=max_retries, base_delay=base_delay,
                            service_name="Azure", log_func=append_log
                        )] = "Azure"
                
                elif service == "yandex" and use_yandex and cfg.getboolean("yandex", "enabled", fallback=False):
                    y_key = cfg.get("yandex", "api_key", fallback="")
                    if y_key:
                        futures[executor.submit(
                            call_translation_service_with_retry,
                            call_yandex_translate, y_key, text, source_lang, target_lang, 
                            max_retries=max_retries, base_delay=base_delay,
                            service_name="Yandex", log_func=append_log
                        )] = "Yandex"
            
            # Process completed tasks
            for future in concurrent.futures.as_completed(futures):
                service_name = futures[future]
                try:
                    result = future.result()
                    if result:
                        translations[service_name] = result
                    else:
                        service_errors[service_name] = "Empty result"
                except Exception as e:
                    service_errors[service_name] = str(e)
        
        # Log the results
        if translations:
            append_log(f"[INFO] Got translations from {len(translations)} services: {', '.join(translations.keys())}")
        if service_errors:
            append_log(f"[INFO] Failed to get translations from {len(service_errors)} services: {', '.join(service_errors.keys())}")
        
        # Now use Ollama to select the best translation if it's enabled
        # This ensures Ollama evaluates the results rather than being used as a fallback
        try:
            if cfg.getboolean("ollama", "enabled", fallback=False) and translations:
                # We'll add Ollama's own translation to the mix
                ollama_translation = run_ollama_translation(text, source_lang, target_lang, cfg, online_translations=translations)
                if ollama_translation:
                    translations["Ollama"] = ollama_translation
        except Exception as e:
            append_log(f"[ERROR] Ollama evaluation failed: {e}")
            # Continue with what we have from online services
        
        return translations

    def run_ollama_translation(text: str, source_lang: str, target_lang: str, cfg, online_translations=None) -> str:
        """
        Run Ollama translation, potentially using online translations as reference.
        This ensures Ollama can evaluate and improve upon online translations.
        
        Args:
            text: Text to translate
            source_lang: Source language
            target_lang: Target language
            cfg: Configuration object
            online_translations: Dictionary of online translation results to use as reference
            
        Returns:
            str: Ollama's translation or empty string if failed
        """
        if not cfg.getboolean("ollama", "enabled", fallback=False):
            return ""
            
        server_url = cfg.get("ollama", "server_url", fallback="")
        endpoint_path = cfg.get("ollama", "endpoint", fallback="/api/generate")
        model_name = cfg.get("ollama", "model", fallback="")
        
        if not (server_url and model_name):
            append_log("[WARNING] Ollama is enabled but missing server_url or model_name")
            return ""
            
        # Build a prompt that includes online translations for Ollama to evaluate
        prompt_lines = [
            f"You are an expert translator from {source_lang} to {target_lang}.",
            "",
            f"TEXT TO TRANSLATE: \"{text}\"",
            ""
        ]
        
        if online_translations:
            prompt_lines.append("AVAILABLE TRANSLATIONS FROM ONLINE SERVICES:")
            for service, translation in online_translations.items():
                prompt_lines.append(f"{service}: \"{translation}\"")
            prompt_lines.append("")
            prompt_lines.append("Your task:")
            prompt_lines.append("1. Review the translations from online services")
            prompt_lines.append("2. Select the best one or provide your own improved translation")
            prompt_lines.append("3. The translation should be accurate, natural-sounding, and convey the original meaning")
        else:
            prompt_lines.append("Please translate this text accurately and naturally.")
            
        prompt_lines.append("\nProvide ONLY the translated text with no additional commentary or explanation.")
        prompt = "\n".join(prompt_lines)
        
        temperature = cfg.getfloat("ollama", "temperature", fallback=0.3)
        result = call_ollama(server_url, endpoint_path, model_name, prompt, temperature, cfg)
        
        # Clean up the result - remove any quotes or extra text
        result = result.strip(' "\'\n')
        
        return result

    def call_azure_translate(api_key: str, region: str, text: str, source_lang: str, target_lang: str) -> str:
        """
        Use Azure Translator service for translation.
        """
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling Azure Translator: {source_iso} -> {target_iso}")
        
        endpoint = f"https://api.cognitive.microsofttranslator.com/translate?api-version=3.0"
        
        headers = {
            'Ocp-Apim-Subscription-Key': api_key,
            'Ocp-Apim-Subscription-Region': region,
            'Content-type': 'application/json',
        }
        
        params = {
            'from': source_iso,
            'to': target_iso
        }
        
        body = [{
            'text': text
        }]
        
        try:
            response = requests.post(endpoint, headers=headers, params=params, json=body, timeout=5)
            response.raise_for_status()
            
            result = response.json()
            if result and len(result) > 0 and 'translations' in result[0] and len(result[0]['translations']) > 0:
                translation = result[0]['translations'][0]['text']
                append_log(f"[DEBUG] Azure Translator result: \"{translation}\"")
                return translation
            else:
                append_log(f"[WARNING] Unexpected response format from Azure Translator: {result}")
                return ""
                
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Azure Translator request failed: {e}")
            return ""
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            append_log(f"[ERROR] Failed to parse Azure Translator response: {e}")
            return ""

    def call_yandex_translate(api_key: str, text: str, source_lang: str, target_lang: str) -> str:
        """
        Use Yandex Translate service for translation.
        """
        source_iso = get_iso_code(source_lang)
        target_iso = get_iso_code(target_lang)
        
        append_log(f"[DEBUG] Calling Yandex Translate: {source_iso} -> {target_iso}")
        
        url = "https://translate.api.cloud.yandex.net/translate/v2/translate"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}"
        }
        
        data = {
            "texts": [text],
            "sourceLanguageCode": source_iso,
            "targetLanguageCode": target_iso,
            "format": "PLAIN_TEXT"
        }
        
        try:
            response = requests.post(url, json=data, headers=headers, timeout=5)
            response.raise_for_status()
            
            result = response.json()
            if 'translations' in result and len(result['translations']) > 0:
                translation = result['translations'][0]['text']
                append_log(f"[DEBUG] Yandex Translate result: \"{translation}\"")
                return translation
            else:
                append_log(f"[WARNING] Unexpected response format from Yandex Translate: {result}")
                return ""
                
        except requests.exceptions.RequestException as e:
            append_log(f"[ERROR] Yandex Translate request failed: {e}")
            return ""
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            append_log(f"[ERROR] Failed to parse Yandex Translate response: {e}")
            return ""

    import re
    def extract_json_key(response_text: str, key_name: str) -> str:
        try:
            parsed = json.loads(response_text)
            if (isinstance(parsed, dict) and key_name in parsed):
                return parsed[key_name]
        except json.JSONDecodeError:
            pass
        json_pattern = r'(\{[\s\S]*?%s[\s\S]*?\})' % re.escape(key_name)
        json_matches = re.findall(json_pattern, response_text)
        for potential_json in json_matches:
            try:
                parsed = json.loads(potential_json)
                if (isinstance(parsed, dict) and key_name in parsed):
                    return parsed[key_name]
            except json.JSONDecodeError:
                continue
        pattern = f'"{key_name}"\\s*:\\s*"([^"]+)"'
        match = re.search(pattern, response_text)
        if (match):
            return match.group(1)
        return response_text.strip()

    def build_prompt_for_line(lines, index, cfg, deepl_translation=""):
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after  = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx   = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after  = lines[index+1:end_idx]
        line_to_translate = lines[index]

        prompt_lines = [
            f"You are an expert subtitle translator from {src_lang_full} to {tgt_lang_full}.",
            "You will see a DeepL suggestion that is usually correct, but might miss subtle context.",
            "",
            "GUIDELINES:",
            "1. If DeepL is correct, keep it. Otherwise fix it minimally.",
            "2. Avoid adding extra words or changing the meaning.",
            "3. Do not hallucinate content not in the original.",
            "",
            "--- CONTEXT ---"
        ]

        if (chunk_before):
            prompt_lines.append("[PREVIOUS LINES]:")
            for i, prev_line in enumerate(chunk_before):
                prompt_lines.append(f"Line {start_idx + i + 1}: {prev_line}")

        prompt_lines.append("\n[CURRENT LINE TO TRANSLATE]:")
        prompt_lines.append(f"Line {index+1}: {line_to_translate}")

        if (chunk_after):
            prompt_lines.append("\n[NEXT LINES]:")
            for i, next_line in enumerate(chunk_after):
                prompt_lines.append(f"Line {index + i + 2}: {next_line}")

        prompt_lines.append("--- END CONTEXT ---\n")
        if (deepl_translation):
            prompt_lines.append(f"DEEPL SUGGESTION: \"{deepl_translation}\"")
            prompt_lines.append("If correct or close, use it. If wrong, fix it minimally.")
        else:
            prompt_lines.append("NO DEEPL SUGGESTION AVAILABLE")

        prompt_lines.append("")
        prompt_lines.append("Respond ONLY with JSON in this format: {\"translation\": \"...\"}")
        return "\n".join(prompt_lines)

    def build_critic_prompt(original_line, first_pass_translation, lines, index, cfg):
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after  = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx   = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after  = lines[index+1:end_idx]

        prompt_lines = [
            f"You are a Critic for translations from {src_lang_full} to {tgt_lang_full}.",
            "Review the line in context. If the translation is correct, return it as-is; if not, correct it minimally.",
            "--- CONTEXT ---"
        ]

        if (chunk_before):
            prompt_lines.append("[PREVIOUS LINES]:")
            for i, prev_line in enumerate(chunk_before):
                prompt_lines.append(f"Line {start_idx + i + 1}: {prev_line}")

        prompt_lines.append("\n[CURRENT LINE / FIRST PASS]:")
        prompt_lines.append(f"Original: {original_line}")
        prompt_lines.append(f"Translation: {first_pass_translation}")

        if (chunk_after):
            prompt_lines.append("\n[NEXT LINES]:")
            for i, next_line in enumerate(chunk_after):
                prompt_lines.append(f"Line {index + i + 2}: {next_line}")

        prompt_lines.append("--- END CONTEXT ---\n")
        prompt_lines.append("Respond ONLY with JSON: {\"corrected_translation\": \"...\"}")
        return "\n".join(prompt_lines)

    def build_specialized_critic_prompt(original_line, current_translation, lines, index, cfg, critic_type="general", pass_num=1):
        src_lang_full = cfg.get("general", "source_language", fallback="en")
        tgt_lang_full = cfg.get("general", "target_language", fallback="en")
        context_before = cfg.getint("general", "context_size_before", fallback=10)
        context_after = cfg.getint("general", "context_size_after", fallback=10)

        start_idx = max(0, index - context_before)
        end_idx = min(len(lines), index + context_after + 1)

        chunk_before = lines[start_idx:index]
        chunk_after = lines[index+1:end_idx]

        # Create specialized prompts based on critic type
        if (critic_type.lower() == "standard"):
            prompt_intro = [
                f"You are an expert translator from {src_lang_full} to {tgt_lang_full}.",
                "Carefully review the translation and make sure it accurately captures the meaning of the original.",
                "Only make changes if necessary to improve accuracy or readability.",
                ""
            ]
        elif (critic_type.lower() == "technical_grammar"):
            prompt_intro = [
                f"You are a {tgt_lang_full} language expert focusing exclusively on grammar, syntax, and punctuation.",
                f"Your job is to make minimal edits to ensure the translation follows correct {tgt_lang_full} language rules. Pay special attention to:",
                "- Correct word order in sentences",
                "- Proper use of definite and indefinite articles",
                "- Correct verb conjugation and tense",
                "- Gender agreement in nouns and adjectives",
                "- Proper punctuation",
                "",
                "Only modify the translation if there are grammar errors.",
                ""
            ]
        elif (critic_type.lower() == "cultural"):
            prompt_intro = [
                f"You are a {tgt_lang_full} cultural expert.",
                f"Your job is to make the translation sound natural to native {tgt_lang_full} speakers. Focus on:",
                "- Replacing literal translations with natural expressions",
                f"- Using appropriate {tgt_lang_full} idioms and colloquialisms",
                "- Making dialogue sound authentic and conversational",
                "- Adapting culturally specific references appropriately",
                f"- Ensuring the emotional tone matches {tgt_lang_full} speech patterns",
                "",
                "Don't change correct translations that already sound natural.",
                ""
            ]
        elif (critic_type.lower() == "consistency"):
            prompt_intro = [
                "You are responsible for ensuring consistency across subtitle translations.",
                "Your job is to:",
                "- Ensure character names and important terms are translated consistently",
                "- Maintain consistency in tone and speaking style for each character",
                "- Check that terminology remains consistent throughout",
                "- Ensure that phrases appearing multiple times are translated the same way",
                "- Verify that references to past events match earlier translations",
                "",
                "Only make changes to maintain consistency.",
                ""
            ]
        else:
            # Default generic prompt for any other critic type
            prompt_intro = [
                f"You are a specialized Critic (Pass #{pass_num}, type: {critic_type}).",
                f"Focus on {critic_type}-related issues. Return the same translation if no fix is needed.",
                ""
            ]

        prompt_lines = prompt_intro + ["--- CONTEXT ---"]

        if (chunk_before):
            prompt_lines.append("[PREVIOUS LINES]:")
            for i, prev_line in enumerate(chunk_before):
                prompt_lines.append(f"Line {start_idx + i + 1}: {prev_line}")

        prompt_lines.append("\n[CURRENT LINE / TRANSLATION]:")
        prompt_lines.append(f"Original: {original_line}")
        prompt_lines.append(f"Current Translation: {current_translation}")

        if (chunk_after):
            prompt_lines.append("\n[NEXT LINES]:")
            for i, next_line in enumerate(chunk_after):
                prompt_lines.append(f"Line {index + i + 2}: {next_line}")

        prompt_lines.append("--- END CONTEXT ---\n")
        prompt_lines.append("Respond ONLY with JSON: {\"corrected_translation\": \"...\"}")
        return "\n".join(prompt_lines)

    def run_llm_call(cfg, prompt, temperature):
        ollama_enabled = cfg.getboolean("ollama", "enabled", fallback=False)
        openai_enabled = cfg.getboolean("openai", "enabled", fallback=False)
        
        if (ollama_enabled):
            server_url = cfg.get("ollama", "server_url", fallback="")
            endpoint_path = cfg.get("ollama", "endpoint", fallback="/api/generate")
            model_name = cfg.get("ollama", "model", fallback="")
            if (server_url and model_name):
                return call_ollama(server_url, endpoint_path, model_name, prompt, temperature, cfg)
        elif (openai_enabled):
            api_key = cfg.get("openai", "api_key", fallback="")
            base_url = cfg.get("openai", "api_base_url", fallback="https://api.openai.com/v1")
            model_name = cfg.get("openai", "model", fallback="")
            if (api_key and model_name):
                return call_openai(api_key, base_url, model_name, prompt, temperature)
        
        append_log("[WARNING] No LLM is enabled in config, or missing credentials.")
        return ""

    def sanitize_text(text: str) -> str:
        import re
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        text = re.sub(r'<[^>]*>', '', text)
        text = re.sub(r'\[(.*?)\]', r'#BRACKET_OPEN#\1#BRACKET_CLOSE#', text)
        text = re.sub(r' +', ' ', text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.strip()

    def preprocess_subtitle(text: str) -> str:
        """
        Pre-process subtitle text to normalize and protect special content
        before translation.
        """
        # Handle bracket content consistently
        text = re.sub(r'\[(.*?)\]', r'#BRACKET_OPEN#\1#BRACKET_CLOSE#', text)
        
        # Handle HTML tags properly
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text)
        text = re.sub(r'<[^>]*>', '', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Handle special characters
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        return text.strip()

    def postprocess_translation(text: str) -> str:
        """
        Post-process translated text to restore formatting and fix common issues.
        """
        # Restore brackets
        text = text.replace('#BRACKET_OPEN#', '[').replace('#BRACKET_CLOSE#', ']')
        
        # Fix common Danish punctuation issues
        text = text.replace(' ,', ',').replace(' .', '.')
        text = text.replace(' !', '!').replace(' ?', '?')
        text = text.replace(' :', ':').replace(' ;', ';')
        
        # Ensure proper spacing after punctuation
        text = re.sub(r'([,.!?;:])([^\s])', r'\1 \2', text)
        
        # Fix common spacing issues with quotation marks
        text = re.sub(r'"\s+', '" ', text)
        text = re.sub(r'\s+"', ' "', text)
        
        # Fix capitalization issues
        text = re.sub(r'^([a-zæøå])', lambda m: m.group(1).upper(), text)
        
        # Fix common Danish specific issues
        text = text.replace("Jeg er", "Jeg er").replace("Du er", "Du er")
        
        return text.strip()

    def translate_srt(input_path, output_path, cfg):
        import pysrt
        start_time = time.time()
        from flask import flash

        translation_stats = {
            'source_language': cfg.get("general", "source_language", fallback="en"),
            'target_language': cfg.get("general", "target_language", fallback="en"),
            'total_lines': 0,
            'standard_critic_enabled': cfg.has_section("agent_critic") and cfg.getboolean("agent_critic", "enabled", fallback=False),
            'standard_critic_changes': 0,
            'multi_critic_enabled': cfg.has_section("multi_critic") and cfg.getboolean("multi_critic", "enabled", fallback=False),
            'translations': []
        }

        try:
            subs = pysrt.open(input_path, encoding='utf-8')
            lines = [s.text.strip() for s in subs]
            translation_stats['total_lines'] = len(subs)
            append_log(f"Loaded {len(subs)} subtitle entries from '{os.path.basename(input_path)}'")
        except Exception as e:
            append_log(f"[ERROR] Cannot parse SRT: {e}")
            raise

        src_lang = cfg.get("general", "source_language", fallback="en")
        tgt_lang = cfg.get("general", "target_language", fallback="en")
        
        translation_progress["total_lines"] = len(subs)
        translation_progress["status"] = "translating"

        for i, sub in enumerate(subs):
            append_log(f"Processing line {i+1}/{len(subs)}...")
            original_text = preprocess_subtitle(sub.text)
            
            # Get translations from all enabled services
            service_translations = get_multiple_translations(original_text, src_lang, tgt_lang, cfg)
            
            translation_progress["current_line"] = i+1
            translation_progress["current"] = {
                "line_number": i+1,
                "original": original_text,
                "suggestions": service_translations,
                "first_pass": "",
                "standard_critic": "",
                "standard_critic_changed": False,
                "critics": [],
                "final": "",
                "llm_status": ""  # Added this field to track LLM agent status
            }

            line_stats = {
                'line_number': i+1,
                'original': original_text,
                'reference_translations': service_translations
            }

            # Use DeepL as the reference if available, for backward compatibility
            deepl_suggestion = service_translations.get("DeepL", "")

            # Build first pass prompt
            clean_lines = [sanitize_text(l) for l in lines]
            prompt = build_prompt_for_line(clean_lines, i, cfg, deepl_suggestion)
            
            # Call LLM for first pass translation
            llm_response = run_llm_call(cfg, prompt, cfg.getfloat("general", "temperature", fallback=0.2))

            if (not llm_response):
                sub.text = original_text
                line_stats['first_pass'] = original_text
                line_stats['final'] = original_text
                translation_progress["current"]["first_pass"] = original_text
                translation_progress["current"]["final"] = original_text
                translation_progress["current"]["llm_status"] = "LLM translation failed, using original text"
                translation_stats['translations'].append(line_stats)
                continue

            first_pass = extract_json_key(llm_response, "translation")
            first_pass = postprocess_translation(first_pass)
            
            line_stats['first_pass'] = first_pass
            translation_progress["current"]["first_pass"] = first_pass
            translation_progress["current"]["llm_status"] = "First pass translation completed"

            live_stream_translation_info(
                "FIRST PASS",
                original_text,
                first_pass,
                i+1,
                len(subs),
                deepl_suggestion
            )

            current_translation = first_pass

            # Standard agent_critic
            if (translation_stats['standard_critic_enabled']):
                critic_prompt = build_critic_prompt(original_text, current_translation, clean_lines, i, cfg)
                critic_resp = run_llm_call(cfg, critic_prompt, cfg.getfloat("agent_critic", "temperature", fallback=0.2))
                
                if (critic_resp):
                    corrected = extract_json_key(critic_resp, "corrected_translation")
                    corrected = postprocess_translation(corrected)
                    
                    if (corrected and corrected != current_translation):
                        line_stats['standard_critic'] = corrected
                        line_stats['standard_critic_changed'] = True
                        translation_stats['standard_critic_changes'] += 1
                        translation_progress["current"]["standard_critic"] = corrected
                        translation_progress["current"]["standard_critic_changed"] = True
                        translation_progress["current"]["llm_status"] = "Standard critic improved translation"

                        live_stream_translation_info(
                            "CRITIC",
                            original_text,
                            corrected,
                            i+1,
                            len(subs),
                            None,
                            "standard"
                        )

                        current_translation = corrected
                    else:
                        translation_progress["current"]["standard_critic"] = current_translation
                        translation_progress["current"]["standard_critic_changed"] = False
                        translation_progress["current"]["llm_status"] = "Standard critic: No changes needed"
                        
                        live_stream_translation_info(
                            "CRITIC",
                            original_text,
                            current_translation,
                            i+1,
                            len(subs),
                            None,
                            "standard"
                        )
            
            # Multi-critic passes
            critic_reviews = {}
            if (translation_stats['multi_critic_enabled']):
                for pass_num in range(1, 4):
                    sec = f"critic_pass_{pass_num}"
                    if (cfg.has_section(sec) and cfg.getboolean(sec, "enabled", fallback=False)):
                        ctype = cfg.get(sec, "type", fallback="general")
                        critic_prompt = build_specialized_critic_prompt(
                            original_text, current_translation, clean_lines, i, cfg, critic_type=ctype, pass_num=pass_num
                        )
                        ctemp = cfg.getfloat(sec, "temperature", fallback=0.2)
                        cresp = run_llm_call(cfg, critic_prompt, ctemp)
                        
                        if (cresp):
                            cfix = extract_json_key(cresp, "corrected_translation")
                            cfix = postprocess_translation(cfix)
                            
                            if (cfix and cfix != current_translation):
                                line_stats['critic_{pass_num}'] = cfix
                                line_stats['critic_{pass_num}_changed'] = True
                                line_stats['critic_{pass_num}_type'] = ctype
                                critic_reviews[ctype] = cfix
                                
                                translation_progress["current"]["critics"].append({
                                    "type": ctype, 
                                    "translation": cfix, 
                                    "changed": True
                                })

                                live_stream_translation_info(
                                    f"CRITIC ({ctype})",
                                    original_text,
                                    cfix,
                                    i+1,
                                    len(subs),
                                    None,
                                    ctype
                                )
                                current_translation = cfix
                            else:
                                critic_reviews[ctype] = "No changes"
                                translation_progress["current"]["critics"].append({
                                    "type": ctype, 
                                    "translation": current_translation, 
                                    "changed": False
                                })
                                
                                live_stream_translation_info(
                                    f"CRITIC ({ctype})",
                                    original_text,
                                    current_translation,
                                    i+1,
                                    len(subs),
                                    None,
                                    ctype
                                )
            
            line_stats['critic_reviews'] = critic_reviews
            final_translation = postprocess_translation(current_translation)
            line_stats['final'] = final_translation
            sub.text = final_translation

            translation_progress["current"]["final"] = final_translation

            live_stream_translation_info(
                "FINAL TRANSLATION",
                original_text,
                final_translation,
                i+1,
                len(subs)
            )

            translation_stats['translations'].append(line_stats)
            translation_progress["processed_lines"].append(translation_progress["current"])

        # Save the translated subtitle file
        subs.save(output_path, encoding='utf-8')
        append_log(f"[INFO] Saved translated SRT to '{os.path.basename(output_path)}'")
        
        # Generate a translation report
        report_path = os.path.join(os.path.dirname(output_path), "translation_report.txt")
        generate_translation_report(translation_stats, report_path)
        append_log(f"[INFO] Saved detailed translation report to {report_path}")
        
        end_time = time.time()
        translation_stats['processing_time'] = end_time - start_time
        processing_time_seconds = end_time - start_time
        processing_time_minutes = processing_time_seconds / 60
        
        # Print final statistics
        append_log("\n[INFO] TRANSLATION STATISTICS:")
        append_log(f"- Total lines translated: {len(subs)}")
        append_log(f"- Translation services used: {', '.join(service_translations.keys() if service_translations else ['None'])}")
        if (translation_stats['standard_critic_enabled']):
            critic_changes = translation_stats['standard_critic_changes']
            append_log(f"- Lines improved by standard critic: {critic_changes} ({(critic_changes/len(subs))*100:.1f}%)")
        append_log(f"- Total processing time: {processing_time_minutes:.2f} minutes ({processing_time_seconds:.2f} seconds)")
        append_log(f"- Average time per line: {processing_time_seconds/len(subs):.2f} seconds")

        translation_progress["status"] = "done"
        translation_progress["message"] = "Translation complete."

    def generate_translation_report(stats, output_path):
        """Generate a detailed translation report with comprehensive statistics"""
        with open(output_path, 'w', encoding='utf-8') as f:
            # Report header
            f.write("="*80 + "\n")
            f.write("SUBTITLE TRANSLATION REPORT\n")
            f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("-"*80 + "\n")
            if ('input_file' in stats):
                f.write(f"Input file: {stats['input_file']}\n")
            if ('output_file' in stats):
                f.write(f"Output file: {stats['output_file']}\n")
            f.write(f"Source language: {stats['source_language']}\n")
            f.write(f"Target language: {stats['target_language']}\n")
            f.write("="*80 + "\n\n")
            
            # Translation details for each line
            f.write("TRANSLATION DETAILS\n")
            f.write("-"*80 + "\n")
            
            for entry in stats['translations']:
                line_num = entry['line_number']
                f.write(f"Line {line_num}:\n")
                f.write(f"  Original: \"{entry['original']}\"\n")
                
                # Show all machine translations (DeepL, Google, LibreTranslate, etc.)
                if ('reference_translations' in entry and entry['reference_translations']):
                    for service, translation in entry['reference_translations'].items():
                        f.write(f"  {service}: \"{translation}\"\n")
                
                # Show first pass translation
                if ('first_pass' in entry):
                    f.write(f"  First pass: \"{entry['first_pass']}\"\n")
                
                # Show standard critic result if available
                if ('standard_critic' in entry):
                    if (entry.get('standard_critic_changed', False)):
                        f.write(f"  Critic: \"{entry['standard_critic']}\" (CHANGED)\n")
                    else:
                        f.write(f"  Critic: No changes\n")
                
                # Show specialized critic results if available
                for i in range(1, 4):
                    critic_key = f'critic_{i}'
                    critic_type_key = f'critic_{i}_type'
                    
                    if (critic_key in entry):
                        critic_type = entry.get(critic_type_key, f"Type {i}")
                        if (entry.get(f'{critic_key}_changed', False)):
                            f.write(f"  Critic {i} ({critic_type}): \"{entry[critic_key]}\" (CHANGED)\n")
                        else:
                            f.write(f"  Critic {i} ({critic_type}): No changes\n")
                
                # Show final translation
                if ('final' in entry):
                    f.write(f"  Final: \"{entry['final']}\"\n")
                
                f.write("-"*60 + "\n")
            
            # Enhanced summary statistics
            f.write("\nSUMMARY STATISTICS\n")
            f.write("-"*80 + "\n")
            f.write(f"Total lines translated: {stats['total_lines']}\n\n")
            
            # Translation service usage and metrics
            service_usage = {}
            service_similarity = {}
            service_selected = {}
            first_pass_matches = {}
            
            if ('translations' in stats and stats['translations']):
                # Count how many times each service was used and analyze similarity
                for entry in stats['translations']:
                    if ('reference_translations' in entry and entry['reference_translations']):
                        for service, translation in entry['reference_translations'].items():
                            # Track service usage
                            service_usage[service] = service_usage.get(service, 0) + 1
                            
                            # Count how often first pass matches each service
                            if ('first_pass' in entry and entry['first_pass'] == translation):
                                first_pass_matches[service] = first_pass_matches.get(service, 0) + 1
                            
                            # Count how often final translation matches each service
                            if ('final' in entry and entry['final'] == translation):
                                service_selected[service] = service_selected.get(service, 0) + 1
                            
                            # Calculate similarity between service and final translation
                            if ('final' in entry):
                                # Simple character-based similarity
                                service_trans = translation.strip()
                                final_trans = entry['final'].strip()
                                
                                # Basic Levenshtein distance calculation (character-level similarity)
                                max_len = max(len(service_trans), len(final_trans))
                                if (max_len > 0):
                                    from difflib import SequenceMatcher
                                    similarity = SequenceMatcher(None, service_trans, final_trans).ratio() * 100
                                    
                                    if (service not in service_similarity):
                                        service_similarity[service] = []
                                    service_similarity[service].append(similarity)
                
                # Print service usage statistics
                f.write("\nTRANSLATION SERVICE STATISTICS:\n")
                f.write("-"*40 + "\n")
                
                for service in sorted(service_usage.keys()):
                    usage_count = service_usage[service]
                    usage_percent = (usage_count / stats['total_lines']) * 100
                    f.write(f"{service}:\n")
                    f.write(f"  - Available for {usage_count} lines ({usage_percent:.1f}%)\n")
                    
                    # How often the LLM's first pass matched this service
                    if (service in first_pass_matches):
                        match_percent = (first_pass_matches[service] / usage_count) * 100
                        f.write(f"  - First pass matched exactly: {first_pass_matches[service]} times ({match_percent:.1f}%)\n")
                    
                    # How often this service's translation was selected as final
                    if (service in service_selected):
                        selected_percent = (service_selected[service] / usage_count) * 100
                        f.write(f"  - Selected as final translation: {service_selected[service]} times ({selected_percent:.1f}%)\n")
                    
                    # Average similarity to final translation
                    if (service in service_similarity and service_similarity[service]):
                        avg_similarity = sum(service_similarity[service]) / len(service_similarity[service])
                        f.write(f"  - Average similarity to final translation: {avg_similarity:.1f}%\n")
                    
                    f.write("\n")
            
            # LLM first pass statistics
            f.write("\nLLM FIRST PASS STATISTICS:\n")
            f.write("-"*40 + "\n")
            
            first_pass_unchanged = 0
            first_pass_modified = 0
            
            for entry in stats['translations']:
                if ('first_pass' in entry and 'final' in entry):
                    if (entry['first_pass'] == entry['final']):
                        first_pass_unchanged += 1
                    else:
                        first_pass_modified += 1
            
            total_with_first_pass = first_pass_unchanged + first_pass_modified
            if (total_with_first_pass > 0):
                unchanged_percent = (first_pass_unchanged / total_with_first_pass) * 100
                modified_percent = (first_pass_modified / total_with_first_pass) * 100
                
                f.write(f"First pass translations used without changes: {first_pass_unchanged} ({unchanged_percent:.1f}%)\n")
                f.write(f"First pass translations modified by critics: {first_pass_modified} ({modified_percent:.1f}%)\n\n")
            
            # Standard critic statistics
            if (stats.get('standard_critic_enabled', False)):
                f.write("\nSTANDARD CRITIC STATISTICS:\n")
                f.write("-"*40 + "\n")
                
                critic_changes = stats.get('standard_critic_changes', 0)
                percentage = (critic_changes / stats['total_lines']) * 100 if stats['total_lines'] > 0 else 0
                f.write(f"Standard Critic changes: {critic_changes} ({percentage:.1f}%)\n")
                f.write(f"Standard Critic pass rate: {stats['total_lines'] - critic_changes} lines left unchanged ({100 - percentage:.1f}%)\n\n")
            
            # Multi-critic statistics
            if (stats.get('multi_critic_enabled', False)):
                f.write("\nMULTI-CRITIC STATISTICS:\n")
                f.write("-"*40 + "\n")
                
                # Count how many changes each specialized critic made
                critic_changes = {}
                critic_total = {}
                
                for i in range(1, 4):
                    critic_key = f'critic_{i}'
                    critic_type_key = f'critic_{i}_type'
                    changes = 0
                    total = 0
                    
                    for entry in stats['translations']:
                        if (critic_key in entry):
                            total += 1
                            critic_type = entry.get(critic_type_key, f"Type {i}")
                            
                            if (entry.get(f'{critic_key}_changed', False)):
                                changes += 1
                                critic_changes[critic_type] = critic_changes.get(critic_type, 0) + 1
                            
                            critic_total[critic_type] = critic_total.get(critic_type, 0) + 1
                
                for critic_type in sorted(critic_total.keys()):
                    total = critic_total[critic_type]
                    changes = critic_changes.get(critic_type, 0)
                    percentage = (changes / total) * 100 if total > 0 else 0
                    f.write(f"Critic '{critic_type}':\n")
                    f.write(f"  - Changes made: {changes} ({percentage:.1f}%)\n")
                    f.write(f"  - Lines left unchanged: {total - changes} ({100 - percentage:.1f}%)\n\n")
                
                # Calculate which critic was most active
                if (critic_changes):
                    most_active = max(critic_changes.items(), key=lambda x: x[1])
                    f.write(f"Most active critic: '{most_active[0]}' with {most_active[1]} changes\n\n")
            
            # Word-level statistics
            total_source_words = 0
            total_target_words = 0
            
            for entry in stats['translations']:
                if ('original' in entry):
                    total_source_words += len(entry['original'].split())
                if ('final' in entry):
                    total_target_words += len(entry['final'].split())
            
            if (total_source_words > 0):
                expansion_ratio = (total_target_words / total_source_words) * 100
                f.write("\nWORD-LEVEL STATISTICS:\n")
                f.write("-"*40 + "\n")
                f.write(f"Total source words: {total_source_words}\n")
                f.write(f"Total target words: {total_target_words}\n")
                f.write(f"Target/Source ratio: {expansion_ratio:.1f}%\n\n")
            
            # Processing time
            if ('processing_time' in stats):
                processing_time = stats['processing_time']
                minutes = int(processing_time // 60)
                seconds = processing_time % 60
                
                f.write("\nPROCESSING TIME STATISTICS:\n")
                f.write("-"*40 + "\n")
                f.write(f"Total processing time: {minutes}m {seconds:.2f}s\n")
                if (stats['total_lines'] > 0):
                    f.write(f"Average time per line: {processing_time / stats['total_lines']:.2f} seconds\n")
                    if (total_source_words > 0):
                        f.write(f"Average time per word: {processing_time / total_source_words:.2f} seconds\n\n")
            
            f.write("="*80 + "\n")
            f.write("\nNOTE: Similarity metrics are approximate and based on character-level comparison.\n")
            f.write("Higher similarity percentages indicate closer matches between service translations and final output.\n")

    app = Flask(__name__)
    app.secret_key = os.urandom(24)

    # Updated INDEX_PAGE with a large console box
    INDEX_PAGE = r"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>SRT Subtitle Translator</title>
        <style>
            body { 
                font-family: sans-serif; 
                margin: 0; 
                padding: 0; 
                background-color: #1e1e2e; 
                color: #cdd6f4;
            }
            .container {
                max-width: 1200px;
                margin: 2em auto;
                background: #282a36;
                padding: 2em;
                border-radius: 8px;
                box-shadow: 0 4px 10px rgba(0,0,0,0.3);
            }
            h1 { text-align: center; color: #f5f5f5; margin-top: 0; }
            label { display: block; margin-bottom: 0.5em; font-weight: bold; }
            input[type="file"] { 
                border: 1px solid #444; 
                padding: 0.5em; 
                width: calc(100% - 1.2em); 
                margin-bottom: 1em; 
                background-color: #1a1a24;
                color: #cdd6f4;
            }
            input[type="submit"] {
                background-color: #74c7ec; 
                color: #1e1e2e; 
                padding: 0.8em 1.5em;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 1em;
                width: 100%;
                font-weight: bold;
            }
            input[type="submit"]:hover { background-color: #89dceb; }
            .status {
                margin-top: 1em;
                padding: 1em;
                background-color: #313244;
                border-left: 5px solid #89b4fa;
                display: none;
            }
            .error {
                margin-top: 1em;
                padding: 1em;
                background-color: #382a37;
                border-left: 5px solid #f38ba8;
                color: #f38ba8;
            }
            .progress-box {
                margin-top: 2em;
                background: #313244;
                border-radius: 6px;
                padding: 1em;
                border: 1px solid #444;
                font-size: 0.95em;
            }
            #console-box {
                margin-top: 2em;
                background-color: #1a1a24;
                color: #cdd6f4;
                border-radius: 6px;
                padding: 1em;
                height: 40vh;
                overflow-y: auto;
                white-space: pre-wrap;
                font-family: monospace;
                font-size: 0.9em;
                border: 1px solid #444;
            }
            .error-line { color: #f38ba8; }
            .warn-line { color: #f9e2af; }
            .info-line { color: #89b4fa; }
            .debug-line { color: #a6e3a1; }
            .progress-line { color: #cba6f7; }
            .settings-panel {
                margin-top: 2em;
                padding: 1em;
                background-color: #313244;
                border-radius: 6px;
                border: 1px solid #444;
            }
            .settings-panel h3 {
                margin-top: 0;
                color: #f5f5f5;
                border-bottom: 1px solid #444;
                padding-bottom: 0.5em;
            }
            .quick-settings {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 1em;
            }
            .setting-card {
                flex: 1;
                min-width: 200px;
                padding: 1em;
                background: #1a1a24;
                border-radius: 4px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.3);
                border: 1px solid #444;
            }
            .setting-card h4 {
                margin-top: 0;
                color: #cdd6f4;
            }
            .setting-card input, .setting-card select {
                width: 100%;
                padding: 0.5em;
                margin-top: 0.3em;
                border: 1px solid #444;
                border-radius: 4px;
                background-color: #282a36;
                color: #cdd6f4;
            }
            .setting-card label {
                font-weight: normal;
                font-size: 0.9em;
                color: #bac2de;
            }
            .setting-card input[type="checkbox"] {
                width: auto;
                margin-right: 0.5em;
            }
            .btn-save {
                background-color: #74c7ec;
                color: #1e1e2e;
                border: none;
                padding: 0.6em 1em;
                border-radius: 4px;
                cursor: pointer;
                font-size: 0.9em;
                margin-top: 1em;
                font-weight: bold;
            }
            .btn-save:hover {
                background-color: #89dceb;
            }
            .nav-links {
                text-align: center;
                margin-bottom: 1.5em;
            }
            .nav-links a {
                display: inline-block;
                padding: 0.5em 1em;
                margin: 0 0.5em;
                color: #89b4fa;
                text-decoration: none;
                border-radius: 4px;
            }
            .nav-links a:hover {
                background-color: #313244;
                text-decoration: underline;
            }
            .nav-links a.active {
                font-weight: bold;
                border-bottom: 2px solid #89b4fa;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>SRT Subtitle Translator</h1>
            
            <!-- Navigation Links -->
            <div class="nav-links">
                <a href="/" class="active">Home</a>
                <a href="/config">Full Configuration</a>
                <a href="/logs">Log Viewer</a>
            </div>
            
            <form action="/upload" method="POST" enctype="multipart/form-data" id="uploadForm">
                <label for="srtfile">Select SRT File:</label>
                <input type="file" id="srtfile" name="srtfile" accept=".srt" required>
                <input type="submit" value="Upload & Translate">
            </form>

            <hr style="margin:1.5em 0">

            <label for="scan_path">…or translate an entire folder:</label>
            <input type="text" id="scan_path" name="scan_path"
                   placeholder="/tv/Kipo and the Age of Wonderbeasts" 
                   style="border:1px solid #444;padding:.5em;width:calc(100% - 1.2em); background-color: #1a1a24; color: #cdd6f4; margin-bottom: 1em;">

            <input type="button" id="scan_button" value="Scan & Translate Folder"
                   style="margin-top:1em;background-color:#a6e3a1; color: #1e1e2e; padding: 0.8em 1.5em; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; width: 100%; font-weight: bold;">

            <div id="status" class="status">Processing... Please wait. This may take several minutes.</div>
            
            <div id="progress-box" class="progress-box" style="display:none"></div>
            
            <div id="console-box">Logs will appear here when translation starts...</div>

            <!-- Subtitle Archive Section -->
            <div class="settings-panel" style="margin-top: 2em;">
                <h3>Subtitle Archive (./subs/)</h3>
                <div id="subs-archive-list" style="max-height: 300px; overflow-y: auto; background: #1a1a24; padding: 1em; border-radius: 6px; border: 1px solid #444;">
                    Loading archive...
                </div>
                <button class="btn-save" id="refresh-subs-btn" style="margin-top: 1em; background-color: #89b4fa;">Refresh List</button>
            </div>
            
            <!-- Quick Settings Panel -->
            <div class="settings-panel">
                <h3>Quick Settings</h3>
                <div class="quick-settings">
                    <div class="setting-card">
                        <h4>Ollama Performance</h4>
                        <label>
                            Number of GPUs:
                            <input type="number" id="num_gpu" min="0" max="8" value="1">
                        </label>
                        <label>
                            CPU Threads:
                            <input type="number" id="num_thread" min="1" max="32" value="4">
                        </label>
                        <label>
                            <input type="checkbox" id="use_mmap" checked>
                            Memory-map model (better performance)
                        </label>
                        <label>
                            <input type="checkbox" id="use_mlock" checked>
                            Lock model in RAM (prevents swapping)
                        </label>
                    </div>
                    <div class="setting-card">
                        <h4>Translation Models</h4>
                        <label>
                            Ollama Model:
                            <select id="ollama_model">
                                <option value="gemma3:12b">gemma3:12b</option>
                                <option value="llama2">llama2</option>
                                <option value="llama2:13b">llama2:13b</option>
                                <option value="mistral">mistral</option>
                                <option value="mixtral">mixtral</option>
                                <option value="phi">phi</option>
                            </select>
                        </label>
                        <label>
                            <input type="checkbox" id="ollama_enabled" checked>
                            Enable Ollama
                        </label>
                        <label>
                            Server URL:
                            <input type="text" id="server_url" value="http://localhost:11434">
                        </label>
                    </div>
                    <div class="setting-card">
                        <h4>Language Settings</h4>
                        <label>
                            Source Language:
                            <select id="source_language">
                                <option value="en">English</option>
                                <option value="es">Spanish</option>
                                <option value="fr">French</option>
                                <option value="de">German</option>
                                <option value="it">Italian</option>
                                <option value="ja">Japanese</option>
                                <option value="ko">Korean</option>
                                <option value="zh">Chinese</option>
                                <option value="ru">Russian</option>
                            </select>
                        </label>
                        <label>
                            Target Language:
                            <select id="target_language">
                                <option value="da">Danish</option>
                                <option value="en">English</option>
                                <option value="es">Spanish</option>
                                <option value="fr">French</option>
                                <option value="de">German</option>
                                <option value="it">Italian</option>
                                <option value="ja">Japanese</option>
                                <option value="ko">Korean</option>
                                <option value="zh">Chinese</option>
                                <option value="ru">Russian</option>
                            </select>
                        </label>
                    </div>
                </div>
                <button class="btn-save" id="save-settings">Save Quick Settings</button>
            </div>

            <script>
                const form = document.getElementById('uploadForm');
                const status = document.getElementById('status');
                const progressBox = document.getElementById('progress-box');
                const consoleBox = document.getElementById('console-box');
                let scrollBottom = true;
                
                // For the quick settings panel
                const saveSettingsBtn = document.getElementById('save-settings');
                
                // Handle scroll behavior
                consoleBox.addEventListener('scroll', function() {
                    // Calculate if we're near the bottom
                    const isAtBottom = Math.abs(
                        (consoleBox.scrollHeight - consoleBox.clientHeight) - 
                        consoleBox.scrollTop
                    ) < 30;
                    scrollBottom = isAtBottom;
                });
                
                form.addEventListener('submit', function(e) {
                    e.preventDefault();
                    
                    const formData = new FormData(form);
                    const xhr = new XMLHttpRequest();
                    
                    xhr.open('POST', form.action);
                    xhr.onreadystatechange = function() {
                        if (xhr.readyState === 4) {
                            if (xhr.status === 200) {
                                // Redirect to download page
                                window.location.href = xhr.responseURL;
                            } else {
                                status.textContent = 'Error during translation. Check console for details.';
                                status.style.display = 'block';
                            }
                        }
                    };
                    
                    status.textContent = 'Processing... Please wait. This may take several minutes.';
                    status.style.display = 'block';
                    progressBox.style.display = 'block';
                    
                    xhr.send(formData);
                });
                
                function colorizeLogs(logText) {
                    if (!logText) return "No logs available";
                    
                    return logText
                        .replace(/\[ERROR\].*$/gm, match => `<span class="error-line">${match}</span>`)
                        .replace(/\[WARNING\].*$/gm, match => `<span class="warn-line">${match}</span>`)
                        .replace(/\[INFO\].*$/gm, match => `<span class="info-line">${match}</span>`)
                        .replace(/\[DEBUG\].*$/gm, match => `<span class="debug-line">${match}</span>`)
                        .replace(/\[LIVE\].*$/gm, match => `<span class="progress-line">${match}</span>`);
                }
                
                function pollProgress() {
                    fetch('/api/progress')
                        .then(r => r.json())
                        .then(progress => {
                            if (progress.mode === "bulk") {
                                // Handle bulk mode progress
                                if (progress.status === 'queued') {
                                    progressBox.innerHTML = `<strong>Bulk translation queued...</strong>`;
                                } else if (progress.status === 'translating') {
                                    const percent = progress.total_files > 0 ? Math.round((progress.done_files / progress.total_files) * 100) : 0;
                                    progressBox.innerHTML = `
                                        <strong>Bulk Translating: ${progress.done_files} / ${progress.total_files} files (${percent}%)</strong><br>
                                        Current file: ${progress.current_file || 'Starting...'}<br>
                                        <div style="background: #45475a; height: 20px; border-radius: 4px; margin: 10px 0;">
                                            <div style="background: #89b4fa; height: 100%; width: ${percent}%; border-radius: 4px;"></div>
                                        </div>
                                    `;
                                } else if (progress.status === 'done') {
                                    // Bulk finished - show download link
                                    progressBox.innerHTML = `
                                        <strong style="color:#a6e3a1;">✓ Bulk translation complete!</strong><br>
                                        ${progress.message}<br><br>
                                        ${progress.zip_path ? 
                                            `<a href="/download-zip?temp=${encodeURIComponent(progress.zip_path)}"
                                               style="color:#89b4fa; background-color: #313244; padding: 0.5em 1em; border-radius: 4px; text-decoration: none;">Download all translated subtitles (.zip)</a>` : 
                                            '<span style="color:#f9e2af;">No files were translated or zipped.</span>'
                                        }
                                    `;
                                }
                            } else {
                                // Handle single file mode progress (existing logic)
                                if (progress.status === 'idle') {
                                    progressBox.textContent = 'Waiting for translation to start...';
                                } else if (progress.status === 'translating') {
                                    const percent = progress.total_lines > 0 ? Math.round((progress.current_line / progress.total_lines) * 100) : 0;
                                    // ... (rest of the existing single file progress rendering logic) ...
                                    // Keep the detailed view for single files
                                    progressBox.innerHTML = `
                                        <strong>Translating: ${progress.current_line} / ${progress.total_lines} lines (${percent}%)</strong><br>
                                        <div style="background: #45475a; height: 20px; border-radius: 4px; margin: 10px 0;">
                                            <div style="background: #89b4fa; height: 100%; width: ${percent}%; border-radius: 4px;"></div>
                                        </div>
                                        ${progress.current.line_number > 0 ? `
                                            <h4>Current Line (#${progress.current.line_number})</h4>
                                            <div style="padding: 10px; border-left: 3px solid #89b4fa; margin-bottom: 15px; background: #1a1a24; border-radius: 4px;">
                                                <strong>Original:</strong> ${progress.current.original}<br>
                                                
                                                <div style="margin-top: 8px;">
                                                    <strong>Online Translations:</strong><br>
                                                    ${Object.entries(progress.current.suggestions || {}).map(([service, text]) => 
                                                        `<div style="margin-left: 10px; margin-bottom: 5px;"><em>${service}:</em> ${text}</div>`
                                                    ).join('')}
                                                </div>
                                                
                                                ${progress.current.first_pass ? `
                                                    <div style="margin-top: 8px;">
                                                        <strong>First Pass:</strong> ${progress.current.first_pass}<br>
                                                    </div>
                                                ` : ''}
                                                
                                                ${progress.current.standard_critic ? `
                                                    <div style="margin-top: 8px;">
                                                        <strong>Standard Critic:</strong> ${progress.current.standard_critic} 
                                                        ${progress.current.standard_critic_changed ? 
                                                            '<span style="color: #f38ba8;">(Changed)</span>' : 
                                                            '<span style="color: #a6e3a1;">(No Change)</span>'}
                                                    </div>
                                                ` : ''}
                                                
                                                ${progress.current.critics && progress.current.critics.length > 0 ? `
                                                    <div style="margin-top: 8px;">
                                                        <strong>Specialized Critics:</strong><br>
                                                        ${progress.current.critics.map(critic => 
                                                            `<div style="margin-left: 10px; margin-bottom: 5px;">
                                                                <em>${critic.type}:</em> ${critic.translation} 
                                                                ${critic.changed ? 
                                                                    '<span style="color: #f38ba8;">(Changed)</span>' : 
                                                                    '<span style="color: #a6e3a1;">(No Change)</span>'}
                                                            </div>`
                                                        ).join('')}
                                                    </div>
                                                ` : ''}
                                                
                                                ${progress.current.final ? `
                                                    <div style="margin-top: 8px;">
                                                        <strong>Final Translation:</strong> ${progress.current.final}<br>
                                                    </div>
                                                ` : ''}
                                                
                                                ${progress.current.llm_status ? `
                                                    <div style="margin-top: 8px; color: #bac2de;">
                                                        <strong>Status:</strong> ${progress.current.llm_status}
                                                    </div>
                                                ` : ''}
                                            </div>
                                        ` : ''}
                                        
                                        <h4>Translation History (Last 10)</h4>
                                        <div style="max-height: 300px; overflow-y: auto; border: 1px solid #444; padding: 10px; border-radius: 4px; background: #1a1a24;">
                                            ${progress.processed_lines && progress.processed_lines.length > 0 ? 
                                                progress.processed_lines.slice(-10).reverse().map(line => `
                                                    <div style="margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #444;">
                                                        <h5 style="margin-top: 0; margin-bottom: 10px; background: #313244; padding: 5px; border-radius: 3px;">Line #${line.line_number}</h5>
                                                        
                                                        <div style="padding-left: 10px; border-left: 3px solid #444;">
                                                            <div style="margin-bottom: 10px;">
                                                                <strong>Original:</strong> <span style="color: #cdd6f4;">${line.original}</span>
                                                            </div>
                                                            
                                                            <!-- Final translation -->
                                                            <div style="margin-bottom: 5px; font-weight: bold; background: #313244; padding: 5px; border-left: 3px solid #a6e3a1; border-radius: 3px;">
                                                                <strong>Final Translation:</strong> ${line.final}
                                                            </div>
                                                        </div>
                                                    </div>
                                                `).join('') : 
                                                '<div style="color: #bac2de; text-align: center; padding: 10px;">No translated lines yet</div>'
                                            }
                                        </div>
                                    `;
                                } else if (progress.status === 'done') {
                                    // Single file finished
                                    progressBox.innerHTML = `
                                        <strong style="color:#a6e3a1;">✓ Translation Complete!</strong><br>
                                        Total lines: ${progress.total_lines}<br>
                                        ${progress.message || ''}
                                    `;
                                    // Note: Download link is handled by the /upload route redirect for single files
                                }
                            }
                        })
                        .catch(err => {
                            console.error("Error fetching progress:", err);
                            progressBox.textContent = 'Error fetching progress. Check console.';
                        });
                }
                
                // Load current config values when page loads
                function loadConfigValues() {
                    fetch('/api/config')
                        .then(r => r.json())
                        .then(data => {
                            const config = data.config;
                            
                            // Ollama settings
                            if (config.ollama) {
                                document.getElementById('num_gpu').value = config.ollama.num_gpu || 1;
                                document.getElementById('num_thread').value = config.ollama.num_thread || 4;
                                document.getElementById('use_mmap').checked = config.ollama.use_mmap !== false;
                                document.getElementById('use_mlock').checked = config.ollama.use_mlock !== false;
                                document.getElementById('ollama_enabled').checked = config.ollama.enabled === true;
                                document.getElementById('server_url').value = config.ollama.server_url || 'http://localhost:11434';
                                
                                // Set model if it exists
                                if (config.ollama.model) {
                                    const modelSelect = document.getElementById('ollama_model');
                                    
                                    // Check if the model is in the list
                                    let found = false;
                                    for (let i = 0; i < modelSelect.options.length; i++) {
                                        if (modelSelect.options[i].value === config.ollama.model) {
                                            modelSelect.selectedIndex = i;
                                            found = true;
                                            break;
                                        }
                                    }
                                    
                                    // If not found, add it
                                    if (!found) {
                                        const option = document.createElement('option');
                                        option.value = config.ollama.model;
                                        option.textContent = config.ollama.model;
                                        option.selected = true;
                                        modelSelect.appendChild(option);
                                    }
                                }
                            }
                            
                            // Language settings
                            if (config.general) {
                                const srcLang = config.general.source_language || 'en';
                                const tgtLang = config.general.target_language || 'da';
                                
                                // Set source language
                                const srcSelect = document.getElementById('source_language');
                                for (let i = 0; i < srcSelect.options.length; i++) {
                                    if (srcSelect.options[i].value === srcLang) {
                                        srcSelect.selectedIndex = i;
                                        break;
                                    }
                                }
                                
                                // Set target language
                                const tgtSelect = document.getElementById('target_language');
                                for (let i = 0; i < tgtSelect.options.length; i++) {
                                    if (tgtSelect.options[i].value === tgtLang) {
                                        tgtSelect.selectedIndex = i;
                                        break;
                                    }
                                }
                            }
                        })
                        .catch(err => {
                            console.error("Error loading config:", err);
                        });
                }
                
                // Save quick settings
                saveSettingsBtn.addEventListener('click', function() {
                    // Gather settings from form
                    const updatedConfig = {
                        ollama: {
                            num_gpu: parseInt(document.getElementById('num_gpu').value, 10),
                            num_thread: parseInt(document.getElementById('num_thread').value, 10),
                            use_mmap: document.getElementById('use_mmap').checked,
                            use_mlock: document.getElementById('use_mlock').checked,
                            enabled: document.getElementById('ollama_enabled').checked,
                            server_url: document.getElementById('server_url').value,
                            model: document.getElementById('ollama_model').value
                        },
                        general: {
                            source_language: document.getElementById('source_language').value,
                            target_language: document.getElementById('target_language').value
                        }
                    };
                    
                    // Save to config
                    fetch('/api/config', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ config: updatedConfig })
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (data.success) {
                            alert('Settings saved successfully!');
                        } else {
                            alert('Error saving settings: ' + (data.message || 'Unknown error'));
                        }
                    })
                    .catch(err => {
                        console.error("Error saving settings:", err);
                        alert('Error saving settings: ' + err.message);
                    });
                });
                
                // Initialize page
                loadConfigValues();

                // ─── NEW: start bulk job ─────────────────────────────────────────
                document.getElementById("scan_button").addEventListener("click", () => {
                    const path = document.getElementById("scan_path").value.trim();
                    if (!path) { alert("Please type a folder path first."); return; }

                    fetch("/api/start-scan", {
                        method : "POST",
                        headers: { "Content-Type": "application/json" },
                        body   : JSON.stringify({ path })
                    })
                    .then(r => r.json())
                    .then(j => {
                        if (j.ok) {
                            status.textContent = "Scanning and translating…";
                            progressBox.style.display = "block";
                            consoleBox.innerHTML = "Scanning folder... Logs will appear shortly."; // Initial message
                        } else {
                            alert("Error starting scan: " + j.error);
                        }
                    })
                    .catch(e => alert("Error starting scan: " + e));
                });
                // ──────────────────────────────────────────────────────────────────

                function pollLogs() {
                    fetch('/api/logs')
                      .then(r => r.json())
                      .then(data => {
                        const colored = colorizeLogs(data.logs);
                        consoleBox.innerHTML = colored;
                        if (scrollBottom) {
                            consoleBox.scrollTop = consoleBox.scrollHeight;
                        }
                      })
                      .catch(err => {
                        console.error("Error fetching logs:", err);
                      });
                }

                // Poll progress and logs
                setInterval(pollProgress, 2000);
                setInterval(pollLogs, 2000);

                // Fetch and display subtitle archive
                function fetchSubsArchive() {
                    console.log("[Archive] Fetching subtitle list..."); // Added log
                    subsArchiveList.innerHTML = 'Loading archive...';
                    fetch('/api/list_subs')
                        .then(response => { // Changed to handle response object first
                            console.log("[Archive] Received response status:", response.status); // Added log
                            if (!response.ok) {
                                // Try to get error text from response body
                                return response.text().then(text => {
                                    throw new Error(`Failed to list subs: ${response.status} ${response.statusText} - ${text}`);
                                });
                            }
                            return response.json(); // Parse JSON only if response is ok
                        })
                        .then(data => {
                            console.log("[Archive] Received data:", data); // Added log
                            if (data.error) {
                                subsArchiveList.innerHTML = `<span style="color:#f38ba8;">Error loading archive: ${data.error}</span>`;
                                console.error("[Archive] Server returned error:", data.error); // Added log
                                return;
                            }
                            if (data.files && data.files.length > 0) {
                                // New improved list with buttons
                                let listHtml = '<ul style="list-style-type: none; padding: 0; margin: 0;">';
                                data.files.forEach(filename => {
                                    const downloadUrl = `/download_sub/${encodeURIComponent(filename)}`;
                                    listHtml += `
                                        <li style="display: flex; justify-content: space-between; align-items: center; padding: 8px; margin-bottom: 8px; background-color: #282a36; border-radius: 4px; border: 1px solid #444;">
                                            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 10px;">${filename}</span>
                                            <div>
                                                <a href="${downloadUrl}" download style="background-color: #89b4fa; color: #1e1e2e; text-decoration: none; padding: 5px 10px; border-radius: 3px; font-size: 0.9em; margin-right: 5px;">Download</a>
                                                <button class="delete-btn" data-filename="${filename}" style="background-color: #f38ba8; color: #1e1e2e; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer; font-size: 0.9em;">Delete</button>
                                            </div>
                                        </li>`;
                                });
                                listHtml += '</ul>';
                                subsArchiveList.innerHTML = listHtml;
                                
                                // Add event listeners for delete buttons
                                document.querySelectorAll('.delete-btn').forEach(btn => {
                                    btn.addEventListener('click', function() {
                                        const filename = this.getAttribute('data-filename');
                                        if (confirm(`Are you sure you want to delete "${filename}"?`)) {
                                            deleteSubtitleFile(filename);
                                        }
                                    });
                                });
                                
                                console.log("[Archive] Successfully updated archive list."); // Added log
                            } else {
                                subsArchiveList.innerHTML = 'No subtitles found in the archive.';
                                console.log("[Archive] No subtitle files found."); // Added log
                            }
                        })
                        .catch(err => {
                            console.error("[Archive] Error fetching or processing subs archive:", err); // Enhanced log
                            subsArchiveList.innerHTML = `<span style="color:#f38ba8;">Error loading archive. Check browser console (F12) for details.</span>`;
                        });
                }

                // Function to delete a subtitle file
                function deleteSubtitleFile(filename) {
                    console.log(`[Archive] Attempting to delete file: ${filename}`);
                    fetch(`/api/delete_sub/${encodeURIComponent(filename)}`, {
                        method: 'DELETE',
                    })
                    .then(response => {
                        if (!response.ok) {
                            return response.json().then(data => {
                                throw new Error(data.error || `Server error: ${response.status}`);
                            });
                        }
                        return response.json();
                    })
                    .then(data => {
                        if (data.success) {
                            console.log(`[Archive] Successfully deleted file: ${filename}`);
                            // Show a brief notification
                            const statusElement = document.createElement('div');
                            statusElement.textContent = `Successfully deleted ${filename}`;
                            statusElement.style.backgroundColor = '#313244';
                            statusElement.style.color = '#a6e3a1';
                            statusElement.style.padding = '0.5em';
                            statusElement.style.marginTop = '0.5em';
                            statusElement.style.borderRadius = '4px';
                            statusElement.style.textAlign = 'center';
                            
                            subsArchiveList.parentNode.insertBefore(statusElement, subsArchiveList.nextSibling);
                            
                            // Remove notification after 3 seconds
                            setTimeout(() => {
                                statusElement.remove();
                            }, 3000);
                            
                            // Refresh the list
                            fetchSubsArchive();
                        } else {
                            alert(`Error deleting file: ${data.error || 'Unknown error'}`);
                        }
                    })
                    .catch(err => {
                        console.error(`[Archive] Error deleting file: ${err}`);
                        alert(`Error deleting file: ${err.message}`);
                    });
                }

                // Initialize fetch on page load
                document.addEventListener('DOMContentLoaded', function() {
                    // Initialize the subsArchiveList variable
                    const subsArchiveList = document.getElementById('subs-archive-list');
                    // Make it globally available
                    window.subsArchiveList = subsArchiveList;
                    
                    fetchSubsArchive();
                    // ... other initializations ...
                });

                // Refresh button for archive
                const refreshSubsBtn = document.getElementById('refresh-subs-btn');
                refreshSubsBtn.addEventListener('click', fetchSubsArchive);
            </script>
    </body>
    </html>
    """

    CONSOLE_PAGE_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Translation Console</title>
    </head>
    <body>
        <h1>Translation Debug Console</h1>
        <pre id="logBox">Loading logs...</pre>
        <script>
          const logBox = document.getElementById('logBox');
          let isScrolled = true;
          logBox.addEventListener('scroll', function() {
              isScrolled = (logBox.scrollHeight - logBox.clientHeight) <= (logBox.scrollTop + 10);
          });
          function fetchLogs() {
            fetch('/logs')
              .then(resp => resp.text())
              .then(txt => {
                logBox.textContent = txt;
                if(isScrolled) {
                    logBox.scrollTop = logBox.scrollHeight;
                }
              })
              .catch(error => {
                  console.error("Error fetching logs:", error);
              });
          }
          fetchLogs();
          setInterval(fetchLogs, 3000);
        </script>
    </body>
    </html>
    """

    translation_progress = {
        "current_line": 0,
        "total_lines": 0,
        "status": "idle",
        "message": "",
        "mode": "single",   # "single" or "bulk"
        "zip_path": "",     # set by bulk translator
        "current": {
            "line_number": 0,
            "original": "",
            "suggestions": {},
            "first_pass": "",
            "standard_critic": "",
            "standard_critic_changed": False,
            "critics": [],
            "final": "",
            "llm_status": ""  # Added this field to track LLM agent status
        },
        "processed_lines": []  # Store history of processed lines
    }

    app = Flask(__name__)
    app.secret_key = os.urandom(24)

    @app.route("/")
    def index():
        return render_template_string(INDEX_PAGE)

    @app.route("/logs")
    def logs_page():
        # This route will render the big log viewer template in a separate tab
        return render_template_string(LOG_VIEWER_TEMPLATE)

    @app.route("/config")
    def config_page():
        return render_template_string(CONFIG_EDITOR_TEMPLATE)

    @app.route("/console")
    def console():
        return render_template_string(CONSOLE_PAGE_TEMPLATE)

    @app.route('/api/logs')
    def api_logs():
        return jsonify({"logs": get_logs()})

    @app.route('/api/config')
    def api_config():
        c = load_config()
        config_dict = {}
        for section in c.sections():
            config_dict[section] = {}
            for key, value in c[section].items():
                if (value.lower() in ('true', 'false')):
                    config_dict[section][key] = c.getboolean(section, key)
                else:
                    # Try to parse as number
                    if (value.replace('.', '', 1).isdigit()):
                        try:
                            if ('.' in value):
                                config_dict[section][key] = float(value)
                            else:
                                config_dict[section][key] = int(value)
                        except ValueError:
                            config_dict[section][key] = value
                    else:
                        config_dict[section][key] = value
        return jsonify({"config": config_dict})

    @app.route('/api/config', methods=['POST'])
    def update_config():
        try:
            data = request.json
            updated_config = data['config']
            current_config = load_config()
            for section, items in updated_config.items():
                if (not current_config.has_section(section)):
                    current_config.add_section(section)
                for k, v in items.items():
                    if (isinstance(v, bool)):
                        v = 'true' if v else 'false'
                    else:
                        v = str(v)
                    current_config[section][k] = v
            with open(CONFIG_FILENAME, 'w') as f:
                current_config.write(f)
            append_log("[INFO] Configuration updated.")
            return jsonify({"success": True})
        except Exception as e:
            append_log(f"[ERROR] Failed to update config: {e}")
            return jsonify({"success": False, "message": str(e)}), 500

    @app.route('/api/progress')
    def get_progress():
        return jsonify(translation_progress)

    @app.route("/upload", methods=["POST"])
    def upload():
        from flask import flash
        if ("srtfile" not in request.files):
            flash("No SRT file part in the request.", "error")
            return redirect(url_for("index"))
        file = request.files["srtfile"]
        if (file.filename == ""):
            flash("No selected file.", "error")
            return redirect(url_for("index"))
        if (not file.filename.lower().endswith(".srt")):
            flash("Invalid file type. Please upload an SRT file.", "error")
            return redirect(url_for("index"))

        # No longer need a temporary directory for single uploads
        # try:
        #     tempd = tempfile.mkdtemp(prefix="srt_translate_")
        #     TEMP_DIRS_TO_CLEAN.add(tempd)
        #     append_log(f"Temp directory: {tempd}")
        # except Exception as e:
        #     append_log(f"[ERROR] {e}")
        #     flash("Server error creating temp dir.", "error")
        #     return redirect(url_for("index"))

        # Save the uploaded file temporarily for processing
        temp_input_path = os.path.join(tempfile.gettempdir(), secure_filename(f"temp_{file.filename}"))
        try:
            file.save(temp_input_path)
            append_log(f"Received SRT: {file.filename} -> {temp_input_path}")
        except Exception as e:
            append_log(f"[ERROR] Failed to save temporary input file: {e}")
            flash("Server error saving uploaded file.", "error")
            return redirect(url_for("index"))

        cfg = load_config()
        src_lang = cfg.get("general", "source_language", fallback="original").strip('"\' ')
        tgt_lang = cfg.get("general", "target_language", fallback="translated").strip('"\' ')
        src_iso = get_iso_code(src_lang)
        tgt_iso = get_iso_code(tgt_lang)

        base, ext = os.path.splitext(file.filename)
        out_base = base
        replaced = False
        patterns = [
            f'.{src_iso}.', f'.{src_iso}-', f'.{src_iso}_',
            f'{src_iso}.', f'-{src_iso}.', f'_{src_iso}.'
        ]
        import re
        for pat in patterns:
            if (pat in base.lower()):
                newpat = pat.replace(src_iso, tgt_iso)
                out_base = re.sub(pat, newpat, base, flags=re.IGNORECASE)
                replaced = True
                break
        if (not replaced):
            out_base = f"{base}.{tgt_iso}"
        
        # Ensure the output filename is secure and save to SUBS_FOLDER
        out_filename = secure_filename(out_base + ext)
        output_path = os.path.join(SUBS_FOLDER, out_filename) # Save to ./subs/

        try:
            # Reset progress for single file mode
            translation_progress.clear()
            translation_progress.update({
                "current_line": 0,
                "total_lines": 0,
                "status": "idle",
                "message": "",
                "mode": "single",
                "zip_path": "",
                "current": {
                    "line_number": 0, "original": "", "suggestions": {}, "first_pass": "",
                    "standard_critic": "", "standard_critic_changed": False, "critics": [],
                    "final": "", "llm_status": ""
                },
                "processed_lines": []
            })
            
            translate_srt(temp_input_path, output_path, cfg)
            flash(f"Translation complete! Saved as '{out_filename}' in the subs archive.", "success")
        except Exception as e:
            append_log(f"[ERROR] Translation failed: {e}")
            flash(f"Translation failed: {e}", "error")
        finally:
            # Clean up the temporary input file
            if os.path.exists(temp_input_path):
                try:
                    os.remove(temp_input_path)
                    append_log(f"Cleaned up temporary input file: {temp_input_path}")
                except Exception as e_clean:
                    append_log(f"[WARNING] Failed to clean up temp input file {temp_input_path}: {e_clean}")

        # Redirect back to the index page instead of download
        return redirect(url_for("index"))

    # Remove the old download route that used temporary folders
    # @app.route("/download/<path:folder>/<path:filename>")
    # def download_file(folder, filename):
    #     ... (old code removed) ...

    # Add a new route to download files specifically from the SUBS_FOLDER
    @app.route("/download_sub/<path:filename>")
    def download_sub_file(filename):
        safe_filename = secure_filename(filename)
        if safe_filename != filename: # Basic check against directory traversal attempts
             append_log(f"[SECURITY] Invalid filename requested for download: {filename}")
             return "Invalid filename", 400
             
        file_path = os.path.join(SUBS_FOLDER, safe_filename)
        
        if not os.path.isfile(file_path):
            append_log(f"[ERROR] File not found in subs archive: {file_path}")
            return "File not found in archive", 404
            
        append_log(f"Serving file from subs archive: {file_path}")
        try:
            return send_from_directory(
                SUBS_FOLDER, 
                safe_filename, 
                as_attachment=True
            )
        except Exception as e:
            append_log(f"[ERROR] Failed to send file from subs archive {file_path}: {e}")
            return "Error serving file", 500

    # Add a route to list files in the SUBS_FOLDER
    @app.route("/api/list_subs")
    def api_list_subs():
        try:
            append_log(f"[INFO] Listing subtitle files in {SUBS_FOLDER}")
            if not os.path.exists(SUBS_FOLDER):
                append_log(f"[WARNING] Subs folder {SUBS_FOLDER} does not exist")
                return jsonify({"files": [], "warning": f"Subs folder {SUBS_FOLDER} does not exist"})
            
            files = [f for f in os.listdir(SUBS_FOLDER) if f.lower().endswith('.srt') and os.path.isfile(os.path.join(SUBS_FOLDER, f))]
            files.sort(key=lambda f: os.path.getmtime(os.path.join(SUBS_FOLDER, f)), reverse=True) # Sort by modification time, newest first
            append_log(f"[INFO] Found {len(files)} subtitle files in archive")
            return jsonify({"files": files})
        except Exception as e:
            append_log(f"[ERROR] Failed to list subs folder: {e}")
            return jsonify({"files": [], "error": str(e)}), 500
    
    # Add a route to delete subtitle files from SUBS_FOLDER
    @app.route("/api/delete_sub/<path:filename>", methods=["DELETE"])
    def api_delete_sub(filename):
        try:
            safe_filename = secure_filename(filename)
            if safe_filename != filename:
                return jsonify({"success": False, "error": "Invalid filename"}), 400
                
            file_path = os.path.join(SUBS_FOLDER, safe_filename)
            
            if not os.path.isfile(file_path):
                return jsonify({"success": False, "error": "File not found"}), 404
                
            os.remove(file_path)
            append_log(f"[INFO] Deleted subtitle file: {safe_filename}")
            return jsonify({"success": True})
        except Exception as e:
            append_log(f"[ERROR] Failed to delete subtitle file: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/start-scan", methods=["POST"])
    def api_start_scan():
        data = request.get_json(silent=True) or {}
        root = data.get("path", "").strip()
        if not root or not os.path.isdir(root):
            append_log(f"[ERROR] /api/start-scan: Invalid or missing folder path: {root}")
            return jsonify({"ok": False, "error": "Folder not found or path is invalid"}), 400

        cfg = load_config()

        # Optional: Whitelist check
        allowed_bases = cfg.get("bulk_scan", "allowed_base", fallback="").split(',')
        allowed_bases = [os.path.abspath(b.strip()) for b in allowed_bases if b.strip()]
        if allowed_bases: # Only check if allowed_base is configured
            try:
                abs_root = os.path.abspath(root)
                base_ok = any(os.path.commonpath([abs_root, b]) == b for b in allowed_bases)
                if not base_ok:
                    append_log(f"[ERROR] /api/start-scan: Path '{root}' is outside allowed base paths: {allowed_bases}")
                    return jsonify({"ok": False, "error": "Folder is outside the allowed base paths configured in config.ini"}), 403
            except ValueError as e:
                # Handle cases where paths might be on different drives (Windows)
                append_log(f"[ERROR] /api/start-scan: Error checking common path for '{root}': {e}")
                return jsonify({"ok": False, "error": "Error validating folder path against allowed bases."}), 400

        # Reset global progress dict for bulk mode
        translation_progress.clear()
        translation_progress.update({
            "mode"        : "bulk",
            "status"      : "queued",
            "message"     : "",
            "current_file": "",
            "done_files"  : 0,
            "total_files" : 0,
            "zip_path"    : ""
        })
        append_log(f"[INFO] /api/start-scan: Starting bulk scan for folder: {root}")

        # Background thread so the HTTP response returns instantly
        threading.Thread(
            target=scan_and_translate_directory,
            args=(root, cfg, translation_progress, translate_srt, append_log),
            daemon=True
        ).start()

        return jsonify({"ok": True})

    @app.route("/download-zip")
    def download_zip():
        temp_path = request.args.get("temp", "")
        # Security check: Ensure the path is within an expected temp directory structure if possible
        # This is a basic check; more robust validation might be needed depending on security requirements.
        if not temp_path or not temp_path.startswith(tempfile.gettempdir()) or '..' in temp_path:
            append_log(f"[ERROR] /download-zip: Invalid or potentially unsafe temp path requested: {temp_path}")
            return "Invalid or potentially unsafe file path", 400
            
        if not os.path.isfile(temp_path):
            append_log(f"[ERROR] /download-zip: Zip file not found or expired: {temp_path}")
            return "File expired or missing", 404
            
        append_log(f"[INFO] /download-zip: Serving zip file: {temp_path}")
        try:
            return send_from_directory(
                directory=os.path.dirname(temp_path),
                path=os.path.basename(temp_path), # Use 'path' argument for Flask >= 2.0
                as_attachment=True,
                download_name="translated_subtitles.zip" # Suggest a filename to the user
            )
        except Exception as e:
            append_log(f"[ERROR] /download-zip: Failed to send file {temp_path}: {e}")
            return "Error serving file", 500

    host = cfg_for_logger.get("general", "host", fallback="127.0.0.1")
    port = cfg_for_logger.getint("general", "port", fallback=5000)

    print("="*40)
    print(f" Subtitle Translator UI running at http://{host}:{port}/ ")
    print(" Press CTRL+C to stop the server.")
    print("="*40)

    try:
        app.run(host=host, port=port, debug=False)
    except Exception as e:
        append_log(f"[ERROR] Flask server failed: {e}")
        sys.exit(1)
    finally:
        cleanup_temp_dirs()

import re

def extract_item_name(filename: str) -> str:
    _SERIES_RE = re.compile(r"^(?P<title>.+?)\\.S\\d{2}E\\d{2}", re.I)
    _MOVIE_RE  = re.compile(r"^(?P<title>.+?)\\.(19|20)\\d{2}")
    base = os.path.splitext(filename)[0]
    m = _SERIES_RE.match(base) or _MOVIE_RE.match(base)
    return (m.group("title") if m else base).replace('.', ' ').strip()

def scan_and_translate_directory(root_path: str, cfg=None, progress_dict=None, translate_func=None, log_func=None):
    """
    Walk *root_path* recursively, translate each *.{src}.srt* that does not
    already have a *.{tgt}.srt* sibling. Updates *progress_dict* so the
    UI can poll live status.
    """
    import os
    import zipfile, tempfile, shutil

    # If called from CLI, load config and create a dummy progress dict
    if cfg is None:
        cfg = load_config()
    if progress_dict is None:
        progress_dict = {}
    
    # Use provided functions or fallbacks
    translate_srt_func = translate_func or (lambda *args: print(f"Would translate: {args}"))
    append_log_func = log_func or print

    src_iso = get_iso_code(cfg.get("general", "source_language", fallback="en"))
    tgt_iso = get_iso_code(cfg.get("general", "target_language", fallback="da"))

    # We'll collect output files here so the user can download one zip at the end
    work_dir  = tempfile.mkdtemp(prefix="bulk_subs_")
    TEMP_DIRS_TO_CLEAN.add(work_dir) # Ensure cleanup
    out_files = []

    # First pass – just count jobs for nice 0‑% progress
    srt_jobs = []
    append_log_func(f"[INFO] Looking for subtitles in language {src_iso} that need translation to {tgt_iso}")
    
    for dirpath, _, filenames in os.walk(root_path):
        # Keep track of which episodes have source and target subtitles
        episodes_with_src = set()
        episodes_with_tgt = set()
        
        # First, identify all English and Danish subtitles
        for fn in filenames:
            lower_fn = fn.lower()
            
            # Identify base filename (without extension)
            base_name = None
            
            # Match patterns like "Show.Name.S01E02.Episode.Title.en.srt"
            # or "Show.Name.S01E02.Episode.Title.en.hi.srt"
            if f".{src_iso}." in lower_fn and (lower_fn.endswith('.srt') or lower_fn.endswith('.ass')):
                # Extract the episode identifier (e.g., "Show.Name.S01E02.Episode.Title")
                parts = fn.split(f".{src_iso}.")
                if len(parts) >= 2:
                    base_name = parts[0]
                    episodes_with_src.add(base_name)
            
            if f".{tgt_iso}." in lower_fn and (lower_fn.endswith('.srt') or lower_fn.endswith('.ass')):
                # Extract the episode identifier
                parts = fn.split(f".{tgt_iso}.")
                if len(parts) >= 2:
                    base_name = parts[0]
                    episodes_with_tgt.add(base_name)
        
        # Now find episodes that have source but not target subtitles
        episodes_needing_translation = episodes_with_src - episodes_with_tgt
        
        if episodes_needing_translation:
            append_log_func(f"[INFO] Found {len(episodes_needing_translation)} episode(s) needing translation in {dirpath}")
            
            # Find the source subtitle files for these episodes
            for episode in episodes_needing_translation:
                for fn in filenames:
                    # Look for source subtitle file for this episode
                    if fn.startswith(episode) and f".{src_iso}." in fn and (fn.endswith('.srt') or fn.endswith('.ass')):
                        src_path = os.path.join(dirpath, fn)
                        
                        # For destination filename, replace source language code with target
                        if ".hi." in fn:  # Handle "hi" (hearing impaired) tag
                            dest_fn = f"{episode}.{tgt_iso}.hi.srt"
                        else:
                            dest_fn = f"{episode}.{tgt_iso}.srt"
                        
                        # Prepare output path in work directory
                        rel_path = os.path.relpath(dirpath, root_path)
                        dest_dir_in_work = os.path.join(work_dir, rel_path)
                        os.makedirs(dest_dir_in_work, exist_ok=True)
                        dest_in_work = os.path.join(dest_dir_in_work, dest_fn)
                        
                        append_log_func(f"[INFO] Will translate: {fn} -> {dest_fn}")
                        srt_jobs.append((src_path, dest_in_work))
                        break

    # Update progress dict for UI
    progress_dict.update({
        "mode"        : "bulk",
        "status"      : "translating",
        "current_file": "",
        "done_files"  : 0,
        "total_files" : len(srt_jobs)
    })

    for idx, (src, dest_in_work) in enumerate(srt_jobs, 1):
        rel_src_path = os.path.relpath(src, root_path)
        progress_dict["current_file"] = rel_src_path
        # If called from CLI, print progress
        if 'print' in globals():
            print(f"[{idx}/{len(srt_jobs)}] Translating {rel_src_path}...")

        try:
            # Call the translate function with the provided parameters
            translate_srt_func(src, dest_in_work, cfg)
            out_files.append(dest_in_work)
            append_log_func(f"[BULK] Successfully translated: {rel_src_path}")
        except Exception as e:
            # Log error but continue with next file
            log_msg = f"[ERROR] Failed to translate {rel_src_path}: {str(e)}"
            append_log_func(log_msg)
        progress_dict["done_files"] = idx

    # Package results if any files were translated
    zip_name = ""
    if out_files:
        zip_basename = "translated_subs.zip"
        zip_name = os.path.join(work_dir, zip_basename)
        with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as z:
            for f in out_files:
                arcname = os.path.relpath(f, work_dir)
                z.write(f, arcname)
        append_log_func(f"[BULK] Created zip file: {zip_name}")
    else:
        append_log_func("[BULK] No files needed translation or translation failed for all files.")

    skipped_count = len(srt_jobs) - len(out_files)
    message = f"Finished {len(out_files)} files."
    if skipped_count > 0:
        message += f" Skipped {skipped_count} files (already exist or failed)."

    progress_dict.update({
        "status"   : "done",
        "zip_path" : zip_name, # Path to the zip file within the temp dir
        "message"  : message
    })
    append_log_func(f"[BULK] {message}")
    # If called from CLI, print final message
    if 'print' in globals():
        print(f"[BULK] {message}")
        if zip_name:
            print(f"[BULK] Results saved to: {zip_name}")

def main():
    parser = argparse.ArgumentParser(
        description="Translate individual SRTs **or** whole folders")
    parser.add_argument(
        "--scan", "-s",
        help="Root folder to search for SRT files (recurses ‘Season x’, etc.)")
    args, unknown = parser.parse_known_args()
    
    if args.scan:
        scan_and_translate_directory(args.scan)
    else:
        setup_environment_and_run()

if __name__ == "__main__":
    main()
