// Main JavaScript file for Subtitle Translator

// --- Global Variables ---
let currentPath = '';
let selectedDirectory = '';
let bulkProgressInterval = null;
let currentJobId = null; // Keep track of the current single translation job
// Track expanded history items by their line_number
let expandedHistoryItems = new Set();
let browserVisible = false; // File Browser State Management
let isTranslationActive = false; // Flag to track if a translation is running

// Helper function to log debug messages
function debug(message) {
    console.log(`[DEBUG] ${message}`);
}

// --- Consolidated DOMContentLoaded Listener ---
document.addEventListener('DOMContentLoaded', function() {
    console.log("Document loaded, initializing...");

    // Load special meanings from file first
    loadSpecialMeaningsFromFile();

    // Check for active translations as soon as page loads
    checkForActiveTranslations();

    // Check if we have a saved state for the file browser visibility
    const savedState = localStorage.getItem('inlineFileBrowserVisible');
    if (savedState === 'true') {
        showInlineFileBrowser();
    }
    
    // Initialize with home directory
    if (browserVisible) {
        browseInlineDirectory('');
    }
    
    // --- Host File Browser Handling ---
    const browseHostFileBtn = document.getElementById('browse-host-file-btn');
    const hostFileBrowser = document.getElementById('host-file-browser');
    
    if (browseHostFileBtn) {
        browseHostFileBtn.addEventListener('click', function() {
            if (hostFileBrowser) {
                // Toggle file browser visibility
                if (hostFileBrowser.style.display === 'none') {
                    hostFileBrowser.style.display = 'block';
                    // Load files only if the list is empty
                    const fileList = document.getElementById('host-file-list');
                    if (fileList && (!fileList.children.length || fileList.innerHTML === '')) {
                        browseHostFiles('');
                    }
                } else {
                    hostFileBrowser.style.display = 'none';
                }
            }
        });
    }
    
    // Event delegation for the host file list
    const hostFileList = document.getElementById('host-file-list');
    if (hostFileList) {
        hostFileList.addEventListener('click', function(event) {
            const item = event.target.closest('li');
            if (!item) return;
            
            if (item.classList.contains('directory-item')) {
                // If directory, navigate into it
                const path = item.dataset.path;
                if (path) {
                    browseHostFiles(path);
                }
            } else if (item.classList.contains('file-item')) {
                // If file, select it
                const filePath = item.dataset.path;
                if (filePath) {
                    selectHostFile(filePath, item.textContent);
                }
            }
        });
    }

    // --- Form Handling ---
    const uploadForm = document.getElementById('upload-form');
    if (uploadForm) {
        uploadForm.addEventListener('submit', function(e) {
            e.preventDefault();
            console.log("Form submitted, preparing to upload file");

            const fileInput = document.getElementById('subtitle-file');
            const hostFilePath = document.getElementById('host-file-path').value;

            // Check if we have either a file upload or a host file path
            if (!fileInput.files.length && !hostFilePath) {
                alert("Please select a subtitle file to translate");
                return;
            }

            console.log("Using host file path:", hostFilePath);
            console.log("Selected file:", fileInput.files.length ? fileInput.files[0].name : "None (using host file)");

            const formData = new FormData();
            
            // If we have a host file path, add it to the form data
            if (hostFilePath) {
                formData.append('host_file_path', hostFilePath);
            } else {
                // Otherwise use the file upload
                formData.append('file', fileInput.files[0]);
            }
            
            formData.append('source_language', document.getElementById('source-language').value);
            formData.append('target_language', document.getElementById('target-language').value);
            
            // Collect special word meanings and add to the form data if any exist
            const specialMeanings = collectSpecialMeanings();
            if (specialMeanings.length > 0) {
                formData.append('special_meanings', JSON.stringify(specialMeanings));
            }

            const statusContainer = document.getElementById('status-container');
            if (statusContainer) {
                statusContainer.style.display = 'block';
                // Clear previous live status on new upload
                const liveStatusDisplay = document.getElementById('live-status-display');
                if (liveStatusDisplay) {
                    liveStatusDisplay.innerHTML = '<p>Initializing translation...</p>';
                }
                console.log("Showing status container and initializing live display");
            }

            const resultContainer = document.getElementById('result-container');
            if (resultContainer) resultContainer.style.display = 'none';

            fetch('/api/translate', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                console.log("Translation job API response:", data);
                if (data.job_id) {
                    console.log("Job ID received:", data.job_id);
                    pollJobStatus(data.job_id); // Start polling job status
                    // No need to call updateLiveStatusDisplay here, interval handles it
                } else {
                    console.error("No job ID received:", data.message || "Unknown error");
                    alert("Error: " + (data.message || "Failed to start translation job"));
                    if (statusContainer) statusContainer.style.display = 'none'; // Hide status if start failed
                }
            })
            .catch(error => {
                console.error("Error starting translation:", error);
                alert("Error: " + error.message);
                 if (statusContainer) statusContainer.style.display = 'none'; // Hide status on error
            });
        });
    } else {
        console.error("Upload form not found!");
    }

    // --- Modal Handling ---
    const modal = document.getElementById('modal');
    const closeModalBtn = document.querySelector('.close');
    if (closeModalBtn && modal) {
        closeModalBtn.addEventListener('click', function() {
            modal.style.display = 'none';
        });
    }
    
    window.addEventListener('click', function(event) {
        if (event.target == modal && modal) {
            modal.style.display = 'none';
        }
    });

    // --- Subtitle Archive ---
    loadSubtitleArchive(); // Initial load

    // --- Directory Browser ---
    const browseDirBtn = document.getElementById('browse-btn');
    
    if (browseDirBtn) {
        debug("Adding click event listener to Browse Directories button");
        browseDirBtn.addEventListener('click', function() {
            debug("Browse Directories button clicked");
            // Show the inline browser if it's hidden
            if (!browserVisible) {
                debug("Showing inline file browser");
                showInlineFileBrowser();
            }
            
            // Try to load the last browsed path if available
            const lastPath = localStorage.getItem('lastBrowsedPath') || '';
            debug(`Browsing to directory: ${lastPath || 'root'}`);
            browseInlineDirectory(lastPath);
        });
    } else {
        console.error("Browse Directories button not found");
    }

    // Set up toggle browser button
    const toggleBrowserBtn = document.getElementById('toggle-browser-btn');
    if (toggleBrowserBtn) {
        debug("Adding click event listener to toggle browser button");
        toggleBrowserBtn.addEventListener('click', function() {
            debug("Toggle browser button clicked");
            if (browserVisible) {
                hideInlineFileBrowser();
            } else {
                showInlineFileBrowser();
                // Load directory listing if it's empty
                if (document.getElementById('inline-directory-list').children.length === 0) {
                    browseInlineDirectory('');
                }
            }
        });
    } else {
        console.error("Toggle browser button not found");
    }

    // Set up inline select directory button
    const inlineSelectDirBtn = document.getElementById('inline-select-dir-btn');
    if (inlineSelectDirBtn) {
        debug("Adding click event listener to inline select directory button");
        inlineSelectDirBtn.addEventListener('click', function() {
            debug("Inline select directory button clicked");
            if (!selectedDirectory) {
                alert('Please navigate to and select a directory first');
                return;
            }
            
            // Start bulk translation with the selected directory
            startBulkTranslation(selectedDirectory);
        });
    } else {
        console.error("Inline select directory button not found");
    }

    // --- Flash Messages ---
    document.querySelectorAll('.close-flash').forEach(btn => {
        btn.addEventListener('click', function() {
            this.parentElement.style.display = 'none';
        });
    });

    // --- Live Status Updates ---
    console.log("Setting up live status updates interval");
    setInterval(updateLiveStatusDisplay, 1500); // Poll every 1.5 seconds
    updateLiveStatusDisplay(); // Initial call

    // --- View Buttons in Recent Files (Event delegation for dynamically loaded content) ---
    const subtitleArchiveContainer = document.getElementById('subtitle-archive');
    if (subtitleArchiveContainer) {
        subtitleArchiveContainer.addEventListener('click', function(event) {
            if (event.target.classList.contains('view-btn')) {
                const fileId = event.target.dataset.file;
                if (fileId) {
                    viewSubtitle(fileId);
                } else {
                    console.error("View button clicked, but no file ID found in data-file attribute.");
                }
            }
            // Handle report button clicks
            if (event.target.classList.contains('report-btn')) {
                const filename = event.target.dataset.file;
                if (filename) {
                    viewTranslationReport(filename);
                } else {
                    console.error("Report button clicked, but no file ID found in data-file attribute.");
                }
            }
            // Add similar handlers for download/delete if needed
            if (event.target.classList.contains('download')) {
                 const filename = event.target.dataset.file;
                 if (filename) {
                     downloadSubtitle(filename);
                 }
            }
            if (event.target.classList.contains('delete')) {
                 const filename = event.target.dataset.file;
                 if (filename) {
                     deleteSubtitle(filename);
                 }
            }
        });
    }

    console.log("Initialization complete.");
}); // --- End of Consolidated DOMContentLoaded Listener ---


