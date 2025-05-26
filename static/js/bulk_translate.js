// Bulk translate page functionality
document.addEventListener('DOMContentLoaded', function() {
    // Directory browser functionality
    const browseBtn = document.getElementById('browse-btn');
    const toggleBrowserBtn = document.getElementById('toggle-browser-btn');
    const inlineFileBrowser = document.getElementById('inline-file-browser');
    const directoryDisplay = document.getElementById('selected-directory-display');
    const directoryList = document.getElementById('inline-directory-list');
    const currentPathDisplay = document.getElementById('current-inline-path');
    const selectDirBtn = document.getElementById('inline-select-dir-btn');
    
    let currentPath = '/';
    let selectedDirectory = null;
    
    // Initialize special meanings functionality
    const specialMeaningsContainer = document.getElementById('special-meanings-container');
    
    // Directory browser buttons
    if (browseBtn) {
        browseBtn.addEventListener('click', function() {
            if (inlineFileBrowser.style.display === 'none' || !inlineFileBrowser.style.display) {
                inlineFileBrowser.style.display = 'block';
                loadDirectoryContent(currentPath);
            } else {
                inlineFileBrowser.style.display = 'none';
            }
        });
    }
    
    if (toggleBrowserBtn) {
        toggleBrowserBtn.addEventListener('click', function() {
            if (inlineFileBrowser.style.display === 'none' || !inlineFileBrowser.style.display) {
                inlineFileBrowser.style.display = 'block';
                loadDirectoryContent(currentPath);
            } else {
                inlineFileBrowser.style.display = 'none';
            }
        });
    }
    
    if (selectDirBtn) {
        selectDirBtn.addEventListener('click', function() {
            selectDirectory(currentPath);
        });
    }
    
    // Function to load directory content
    function loadDirectoryContent(path) {
        currentPathDisplay.textContent = path;
        currentPath = path;
        directoryList.innerHTML = '';
        
        // Add parent directory option if not at root
        if (path !== '/') {
            const parentDir = path.split('/').slice(0, -2).join('/') + '/';
            const parentItem = document.createElement('li');
            parentItem.classList.add('directory-item');
            parentItem.innerHTML = '<i class="fas fa-level-up-alt"></i> Parent Directory';
            parentItem.addEventListener('click', function() {
                loadDirectoryContent(parentDir);
            });
            directoryList.appendChild(parentItem);
        }
        
        // Fetch directories
        fetch(`/api/browse_dirs?path=${encodeURIComponent(path)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                data.directories.forEach(dir => {
                    const dirItem = document.createElement('li');
                    dirItem.classList.add('directory-item');
                    dirItem.innerHTML = `<i class="fas fa-folder"></i> ${dir.name}`;
                    dirItem.addEventListener('click', function() {
                        loadDirectoryContent(dir.path);
                    });
                    directoryList.appendChild(dirItem);
                });
            }
        });
    }
    
    // Function to select a directory
    function selectDirectory(path) {
        selectedDirectory = path;
        directoryDisplay.value = path;
        inlineFileBrowser.style.display = 'none';
        
        // Check if directory contains subtitle files
        fetch(`/api/browse_files?path=${encodeURIComponent(path)}&filter=subtitle`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                if (data.files.length === 0) {
                    alert('Warning: No subtitle files found in this directory.');
                } else {
                    // You could show a preview of subtitle files here
                    console.log(`Found ${data.files.length} subtitle files.`);
                }
            }
        });
    }
    
    // Start bulk translation
    document.getElementById('inline-select-dir-btn').addEventListener('click', function() {
        if (!selectedDirectory && !currentPath) {
            alert('Please select a directory first.');
            return;
        }
        
        const dirPath = selectedDirectory || currentPath;
        const sourceLanguage = document.getElementById('source-language').value;
        const targetLanguage = document.getElementById('target-language').value;
        
        // Collect special meanings
        const specialMeanings = [];
        const meaningRows = document.querySelectorAll('.special-meaning-row');
        meaningRows.forEach(row => {
            const sourceText = row.querySelector('.source-text').value.trim();
            const targetText = row.querySelector('.target-text').value.trim();
            if (sourceText && targetText) {
                specialMeanings.push({
                    source: sourceText,
                    target: targetText
                });
            }
        });
        
        // Show status container
        const statusContainer = document.getElementById('status-container');
        if (statusContainer) {
            statusContainer.style.display = 'block';
            document.getElementById('status-message').textContent = 'Starting bulk translation...';
            document.getElementById('progress-bar').style.width = '0%';
            document.getElementById('progress-text').textContent = '0%';
            document.getElementById('live-status-display').innerHTML = '<p>Initializing bulk translation...</p>';
        }
        
        // Send bulk translation request
        const formData = new FormData();
        formData.append('directory_path', dirPath);
        formData.append('source_language', sourceLanguage);
        formData.append('target_language', targetLanguage);
        formData.append('special_meanings', JSON.stringify(specialMeanings));
        
        // Generate a unique job ID
        const jobId = Date.now().toString(36) + Math.random().toString(36).substr(2);
        formData.append('job_id', jobId);
        
        fetch('/api/bulk_translate', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // Start polling for progress
                pollBulkTranslationProgress(jobId);
            } else {
                document.getElementById('status-message').textContent = 'Error: ' + data.message;
                document.getElementById('live-status-display').innerHTML = '<p class="error">Bulk translation failed: ' + data.message + '</p>';
            }
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('status-message').textContent = 'Error: ' + error.message;
            document.getElementById('live-status-display').innerHTML = '<p class="error">Bulk translation failed: ' + error.message + '</p>';
        });
    });
    
    // Function to poll for bulk translation progress
    function pollBulkTranslationProgress(jobId) {
        const progressInterval = setInterval(function() {
            fetch(`/api/bulk_translation_progress/${jobId}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'error') {
                    clearInterval(progressInterval);
                    document.getElementById('status-message').textContent = 'Error: ' + data.message;
                    document.getElementById('live-status-display').innerHTML += '<p class="error">Bulk translation failed: ' + data.message + '</p>';
                    return;
                }
                
                const progress = data.progress;
                document.getElementById('progress-bar').style.width = progress + '%';
                document.getElementById('progress-text').textContent = progress + '%';
                document.getElementById('status-message').textContent = data.message;
                
                if (data.live_status) {
                    document.getElementById('live-status-display').innerHTML = data.live_status;
                }
                
                if (data.complete) {
                    clearInterval(progressInterval);
                    document.getElementById('status-message').textContent = 'Bulk translation complete!';
                    
                    // Show download link
                    const resultContainer = document.getElementById('result-container');
                    if (resultContainer) {
                        resultContainer.style.display = 'block';
                        resultContainer.innerHTML = `
                            <div class="download-links">
                                <a href="/download-zip/${jobId}" class="btn btn-success">
                                    <i class="fas fa-download"></i> Download All Files (ZIP)
                                </a>
                                <button class="btn btn-info view-report-btn" data-report="${jobId}">
                                    <i class="fas fa-file-alt"></i> View Translation Report
                                </button>
                            </div>
                        `;
                        
                        // Add event listener for view report button
                        const viewReportBtn = resultContainer.querySelector('.view-report-btn');
                        if (viewReportBtn) {
                            viewReportBtn.addEventListener('click', function() {
                                const reportId = this.getAttribute('data-report');
                                viewTranslationReport(reportId);
                            });
                        }
                    }
                }
            })
            .catch(error => {
                console.error('Error polling progress:', error);
            });
        }, 1000);
    }
    
    // Function to view translation report
    function viewTranslationReport(reportId) {
        fetch(`/api/view_report/${reportId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const reportModal = document.getElementById('report-modal');
                const reportTitle = document.getElementById('report-modal-title');
                const reportContent = document.getElementById('report-content');
                const reportLoading = document.getElementById('report-loading');
                
                reportTitle.textContent = 'Translation Report';
                reportLoading.style.display = 'none';
                reportContent.innerHTML = data.content;
                reportModal.style.display = 'block';
            } else {
                alert('Error viewing report: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error viewing report:', error);
            alert('Error viewing report: ' + error.message);
        });
    }
    
    // Special meanings functionality
    window.addSpecialMeaningRow = function() {
        const row = document.createElement('div');
        row.className = 'special-meaning-row';
        row.innerHTML = `
            <div class="meaning-inputs">
                <input type="text" class="form-control source-text" placeholder="Original text">
                <input type="text" class="form-control target-text" placeholder="Translation">
            </div>
            <button type="button" class="btn btn-danger btn-sm remove-meaning-btn">
                <i class="fas fa-times"></i>
            </button>
        `;
        
        // Add remove button functionality
        const removeBtn = row.querySelector('.remove-meaning-btn');
        removeBtn.addEventListener('click', function() {
            row.remove();
        });
        
        specialMeaningsContainer.appendChild(row);
    };
    
    // Load special meanings from file
    window.loadSpecialMeaningsFromFile = function() {
        fetch('/api/special_meanings')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success' && data.meanings && data.meanings.length > 0) {
                specialMeaningsContainer.innerHTML = ''; // Clear existing
                
                data.meanings.forEach(meaning => {
                    const row = document.createElement('div');
                    row.className = 'special-meaning-row';
                    row.innerHTML = `
                        <div class="meaning-inputs">
                            <input type="text" class="form-control source-text" value="${meaning.source}" placeholder="Original text">
                            <input type="text" class="form-control target-text" value="${meaning.target}" placeholder="Translation">
                        </div>
                        <button type="button" class="btn btn-danger btn-sm remove-meaning-btn">
                            <i class="fas fa-times"></i>
                        </button>
                    `;
                    
                    // Add remove button functionality
                    const removeBtn = row.querySelector('.remove-meaning-btn');
                    removeBtn.addEventListener('click', function() {
                        row.remove();
                    });
                    
                    specialMeaningsContainer.appendChild(row);
                });
            }
        })
        .catch(error => {
            console.error('Error loading special meanings:', error);
        });
    };
    
    // Close modals when clicking outside
    window.addEventListener('click', function(event) {
        const reportModal = document.getElementById('report-modal');
        const modal = document.getElementById('modal');
        
        if (event.target == reportModal) {
            reportModal.style.display = 'none';
        }
        
        if (event.target == modal) {
            modal.style.display = 'none';
        }
    });
});
