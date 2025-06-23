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
    
    let currentPath = '';
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
    
    // Add event listener for the save special meanings button
    const saveSpecialMeaningsBtn = document.getElementById('save-special-meanings-btn');
    if (saveSpecialMeaningsBtn) {
        saveSpecialMeaningsBtn.addEventListener('click', function() {
            saveSpecialMeanings();
        });
    }

    // Add event listener for the add special meaning button
    const addSpecialMeaningBtn = document.getElementById('add-special-meaning-btn');
    if (addSpecialMeaningBtn) {
        addSpecialMeaningBtn.addEventListener('click', function() {
            window.addSpecialMeaningRow();
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
        // Update globals
        currentPath = path;

        // Show path (fallback to / for root)
        currentPathDisplay.textContent = path || '/';

        // Clear existing list
        directoryList.innerHTML = '';

        // Build API URL – omit the query param when at virtual root
        let apiUrl = '/api/browse_dirs';
        if (path) {
            apiUrl += `?path=${encodeURIComponent(path)}`;
        }

        // Fetch directories from backend
        fetch(apiUrl)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert(`Error: ${data.error}`);
                return;
            }

            // Add parent navigation if server says it's allowed
            if (data.parent_path) {
                const parentItem = document.createElement('li');
                parentItem.classList.add('directory-item');
                parentItem.innerHTML = '<i class="fas fa-level-up-alt"></i> Parent Directory';
                parentItem.addEventListener('click', function() {
                    loadDirectoryContent(data.parent_path);
                });
                directoryList.appendChild(parentItem);
            }

            // Render directories
            (data.directories || []).forEach(dir => {
                const dirItem = document.createElement('li');
                dirItem.classList.add('directory-item');
                dirItem.innerHTML = `<i class="fas fa-folder"></i> ${dir.name}`;
                dirItem.addEventListener('click', function() {
                    loadDirectoryContent(dir.path);
                });
                directoryList.appendChild(dirItem);
            });
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
            if (data.error) {
                alert(`Error: ${data.error}`);
                return;
            }

            if ((data.files || []).length === 0) {
                alert('Warning: No subtitle files found in this directory.');
            } else {
                console.log(`Found ${data.files.length} subtitle files.`);
            }
        });
    }
    
    // Start bulk translation
    document.getElementById('inline-select-dir-btn').addEventListener('click', function() {
        // Disallow root selection (must pick an actual directory)
        if ((!selectedDirectory && !currentPath) || (!selectedDirectory && !currentPath)) {
            alert('Please select a directory first.');
            return;
        }
        
        const dirPath = selectedDirectory || currentPath;
        if (!dirPath) {
            alert('Please navigate into a directory first.');
            return;
        }
        
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
            // Save special meanings after removal
            saveSpecialMeanings();
        });
        
        specialMeaningsContainer.appendChild(row);
    };
    
    // Load special meanings from file
    window.loadSpecialMeaningsFromFile = function() {
        fetch('/api/special_meanings')
        .then(response => response.json())
        .then(data => {
            if (data.success && data.meanings && data.meanings.length > 0) {
                specialMeaningsContainer.innerHTML = ''; // Clear existing
                
                data.meanings.forEach(meaning => {
                    const row = document.createElement('div');
                    row.className = 'special-meaning-row';
                    row.innerHTML = `
                        <div class="meaning-inputs">
                            <input type="text" class="form-control source-text" value="${meaning.word || ''}" placeholder="Original text">
                            <input type="text" class="form-control target-text" value="${meaning.meaning || ''}" placeholder="Translation">
                        </div>
                        <button type="button" class="btn btn-danger btn-sm remove-meaning-btn">
                            <i class="fas fa-times"></i>
                        </button>
                    `;
                    
                    // Add remove button functionality
                    const removeBtn = row.querySelector('.remove-meaning-btn');
                    removeBtn.addEventListener('click', function() {
                        row.remove();
                        // Save special meanings after removal
                        saveSpecialMeanings();
                    });
                    
                    specialMeaningsContainer.appendChild(row);
                });
            }
        })
        .catch(error => {
            console.error('Error loading special meanings:', error);
        });
    };
    
    // Function to collect all special meanings as an array of objects
    function collectSpecialMeanings() {
        console.log("Collecting special meanings from bulk translate page");
        const specialMeanings = [];
        const rows = document.querySelectorAll('.special-meaning-row');
        
        rows.forEach(row => {
            const sourceInput = row.querySelector('.source-text');
            const targetInput = row.querySelector('.target-text');
            
            if (sourceInput && targetInput) {
                const word = sourceInput.value.trim();
                const meaning = targetInput.value.trim();
                
                if (word && meaning) {
                    specialMeanings.push({ word, meaning });
                }
            }
        });
        
        console.log('Collected special meanings:', specialMeanings.length);
        return specialMeanings;
    }
    
    // Function to save special meanings to the file
    function saveSpecialMeanings() {
        console.log("Saving special meanings from bulk translate page");
        const meanings = collectSpecialMeanings();
        
        fetch('/api/special_meanings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ meanings: meanings })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log(`Saved ${meanings.length} special meanings to file`);
                // Show a success message
                const statusElem = document.getElementById('special-meanings-status');
                if (statusElem) {
                    statusElem.textContent = `Saved ${meanings.length} meanings`;
                    statusElem.className = 'success-message';
                    setTimeout(() => {
                        statusElem.textContent = '';
                    }, 3000);
                }
            } else {
                console.error("Error saving special meanings:", data.message || "Unknown error");
                alert(`Error saving special meanings: ${data.message || 'Unknown error'}`);
            }
        })
        .catch(error => {
            console.error("Error saving special meanings:", error);
            alert(`Error saving special meanings: ${error.message}`);
        });
    }

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