// --- Function Definitions (pollJobStatus, updateLiveStatusDisplay, etc.) ---

// Poll for overall job completion status (distinct from live line-by-line status)
function pollJobStatus(jobId) {
    console.log(`[Job ${jobId}] Starting to poll job status.`);
    currentJobId = jobId; // Store the current job ID

    function checkStatus() {
        // Only poll if this is still the active job
        if (currentJobId !== jobId) {
            console.log(`[Job ${jobId}] Polling stopped, another job is active.`);
            return;
        }

        fetch(`/api/job_status/${jobId}`)
            .then(response => response.json())
            .then(data => {
                console.log(`[Job ${jobId}] Status:`, data);

                const statusContainer = document.getElementById('status-container');
                const resultContainer = document.getElementById('result-container');
                const resultMessage = document.getElementById('result-message');
                const downloadBtn = document.getElementById('download-btn');
                const viewBtn = document.getElementById('view-btn'); // Get the view button for single results

                if (data.status === 'completed') {
                    console.log(`[Job ${jobId}] Completed successfully.`);
                    if (resultContainer) resultContainer.style.display = 'block';
                    if (resultMessage) resultMessage.innerHTML = `<p>Translation completed successfully!</p>`;

                    if (downloadBtn) {
                        downloadBtn.style.display = 'inline-block'; // Ensure visible
                        downloadBtn.onclick = function() { window.location.href = `/download/${jobId}`; };
                    }
                    if (viewBtn) { // Ensure view button exists
                        viewBtn.style.display = 'inline-block'; // Ensure visible
                        // Use the job ID to view the result
                        viewBtn.onclick = function() { viewSubtitle(jobId); };
                    }

                    // Optionally hide the live status section after completion
                    // const liveStatusContainer = document.getElementById('live-status-container');
                    // if (liveStatusContainer) liveStatusContainer.style.display = 'none';

                    loadSubtitleArchive(); // Refresh archive list
                    currentJobId = null; // Clear current job ID
                    return; // Stop polling

                } else if (data.status === 'failed') {
                    console.error(`[Job ${jobId}] Failed: ${data.message}`);
                    if (resultContainer) resultContainer.style.display = 'block';
                    if (resultMessage) resultMessage.innerHTML = `<p class="error">Translation failed: ${data.message || 'Unknown error'}</p>`;
                    if (downloadBtn) downloadBtn.style.display = 'none';
                    if (viewBtn) viewBtn.style.display = 'none'; // Hide view button on failure

                    // Hide the main status container on failure
                    if (statusContainer) statusContainer.style.display = 'none';
                    currentJobId = null; // Clear current job ID
                    return; // Stop polling

                } else if (data.status === 'processing') {
                    // Update general status message if needed (distinct from live updates)
                    const statusMessage = document.getElementById('status-message');
                    if(statusMessage) statusMessage.textContent = data.message || 'Processing...';

                    // Continue polling
                    setTimeout(checkStatus, 2000);
                } else {
                    // Unexpected status, maybe stop polling or handle differently
                     console.warn(`[Job ${jobId}] Unexpected status: ${data.status}`);
                     setTimeout(checkStatus, 5000); // Poll less frequently
                }
            })
            .catch(error => {
                console.error(`[Job ${jobId}] Error checking job status:`, error);
                // Don't stop polling on network errors, just wait longer
                setTimeout(checkStatus, 5000);
            });
    }

    // Start the first check
    checkStatus();
}


// Update the live status display based on /api/live_status
function updateLiveStatusDisplay() {
    fetch('/api/live_status')
        .then(response => response.json())
        .then(data => {
            // Log data for debugging
            console.log("Live status data:", data);

            const liveStatusDisplay = document.getElementById('live-status-display');
            if (!liveStatusDisplay) {
                console.error("Live status display element (#live-status-display) not found!");
                return;
            }

            const statusContainer = document.getElementById('status-container');

            // Check for bulk translation mode and update global flag
            if (data.status === 'translating' || data.status === 'processing') {
                window.bulkTranslationActive = true;
            }

            // Check if we have data from the current property or directly in the response
            const hasMeaningfulDataInCurrent = data.current && 
                ((data.current.line_number && data.current.line_number > 0) || 
                 data.current.original || 
                 data.current.first_pass || 
                 data.current.final || 
                 data.current.critic || 
                 data.current.standard_critic);

            // Also check for data directly in the response (for backwards compatibility)
            const hasMeaningfulDataDirect = (data.line_number && data.line_number > 0) || 
                data.original || 
                data.first_pass || 
                data.final || 
                data.critic;

            // Use data from either source
            const hasMeaningfulData = hasMeaningfulDataInCurrent || hasMeaningfulDataDirect;
            
            // Also check if we're in an active translation state based on status and flag
            const isActiveTranslation = data.status === 'processing' || 
                                      data.status === 'translating' || 
                                      window.bulkTranslationActive === true;

            // Create a "fake" current object if needed based on top-level data
            // This helps standardize processing regardless of where the data comes from
            if (!data.current && hasMeaningfulDataDirect) {
                data.current = {
                    line_number: data.line_number || 0,
                    original: data.original || '',
                    first_pass: data.first_pass || '',
                    standard_critic: data.critic || '',
                    final: data.final || '',
                    timing: data.timing || {}
                };
            }

            // If we have actual line-by-line data to show
            if (hasMeaningfulData) {
                console.log("Found meaningful translation data, displaying live status");
                
                // Ensure the main status container is visible
                if (statusContainer && statusContainer.style.display === 'none') {
                    statusContainer.style.display = 'block';
                }

                let statusHTML = `<div class="current-translation">`;

                // Filename
                const filename = data.filename || data.current_file || '';
                if (filename) { 
                    statusHTML += `<p><strong>File:</strong> ${filename}</p>`;
                }

                // Progress (Line number / Total)
                const currentLine = data.current ? data.current.line_number : (data.current_line || data.line_number || 0);
                const totalLines = data.total_lines || 0;
                
                if (currentLine > 0 && totalLines > 0) {
                    statusHTML += `<p><strong>Progress:</strong> ${currentLine} / ${totalLines} lines</p>`;
                    const percent = Math.round((currentLine / totalLines) * 100);
                    statusHTML += `
                        <div class="progress-bar-container">
                            <div class="progress-bar" style="width: ${percent}%"></div>
                        </div>
                    `;
                } else if (currentLine > 0) {
                    statusHTML += `<p><strong>Processing Line:</strong> ${currentLine}</p>`;
                }

                // Current line details
                statusHTML += `<div class="translation-item current">`;
                statusHTML += `<h3>Current Line</h3>`;
                
                // Extract current line details from either data.current or directly from data
                const original = data.current ? data.current.original : data.original;
                const firstPass = data.current ? data.current.first_pass : data.first_pass;
                const critic = data.current ? (data.current.standard_critic || data.current.critic) : data.critic;
                const criticChanged = data.current ? data.current.critic_changed : data.critic_changed;
                const final = data.current ? data.current.final : data.final;
                const timing = data.current && data.current.timing ? data.current.timing : (data.timing || {});
                
                if (original) {
                    statusHTML += `<p><strong>Original:</strong> ${original}</p>`;
                }
                
                if (firstPass) {
                    let timingInfo = '';
                    if (timing.first_pass) {
                        timingInfo = ` <span class="timing">(${timing.first_pass.toFixed(2)}s)</span>`;
                    }
                    statusHTML += `<p><strong>First Pass:</strong> ${firstPass}${timingInfo}</p>`;
                }
                
                if (critic) {
                    let timingInfo = '';
                    let actionInfo = '';
                    
                    if (timing.critic) {
                        timingInfo = ` <span class="timing">(${timing.critic.toFixed(2)}s)</span>`;
                    }
                    
                    // Critic feedback if available
                    if (data.critic_action && data.critic_action.feedback) {
                        actionInfo = `<div class="critic-feedback"><em>${data.critic_action.feedback}</em></div>`;
                    } else if (data.current && data.current.critic_action && data.current.critic_action.feedback) {
                        actionInfo = `<div class="critic-feedback"><em>${data.current.critic_action.feedback}</em></div>`;
                    }
                    
                    statusHTML += `<p><strong>Critic:</strong> ${critic} ${criticChanged ? '<span class="improved">(Improved)</span>' : ''}${timingInfo}</p>`;
                    statusHTML += actionInfo;
                }
                
                // Display final translation (or best available)
                const finalToShow = final || critic || firstPass;
                if (finalToShow) {
                    let timingInfo = '';
                    if (timing.total) {
                        timingInfo = ` <span class="timing">(Total: ${timing.total.toFixed(2)}s)</span>`;
                    }
                    statusHTML += `<p><strong>Current Best:</strong> ${finalToShow}${timingInfo}</p>`;
                }
                statusHTML += `</div>`; // End translation-item
                statusHTML += `</div>`; // End current-translation

                // Process history data
                const processedLines = data.processed_lines || 
                                     (data.current && data.current.processed_lines) || 
                                     [];
                                     
                if (processedLines.length > 0) {
                    statusHTML += `<div class="history-section">
                        <h3>Recent Translation History</h3>
                        <div class="history-container" id="history-container">`;
                    
                    // Show the history items in reverse order (newest first)
                    processedLines.slice().reverse().forEach((line, index) => {
                        let timingInfo = '';
                        if (line.timing && line.timing.total) {
                            timingInfo = ` <span class="timing">(${line.timing.total.toFixed(2)}s)</span>`;
                        }
                        
                        const lineNum = line.line_number;
                        const isExpanded = expandedHistoryItems.has(lineNum);
                        statusHTML += `
                            <div class="history-item" data-line-number="${lineNum}">
                                <div class="history-header">
                                    <span class="line-number">Line #${lineNum}</span>
                                    <span class="expand-btn" data-line-number="${lineNum}">${isExpanded ? '▲' : '▼'}</span>
                                    ${timingInfo}
                                </div>
                                <div class="history-content" id="history-content-${lineNum}" style="display: ${isExpanded ? 'block' : 'none'};">
                                    <p><strong>Original:</strong> ${line.original || ''}</p>`;
                                    
                        if (line.first_pass) {
                            statusHTML += `<p><strong>First Pass:</strong> ${line.first_pass || ''}</p>`;
                        }
                        
                        if (line.critic || line.standard_critic) {
                            const criticText = line.critic || line.standard_critic;
                            statusHTML += `<p><strong>Critic:</strong> ${criticText} ${line.critic_changed ? '<span class="improved">(Improved)</span>' : ''}</p>`;
                        }
                        
                        // Always show final translation
                        const lineFinal = line.final || line.critic || line.standard_critic || line.first_pass || '';
                        statusHTML += `<p><strong>Final:</strong> ${lineFinal}</p>
                                </div>
                            </div>`;
                    });
                    
                    statusHTML += `</div></div>`; // End history-container and history-section
                }

                // Update the DOM with our generated HTML
                liveStatusDisplay.innerHTML = statusHTML;
                liveStatusDisplay.style.display = 'block';
                
                // Setup event handlers for collapsible history items
                setupHistoryItemEventHandlers();

            } else if (isActiveTranslation) {
                // If job is active but no line data yet, show initializing message
                if (statusContainer && statusContainer.style.display === 'none') {
                    statusContainer.style.display = 'block';
                }
                liveStatusDisplay.innerHTML = `<p>Initializing translation, please wait...</p>`;
                liveStatusDisplay.style.display = 'block';
                
                // Check for progress data and manually trigger a progress check
                if (data.status === 'translating' || data.status === 'processing') {
                    console.log("Translation is active, but no line data yet. Triggering bulk progress check...");
                    // Try to force a progress check to get more data
                    fetch('/api/progress')
                        .then(response => response.json())
                        .then(progressData => {
                            console.log("Forced progress check data:", progressData);
                            // If progress data has current info, force a live status update
                            if (progressData.current && progressData.current.original) {
                                console.log("Found line data in progress API, updating live status...");
                                setTimeout(updateLiveStatusDisplay, 500);
                            }
                        })
                        .catch(error => {
                            console.error("Error in forced progress check:", error);
                        });
                }
            } else if (data.status === 'idle' || data.status === 'completed' || data.status === 'failed') {
                // Show appropriate waiting message based on status
                if (!currentJobId && !window.bulkTranslationActive) { 
                    liveStatusDisplay.innerHTML = `<p>Waiting for translation to start...</p>`;
                } else {
                    liveStatusDisplay.innerHTML = `<p>Waiting for next line data...</p>`;
                }
            } else {
                // Default fallback
                if (!currentJobId && !window.bulkTranslationActive) {
                    liveStatusDisplay.innerHTML = `<p>Waiting for translation to start...</p>`;
                }
            }
        })
        .catch(error => {
            console.error("Error fetching live status:", error);
            const liveStatusDisplay = document.getElementById('live-status-display');
            if (liveStatusDisplay) {
                liveStatusDisplay.innerHTML = `<p class="error">Error fetching live status updates.</p>`;
            }
        });
}

// ** NEW FUNCTION ** - Set up event handlers for history items
function setupHistoryItemEventHandlers() {
    // Use a proper event delegation approach that works with dynamically created elements
    const historyContainer = document.getElementById('history-container');
    
    if (!historyContainer) {
        console.log("History container not found - no history items to display yet");
        return; // Exit gracefully - there are no history items yet
    }
    
    // Remove any existing event listeners to prevent duplication
    const newHistoryContainer = historyContainer.cloneNode(true);
    historyContainer.parentNode.replaceChild(newHistoryContainer, historyContainer);
    
    // Add the event listener to the container for delegation
    newHistoryContainer.addEventListener('click', function(event) {
        // Check if the clicked element is an expand button or its parent header
        const expandBtn = event.target.closest('.expand-btn');
        if (expandBtn) {
            const lineNum = parseInt(expandBtn.getAttribute('data-line-number'));
            if (!isNaN(lineNum)) {
                const historyContent = document.getElementById(`history-content-${lineNum}`);
                if (historyContent) {
                    // Toggle display state
                    const isVisible = historyContent.style.display !== 'none';
                    historyContent.style.display = isVisible ? 'none' : 'block';
                    // Toggle the expand button icon
                    expandBtn.textContent = isVisible ? '▼' : '▲';
                    // Track expanded/collapsed state by line number
                    if (!isVisible) {
                        expandedHistoryItems.add(lineNum);
                    } else {
                        expandedHistoryItems.delete(lineNum);
                    }
                }
            }
        }
    });
    
    console.log("History item event handlers set up successfully");
}


// --- Other Helper Functions (loadSubtitleArchive, viewSubtitle, etc.) ---

function loadSubtitleArchive() {
    const subtitleArchiveContainer = document.getElementById('subtitle-archive');
    if (!subtitleArchiveContainer) return;

    fetch('/api/list_subs')
        .then(response => response.json())
        .then(data => {
            if (data.files && data.files.length > 0) {
                renderSubtitleArchive(data.files);
            } else {
                subtitleArchiveContainer.innerHTML = '<p>No subtitle files found in the archive.</p>';
            }
        })
        .catch(error => {
            console.error('Error loading subtitle archive:', error);
            subtitleArchiveContainer.innerHTML = '<p>Error loading subtitle archive.</p>';
        });
}

function renderSubtitleArchive(files) {
     const subtitleArchiveContainer = document.getElementById('subtitle-archive');
     if (!subtitleArchiveContainer) return;

    let html = '<ul class="file-list">';
    files.forEach(file => {
        // Extract job ID or identifier if possible, otherwise use filename
        // This depends on how files are named/stored
        const fileId = file; // Assuming filename is the ID for now
        html += `
            <li class="file-item">
                <span class="file-name">${file}</span>
                <div class="file-actions">
                    <button class="download" data-file="${file}">Download</button>
                    <button class="view-btn" data-file="${fileId}">View</button>
                    <button class="report-btn" data-file="${file}">Report</button>
                    <button class="delete" data-file="${file}">Delete</button>
                </div>
            </li>
        `;
    });
    html += '</ul>';
    subtitleArchiveContainer.innerHTML = html;
}

// Make download/delete/view functions globally accessible
// These are now primarily triggered by event delegation in DOMContentLoaded
window.downloadSubtitle = function(filename) {
    window.location.href = `/download_sub/${encodeURIComponent(filename)}`;
};

window.deleteSubtitle = function(filename) {
    if (confirm(`Are you sure you want to delete ${filename}?`)) {
        fetch(`/api/delete_sub/${encodeURIComponent(filename)}`, { method: 'DELETE' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    loadSubtitleArchive(); // Reload the archive
                } else {
                    alert(data.error || 'Failed to delete the file.');
                }
            })
            .catch(error => {
                console.error('Error deleting subtitle:', error);
                alert('Network error while trying to delete the file.');
            });
    }
};

window.viewSubtitle = function(fileIdOrName) {
    // Assuming the backend /api/view_subtitle expects the filename or job ID
    const modal = document.getElementById('modal');
    const modalTitle = document.getElementById('modal-title');
    const subtitlePreview = document.getElementById('subtitle-preview');

    if (!modal || !modalTitle || !subtitlePreview) {
        console.error("Modal elements not found for viewing subtitle.");
        return;
    }

    fetch(`/api/view_subtitle/${encodeURIComponent(fileIdOrName)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                modalTitle.textContent = data.filename || 'Subtitle Preview'; // Use filename from response if available
                subtitlePreview.textContent = data.content || 'No content available.';
                modal.style.display = 'block';
            } else {
                alert(data.message || 'Failed to view subtitle file.');
            }
        })
        .catch(error => {
            console.error('Error viewing subtitle:', error);
            alert('Network error while trying to view the subtitle file.');
        });
};

window.viewTranslationReport = function(filename) {
    // Create or get the report modal
    let reportModal = document.getElementById('report-modal');
    
    // If the report modal doesn't exist yet, create it
    if (!reportModal) {
        reportModal = document.createElement('div');
        reportModal.id = 'report-modal';
        reportModal.className = 'modal';
        
        // Create modal content
        reportModal.innerHTML = `
            <div class="modal-content report-modal-content">
                <span class="close report-modal-close">&times;</span>
                <h2 id="report-modal-title">Translation Report</h2>
                <div id="report-loading">Loading report data...</div>
                <div id="report-content" class="report-content"></div>
            </div>
        `;
        
        // Add modal to the body
        document.body.appendChild(reportModal);
        
        // Add event listener to close button
        const closeBtn = reportModal.querySelector('.report-modal-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', function() {
                reportModal.style.display = 'none';
            });
        }
        
        // Close modal when clicking outside the content
        window.addEventListener('click', function(event) {
            if (event.target === reportModal) {
                reportModal.style.display = 'none';
            }
        });
    }
    
    // Show the modal and loading state
    reportModal.style.display = 'block';
    const reportLoading = document.getElementById('report-loading');
    const reportContent = document.getElementById('report-content');
    const reportTitle = document.getElementById('report-modal-title');
    
    if (reportLoading) reportLoading.style.display = 'block';
    if (reportContent) reportContent.innerHTML = '';
    
    // Fetch the translation report data
    fetch(`/api/translation_report/${encodeURIComponent(filename)}`)
        .then(response => response.json())
        .then(data => {
            if (reportLoading) reportLoading.style.display = 'none';
            
            if (data.success) {
                // Set the title
                if (reportTitle) reportTitle.textContent = `Translation Report: ${data.filename}`;
                
                // Build the report HTML
                let reportHTML = `
                    <div class="report-section">
                        <h3>File Information</h3>
                        <div class="report-grid">
                            <div><strong>Filename:</strong></div>
                            <div>${data.filename}</div>
                            
                            <div><strong>Created:</strong></div>
                            <div>${data.creation_time}</div>
                            
                            <div><strong>File Size:</strong></div>
                            <div>${data.file_size_formatted}</div>
                            
                            <div><strong>Source Language:</strong></div>
                            <div>${getLanguageName(data.source_language)}</div>
                            
                            <div><strong>Target Language:</strong></div>
                            <div>${getLanguageName(data.target_language)}</div>
                        </div>
                    </div>
                    
                    <div class="report-section">
                        <h3>Statistics</h3>
                        <div class="report-grid">
                            <div><strong>Total Subtitles:</strong></div>
                            <div>${data.total_subtitles}</div>
                            
                            <div><strong>Total Words:</strong></div>
                            <div>${data.total_words}</div>
                            
                            <div><strong>Total Characters:</strong></div>
                            <div>${data.total_chars}</div>
                            
                            <div><strong>Average Line Length:</strong></div>
                            <div>${data.avg_line_length} characters</div>
                        </div>
                    </div>
                `;
                
                // Add sample subtitles section if available
                if (data.samples && data.samples.length > 0) {
                    reportHTML += `
                        <div class="report-section">
                            <h3>Sample Subtitles</h3>
                            <div class="sample-subtitles">
                    `;
                    
                    data.samples.forEach(sample => {
                        reportHTML += `
                            <div class="subtitle-sample">
                                <div class="subtitle-index">#${sample.index}</div>
                                <div class="subtitle-time">${sample.time}</div>
                                <div class="subtitle-text">${sample.text}</div>
                            </div>
                        `;
                    });
                    
                    reportHTML += `
                            </div>
                        </div>
                    `;
                }
                
                // Add content preview section if available
                if (data.content_preview) {
                    reportHTML += `
                        <div class="report-section">
                            <h3>Content Preview</h3>
                            <pre class="content-preview">${data.content_preview}</pre>
                        </div>
                    `;
                }
                
                // Display the report
                if (reportContent) reportContent.innerHTML = reportHTML;
                
            } else {
                // Display error
                if (reportContent) {
                    reportContent.innerHTML = `
                        <div class="error-message">
                            <p>Error retrieving translation report:</p>
                            <p>${data.message || 'Unknown error'}</p>
                        </div>
                    `;
                }
            }
        })
        .catch(error => {
            console.error('Error fetching translation report:', error);
            
            if (reportLoading) reportLoading.style.display = 'none';
            if (reportContent) {
                reportContent.innerHTML = `
                    <div class="error-message">
                        <p>Network error while trying to fetch the translation report.</p>
                        <p>${error.message}</p>
                    </div>
                `;
            }
        });
};

// Helper function to get full language name from code
function getLanguageName(code) {
    const languageMap = {
        'en': 'English',
        'es': 'Spanish',
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'ru': 'Russian',
        'ja': 'Japanese',
        'ko': 'Korean',
        'zh': 'Chinese',
        'da': 'Danish',
        'nl': 'Dutch',
        'fi': 'Finnish',
        'sv': 'Swedish',
        'no': 'Norwegian'
    };
    
    return languageMap[code] || code;
}


// --- Directory Browser Functions ---
// ... (Directory browser functions remain unchanged) ...
function openDirectoryBrowser(path = '') {
    const directoryModal = document.getElementById('directory-modal');
    if(directoryModal) directoryModal.style.display = 'block';
    loadDirectories(path);
}

function loadDirectories(path) {
    selectedDirectory = ''; // Reset selection when loading new path
    const directoryList = document.getElementById('directory-list');
    const currentPathDisplay = document.getElementById('current-path');

    if (!directoryList || !currentPathDisplay) {
        console.error("Directory browser elements not found.");
        return;
    }

    fetch(`/api/browse_dirs?path=${encodeURIComponent(path)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert(data.error);
                return;
            }

            currentPath = data.current_path;
            currentPathDisplay.textContent = `Current: ${currentPath || '/'}`; // Show '/' for root

            let html = '';
            if (data.parent_path !== null && data.parent_path !== undefined) { // Check parent path exists
                html += `<li class="directory-item up-level" data-path="${data.parent_path}">.. (Up)</li>`;
            }

            if (data.directories && data.directories.length > 0) {
                data.directories.forEach(dir => {
                    html += `<li class="directory-item" data-path="${dir.path}">${dir.name}/</li>`;
                });
            } else if (!data.parent_path && (!data.directories || data.directories.length === 0)) { // Only show if no parent and no dirs
                 html += '<li class="empty-message">No subdirectories found</li>';
            }


            directoryList.innerHTML = html;

            // Add click listeners
            directoryList.querySelectorAll('.directory-item').forEach(item => {
                item.addEventListener('click', function() {
                    // Single click: Select visually and store path
                    directoryList.querySelectorAll('.directory-item').forEach(i => i.classList.remove('selected'));
                    this.classList.add('selected');
                    selectedDirectory = this.dataset.path;
                });
                 item.addEventListener('dblclick', function() {
                    // Double click: Navigate into directory
                    const newPath = this.dataset.path;
                    loadDirectories(newPath);
                });
            });
        })
        .catch(error => {
            console.error('Error loading directories:', error);
            directoryList.innerHTML = '<li class="error-message">Error loading directories</li>';
        });
}


// --- Bulk Translation Functions ---
// ... (Bulk translation functions remain unchanged) ...
function startBulkTranslation(directory) {
     const bulkTranslationStatus = document.getElementById('bulk-translation-status');
     const bulkProgressBar = document.getElementById('bulk-progress-bar');
     const bulkProgressText = document.getElementById('bulk-progress-text');
     const bulkStatusMessage = document.getElementById('bulk-status-message');
     const bulkDownloadLink = document.getElementById('bulk-download-link');
     const downloadZipLink = document.getElementById('download-zip-link');


    if (!directory) {
        alert('No directory selected.');
        return;
    }
     if (!bulkTranslationStatus || !bulkProgressBar || !bulkProgressText || !bulkStatusMessage || !bulkDownloadLink || !downloadZipLink) {
        console.error("Bulk translation status elements not found.");
        return;
    }


    bulkTranslationStatus.style.display = 'block';
    bulkProgressBar.style.width = '0%';
    bulkProgressText.textContent = '0%';
    bulkStatusMessage.textContent = 'Starting bulk translation scan...';
    bulkDownloadLink.style.display = 'none';

    fetch('/api/start-scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: directory })
    })
    .then(response => response.json())
    .then(data => {
        if (data.ok) {
            bulkStatusMessage.textContent = 'Scan started. Monitoring progress...';
            if (bulkProgressInterval) clearInterval(bulkProgressInterval);
            bulkProgressInterval = setInterval(checkBulkProgress, 2000); // Check every 2 seconds
        } else {
            bulkStatusMessage.textContent = `Error: ${data.error || 'Failed to start bulk translation'}`;
        }
    })
    .catch(error => {
        console.error('Error starting bulk translation:', error);
        bulkStatusMessage.textContent = 'Network error starting bulk translation.';
    });
}

function checkBulkProgress() {
     const bulkTranslationStatus = document.getElementById('bulk-translation-status');
     const bulkProgressBar = document.getElementById('bulk-progress-bar');
     const bulkProgressText = document.getElementById('bulk-progress-text');
     const bulkStatusMessage = document.getElementById('bulk-status-message');
     const bulkDownloadLink = document.getElementById('bulk-download-link');
     const downloadZipLink = document.getElementById('download-zip-link');

     if (!bulkTranslationStatus || !bulkProgressBar || !bulkProgressText || !bulkStatusMessage || !bulkDownloadLink || !downloadZipLink) {
        console.error("Bulk translation status elements not found during progress check.");
        if (bulkProgressInterval) clearInterval(bulkProgressInterval); // Stop polling if elements are gone
        return;
    }

    fetch('/api/progress')
        .then(response => response.json())
        .then(data => {
            console.log("Bulk progress check data:", data);
            bulkStatusMessage.textContent = data.message || 'Processing...';

            if (data.total_files > 0) {
                const progress = Math.round((data.done_files / data.total_files) * 100);
                bulkProgressBar.style.width = `${progress}%`;
                bulkProgressText.textContent = `${progress}%`;
            } else {
                 bulkProgressBar.style.width = `0%`; // Reset if no total files yet
                 bulkProgressText.textContent = `0%`;
            }

            // Force update of live translation status display if there's line-by-line data
            if (data.current && data.current.original && data.current.line_number) {
                // Set a flag to indicate bulk translation is active
                window.bulkTranslationActive = true;
                
                // Force update of the live status display to show line-by-line progress
                const liveStatusDisplay = document.getElementById('live-status-display');
                if (liveStatusDisplay && liveStatusDisplay.innerHTML.includes('Waiting for translation to start')) {
                    // If the display shows the waiting message but we have active translation data,
                    // force an immediate update of the live status display
                    updateLiveStatusDisplay();
                }
            }

            if (data.status === 'completed' || data.status === 'failed') {
                if (bulkProgressInterval) clearInterval(bulkProgressInterval);
                window.bulkTranslationActive = false;

                if (data.status === 'completed') {
                     bulkProgressBar.style.width = '100%';
                     bulkProgressText.textContent = '100%';
                     if (data.zip_path) {
                        downloadZipLink.href = `/download-zip?temp=${encodeURIComponent(data.zip_path)}`;
                        bulkDownloadLink.style.display = 'block';
                    }
                    loadSubtitleArchive(); // Refresh archive
                } else {
                    // Failed state already covered by message update
                     bulkStatusMessage.textContent = `Error: ${data.message || 'Bulk translation failed'}`;
                }
            }
        })
        .catch(error => {
            console.error('Error checking bulk progress:', error);
            // Optionally update status message on error
            // bulkStatusMessage.textContent = 'Error checking progress.';
            // Consider stopping polling after multiple errors?
        });
}

// --- File Browser Functions ---

// Show inline file browser
function showInlineFileBrowser() {
    debug("In showInlineFileBrowser()");
    const browser = document.getElementById('inline-file-browser');
    if (!browser) {
        console.error("Inline file browser element not found");
        return;
    }
    
    browser.classList.add('active');
    
    const toggleBtn = document.getElementById('toggle-browser-btn');
    if (toggleBtn) {
        toggleBtn.textContent = '🔽';
        toggleBtn.title = 'Hide file browser';
    }
    
    browserVisible = true;
    localStorage.setItem('inlineFileBrowserVisible', 'true');
    debug("Inline file browser shown");
}

// Hide inline file browser
function hideInlineFileBrowser() {
    debug("In hideInlineFileBrowser()");
    const browser = document.getElementById('inline-file-browser');
    if (!browser) {
        console.error("Inline file browser element not found");
        return;
    }
    
    browser.classList.remove('active');
    
    const toggleBtn = document.getElementById('toggle-browser-btn');
    if (toggleBtn) {
        toggleBtn.textContent = '🔍';
        toggleBtn.title = 'Show file browser';
    }
    
    browserVisible = false;
    localStorage.setItem('inlineFileBrowserVisible', 'false');
    debug("Inline file browser hidden");
}

// Browse directory - inline version
function browseInlineDirectory(path) {
    debug(`In browseInlineDirectory(), path: ${path}`);
    const dirList = document.getElementById('inline-directory-list');
    if (!dirList) {
        console.error("Directory list element not found");
        return;
    }

    dirList.innerHTML = '<li class="loading">Loading directories...</li>';
    
    // Update current path display
    const pathDisplay = document.getElementById('current-path-display');
    if (pathDisplay) {
        pathDisplay.textContent = path || 'Root Directory';
    }
    
    // Save the current path to localStorage
    if (path) {
        localStorage.setItem('lastBrowsedPath', path);
    }
    
    // Fetch directories from the server
    debug(`Fetching directories from: /api/browse_dirs?path=${encodeURIComponent(path)}`);
    fetch(`/api/browse_dirs?path=${encodeURIComponent(path)}`)
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || 'Error browsing directories');
                });
            }
            return response.json();
        })
        .then(data => {
            debug("Directory data received:", data);
            // Clear existing list
            dirList.innerHTML = '';
            
            // Get parent path from API response
            const parentPath = data.parent_path || '';
            const currentPath = data.current_path || path || '';
            
            // Add parent directory option if not at root
            if (parentPath && parentPath !== currentPath) {
                const parentItem = document.createElement('li');
                parentItem.className = 'directory-item parent';
                parentItem.innerHTML = '<span class="dir-icon">📁</span> <span class="dir-name">..</span>';
                parentItem.addEventListener('click', function() {
                    browseInlineDirectory(parentPath);
                });
                dirList.appendChild(parentItem);
            }
            
            // Process the directories array from the API response
            const directories = data.directories || [];
            if (directories.length > 0) {
                directories.forEach(dir => {
                    const listItem = document.createElement('li');
                    listItem.className = 'directory-item';
                    
                    // Create icon and name elements
                    const icon = document.createElement('span');
                    icon.className = 'dir-icon';
                    icon.textContent = '📁';
                    
                    const name = document.createElement('span');
                    name.className = 'dir-name';
                    name.textContent = dir.name;
                    
                    // Add to list item
                    listItem.appendChild(icon);
                    listItem.appendChild(name);
                    
                    // Add click handler for directories
                    listItem.addEventListener('click', function() {
                        browseInlineDirectory(dir.path);
                    });
                    
                    dirList.appendChild(listItem);
                });
                
                // Special option to select the current directory
                const selectCurrentDirItem = document.createElement('li');
                selectCurrentDirItem.className = 'directory-item select-current';
                selectCurrentDirItem.innerHTML = '<span class="dir-icon">✓</span> <span class="dir-name">Select this directory</span>';
                selectCurrentDirItem.addEventListener('click', function() {
                    selectedDirectory = currentPath;
                    
                    // Update the display with the selected directory
                    const pathDisplay = document.getElementById('current-path-display');
                    if (pathDisplay) {
                        pathDisplay.innerHTML = `<strong>Selected:</strong> ${selectedDirectory}`;
                    }
                    
                    localStorage.setItem('selectedDirectory', selectedDirectory);
                });
                dirList.appendChild(selectCurrentDirItem);
            } else {
                const emptyItem = document.createElement('li');
                emptyItem.className = 'empty-message';
                emptyItem.textContent = 'No directories found';
                dirList.appendChild(emptyItem);
            }
            
            // Update the selected directory
            selectedDirectory = currentPath;
            localStorage.setItem('selectedDirectory', selectedDirectory);
        })
        .catch(error => {
            console.error('Error browsing directories:', error);
            dirList.innerHTML = `<li class="error-message">${error.message}</li>`;
        });
}

// Start bulk translation process
function startBulkTranslation(directoryPath) {
    console.log(`Starting bulk translation for directory: ${directoryPath}`);
    
    // Show the bulk translation status
    const bulkStatus = document.getElementById('bulk-translation-status');
    if (bulkStatus) {
        bulkStatus.style.display = 'block';
    }
    
    // Update UI
    const statusMessage = document.getElementById('bulk-status-message');
    const progressBar = document.getElementById('bulk-progress-bar');
    const progressText = document.getElementById('bulk-progress-text');
    const downloadLink = document.getElementById('bulk-download-link');
    
    if (statusMessage) {
        statusMessage.textContent = `Starting bulk translation for ${directoryPath}...`;
    }
    if (progressBar) {
        progressBar.style.width = '0%';
    }
    if (progressText) {
        progressText.textContent = '0%';
    }
    if (downloadLink) {
        downloadLink.style.display = 'none';
    }
    
    // Call the API to start the scan
    fetch('/api/start-scan', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ path: directoryPath })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || 'Failed to start bulk translation');
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.ok) {
            // Start polling for progress updates
            startProgressPolling();
        } else {
            throw new Error(data.error || 'Unknown error starting translation');
        }
    })
    .catch(error => {
        console.error('Error starting bulk translation:', error);
        if (statusMessage) {
            statusMessage.textContent = `Error: ${error.message}`;
        }
        if (progressBar) {
            progressBar.style.width = '0%';
        }
    });
}

// Poll for translation progress
let progressInterval = null;
function startProgressPolling() {
    // Clear any existing interval
    if (progressInterval) {
        clearInterval(progressInterval);
    }
    
    // Poll every 2 seconds
    progressInterval = setInterval(updateBulkProgress, 2000);
    
    // Initial update
    updateBulkProgress();
}

// Update the bulk translation progress
function updateBulkProgress() {
    fetch('/api/progress')
        .then(response => response.json())
        .then(data => {
            // Update status message
            const statusMessage = document.getElementById('bulk-status-message');
            const progressBar = document.getElementById('bulk-progress-bar');
            const progressText = document.getElementById('bulk-progress-text');
            const downloadLink = document.getElementById('bulk-download-link');
            const downloadZipLink = document.getElementById('download-zip-link');
            
            if (statusMessage) {
                statusMessage.textContent = data.message || 'Processing...';
            }
            
            // Calculate and update progress bar
            let progressPercent = 0;
            if (data.total_files > 0) {
                progressPercent = Math.round((data.done_files / data.total_files) * 100);
            }
            
            if (progressBar) {
                progressBar.style.width = `${progressPercent}%`;
            }
            if (progressText) {
                progressText.textContent = `${progressPercent}%`;
            }
            
            // Check if the process is complete
            if (data.status === 'completed') {
                clearInterval(progressInterval);
                
                // Show download link if available
                if (data.zip_path && downloadLink && downloadZipLink) {
                    downloadZipLink.href = `/download-zip?temp=${encodeURIComponent(data.zip_path)}`;
                    downloadLink.style.display = 'block';
                }
            }
            
            // Check if the process failed
            if (data.status === 'failed') {
                clearInterval(progressInterval);
                if (statusMessage) {
                    statusMessage.textContent = `Error: ${data.message}`;
                }
            }
        })
        .catch(error => {
            console.error('Error updating progress:', error);
        });
}

// Function to check for active translations when page loads
function checkForActiveTranslations() {
    console.log("Checking for active translations...");
    
    // Fetch the current progress state from the server
    fetch('/api/progress')
        .then(response => response.json())
        .then(data => {
            console.log("Translation status check:", data);
            
            // Check if there's an active translation running
            if (data.status === 'scanning' || data.status === 'processing' || data.status === 'translating') {
                console.log("Active translation found:", data.status);
                isTranslationActive = true;
                
                // Show the status container
                const statusContainer = document.getElementById('status-container');
                if (statusContainer) {
                    statusContainer.style.display = 'block';
                }
                
                // If it's a bulk translation, show bulk translation status
                if (data.mode === 'bulk') {
                    console.log("Active bulk translation found");
                    const bulkStatus = document.getElementById('bulk-translation-status');
                    if (bulkStatus) {
                        bulkStatus.style.display = 'block';
                    }
                    
                    // Start progress polling
                    if (bulkProgressInterval) clearInterval(bulkProgressInterval);
                    bulkProgressInterval = setInterval(checkBulkProgress, 2000);
                }
                
                // Optionally display a message that we've reconnected to an active translation
                const statusMessage = document.getElementById('status-message');
                if (statusMessage) {
                    statusMessage.textContent = "Reconnected to active translation: " + (data.message || data.status);
                }
                
                // For single file jobs, store the job ID if available
                if (data.mode === 'single' && data.job_id) {
                    currentJobId = data.job_id;
                    // Start job polling with the recovered job ID
                    console.log(`Reconnected to translation job ${currentJobId}`);
                    pollJobStatus(currentJobId);
                }
            } else if (data.status === 'completed') {
                // Handle completed translation that wasn't acknowledged
                console.log("Found completed translation:", data);
                
                // Show the result container
                const resultContainer = document.getElementById('result-container');
                const resultMessage = document.getElementById('result-message');
                
                if (resultContainer) resultContainer.style.display = 'block';
                if (resultMessage) resultMessage.innerHTML = `<p>Translation completed: ${data.message || 'Translation completed successfully!'}</p>`;
                
                // Show download link if zip file is available for bulk translations
                if (data.mode === 'bulk' && data.zip_path) {
                    const bulkDownloadLink = document.getElementById('bulk-download-link');
                    const downloadZipLink = document.getElementById('download-zip-link');
                    
                    if (bulkDownloadLink && downloadZipLink) {
                        downloadZipLink.href = `/download-zip?temp=${encodeURIComponent(data.zip_path)}`;
                        bulkDownloadLink.style.display = 'block';
                    }
                }
                
                // Refresh the subtitle archive list
                loadSubtitleArchive();
            }
        })
        .catch(error => {
            console.error("Error checking for active translations:", error);
        });
}

// --- Special Meanings Section ---

// Add event listener for the add meaning button
document.addEventListener('DOMContentLoaded', function() {
    const addMeaningBtn = document.getElementById('add-meaning-btn');
    if (addMeaningBtn) {
        addMeaningBtn.addEventListener('click', addSpecialMeaningRow);
    }

    // Setup remove buttons for existing rows
    setupRemoveButtons();
});

// Function to add a new special meaning row
function addSpecialMeaningRow() {
    const container = document.getElementById('special-meanings-container');
    
    // Create new row
    const newRow = document.createElement('div');
    newRow.className = 'special-meaning-row';
    
    // Add input fields and remove button
    newRow.innerHTML = `
        <input type="text" class="word-input" placeholder="Word or phrase">
        <input type="text" class="meaning-input" placeholder="Meaning/context">
        <button type="button" class="remove-meaning-btn">×</button>
    `;
    
    // Add the new row to the container
    container.appendChild(newRow);
    
    // Setup the remove button for the new row
    setupRemoveButtons();
}

// Function to set up all remove buttons
function setupRemoveButtons() {
    document.querySelectorAll('.remove-meaning-btn').forEach(button => {
        // Remove existing event listeners to prevent duplicates
        const newButton = button.cloneNode(true);
        button.parentNode.replaceChild(newButton, button);
        
        // Add new event listener
        newButton.addEventListener('click', function() {
            const row = this.parentNode;
            row.remove();
        });
    });
}

// Function to collect all special meanings as an array of objects
function collectSpecialMeanings() {
    const specialMeanings = [];
    const rows = document.querySelectorAll('.special-meaning-row');
    
    rows.forEach(row => {
        const wordInput = row.querySelector('.word-input');
        const meaningInput = row.querySelector('.meaning-input');
        
        if (wordInput && meaningInput) {
            const word = wordInput.value.trim();
            const meaning = meaningInput.value.trim();
            
            if (word && meaning) {
                specialMeanings.push({ word, meaning });
            }
        }
    });
    
    console.log('Collected special meanings:', specialMeanings);
    return specialMeanings;
}

// Function to load special meanings from the file when the page loads
function loadSpecialMeaningsFromFile() {
    console.log("Loading special meanings from file...");
    
    fetch('/api/special_meanings')
        .then(response => response.json())
        .then(data => {
            if (data.success && data.meanings && data.meanings.length > 0) {
                console.log(`Loaded ${data.meanings.length} special meanings from file:`, data.meanings);
                
                // Get the container element
                const container = document.getElementById('special-meanings-container');
                if (!container) {
                    console.error("Special meanings container not found");
                    return;
                }
                
                // Clear existing rows
                container.innerHTML = '';
                
                // Create a row for each meaning
                data.meanings.forEach(meaning => {
                    const row = document.createElement('div');
                    row.className = 'special-meaning-row';
                    
                    row.innerHTML = `
                        <input type="text" class="word-input" placeholder="Word or phrase" value="${escapeHtml(meaning.word)}">
                        <input type="text" class="meaning-input" placeholder="Meaning/context" value="${escapeHtml(meaning.meaning)}">
                        <button type="button" class="remove-meaning-btn">×</button>
                    `;
                    
                    container.appendChild(row);
                });
                
                // Set up the remove buttons for the newly created rows
                setupRemoveButtons();
                
                // Add a save button if it doesn't exist
                let saveBtn = document.getElementById('save-meanings-btn');
                if (!saveBtn) {
                    saveBtn = document.createElement('button');
                    saveBtn.id = 'save-meanings-btn';
                    saveBtn.className = 'primary';
                    saveBtn.textContent = 'Save Meanings';
                    saveBtn.addEventListener('click', saveSpecialMeanings);
                    
                    // Add it after the add meaning button
                    const addBtn = document.getElementById('add-meaning-btn');
                    if (addBtn && addBtn.parentNode) {
                        addBtn.parentNode.insertBefore(saveBtn, addBtn.nextSibling);
                    }
                }
            } else {
                console.log("No special meanings found in file or error loading");
            }
        })
        .catch(error => {
            console.error("Error loading special meanings from file:", error);
        });
}

// Function to save special meanings to the file
function saveSpecialMeanings() {
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
            alert(`Saved ${meanings.length} special meanings to file`);
        } else {
            alert(`Error saving special meanings: ${data.message || 'Unknown error'}`);
        }
    })
    .catch(error => {
        console.error("Error saving special meanings:", error);
        alert(`Error saving special meanings: ${error.message}`);
    });
}

// Helper function to escape HTML to prevent XSS
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// --- Host File Browser Functions ---
function browseHostFiles(path) {
    console.log(`Browsing host files at path: ${path}`);
    const hostFileList = document.getElementById('host-file-list');
    
    // Show loading indicator
    if (hostFileList) {
        hostFileList.innerHTML = '<li class="loading">Loading files...</li>';
    }
    
    // Call the API to browse files
    fetch(`/api/browse_files?path=${encodeURIComponent(path)}`)
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || 'Error browsing files');
                });
            }
            return response.json();
        })
        .then(data => {
            console.log("Got file browser data:", data);
            
            // Clear the list
            hostFileList.innerHTML = '';
            
            // Add parent directory option if not at root
            if (data.parent_path) {
                const parentItem = document.createElement('li');
                parentItem.className = 'directory-item up-level';
                parentItem.innerHTML = '<span class="dir-icon">📁</span> <span class="dir-name">..</span>';
                parentItem.dataset.path = data.parent_path;
                hostFileList.appendChild(parentItem);
            }
            
            // Add directories
            if (data.directories && data.directories.length > 0) {
                data.directories.forEach(dir => {
                    const dirItem = document.createElement('li');
                    dirItem.className = 'directory-item';
                    dirItem.innerHTML = `<span class="dir-icon">📁</span> <span class="dir-name">${dir.name}</span>`;
                    dirItem.dataset.path = dir.path;
                    hostFileList.appendChild(dirItem);
                });
            }
            
            // Add files
            if (data.files && data.files.length > 0) {
                data.files.forEach(file => {
                    const fileItem = document.createElement('li');
                    fileItem.className = 'file-item';
                    fileItem.innerHTML = `<span class="file-icon">📄</span> <span class="file-name">${file.name}</span>`;
                    fileItem.dataset.path = file.path;
                    hostFileList.appendChild(fileItem);
                });
            }
            
            // Show message if empty
            if (!data.directories?.length && !data.files?.length) {
                const emptyItem = document.createElement('li');
                emptyItem.className = 'empty-message';
                emptyItem.textContent = 'No subtitle files found in this directory';
                hostFileList.appendChild(emptyItem);
            }
            
            // Update current path display
            const pathDisplay = document.getElementById('host-current-path');
            if (pathDisplay) {
                pathDisplay.textContent = data.current_path || 'Root';
            }
        })
        .catch(error => {
            console.error('Error browsing files:', error);
            hostFileList.innerHTML = `<li class="error-message">${error.message}</li>`;
        });
}

// Function to select a host file for translation
function selectHostFile(filePath, fileName) {
    console.log(`Selected host file: ${filePath}`);
    
    // Hide the file browser
    const hostFileBrowser = document.getElementById('host-file-browser');
    if (hostFileBrowser) {
        hostFileBrowser.style.display = 'none';
    }
    
    // Update the input field with the selected file path
    const fileInput = document.getElementById('host-file-path');
    if (fileInput) {
        fileInput.value = filePath;
    }
    
    // Update the selected file display
    const selectedFileDisplay = document.getElementById('selected-host-file');
    if (selectedFileDisplay) {
        selectedFileDisplay.textContent = fileName || filePath.split('/').pop();
        selectedFileDisplay.style.display = 'block';
    }
    
    // If we have a form, update its action to use the host file
    const form = document.getElementById('upload-form');
    if (form) {
        form.dataset.useHostFile = 'true';
    }
}