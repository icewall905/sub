// Main JavaScript file for Subtitle Translator

// --- Global Variables ---
let currentPath = '';
let selectedDirectory = '';
let bulkProgressInterval = null;
let currentJobId = null; // Keep track of the current single translation job
// Track expanded history items by their line_number
let expandedHistoryItems = new Set();
let browserVisible = false; // File Browser State Management

// Helper function to log debug messages
function debug(message) {
    console.log(`[DEBUG] ${message}`);
}

// --- Consolidated DOMContentLoaded Listener ---
document.addEventListener('DOMContentLoaded', function() {
    console.log("Document loaded, initializing...");

    // Check if we have a saved state for the file browser visibility
    const savedState = localStorage.getItem('inlineFileBrowserVisible');
    if (savedState === 'true') {
        showInlineFileBrowser();
    }
    
    // Initialize with home directory
    if (browserVisible) {
        browseInlineDirectory('');
    }

    // --- Form Handling ---
    const uploadForm = document.getElementById('upload-form');
    if (uploadForm) {
        uploadForm.addEventListener('submit', function(e) {
            e.preventDefault();
            console.log("Form submitted, preparing to upload file");

            const fileInput = document.getElementById('subtitle-file');
            if (!fileInput) {
                console.error("File input element not found!");
                return;
            }
            console.log("File input found:", fileInput.value);

            if (!fileInput.files.length) {
                alert("Please select a subtitle file to translate");
                return;
            }
            console.log("Selected file:", fileInput.files[0].name);

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('source_language', document.getElementById('source-language').value);
            formData.append('target_language', document.getElementById('target-language').value);

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
    // console.log("Updating live status display..."); // Reduce noise, log only when data changes?

    fetch('/api/live_status')
        .then(response => response.json())
        .then(data => {
            // Log only if data indicates activity or changes from idle
            if (data.status !== 'idle' || (data.line_number && data.line_number > 0) || data.first_pass || data.final || data.critic) {
                 console.log("Live status data:", data);
            }

            const liveStatusDisplay = document.getElementById('live-status-display');
            if (!liveStatusDisplay) {
                // This should not happen if HTML is correct
                console.error("Live status display element (#live-status-display) not found!");
                return;
            }

            const statusContainer = document.getElementById('status-container'); // The overall container

            // Determine if there's active translation data to show
            // Use more specific checks based on expected data fields
            const hasMeaningfulData = (data.line_number && data.line_number > 0) || data.first_pass || data.final || data.critic;

            if (hasMeaningfulData) {
                // Ensure the main status container is visible
                if (statusContainer && statusContainer.style.display === 'none') {
                    console.log("Forcing status container visible due to live data.");
                    statusContainer.style.display = 'block';
                }

                console.log("Attempting to build and display live status HTML..."); // Add log here

                let statusHTML = `<div class="current-translation">`; // Use existing class if suitable

                // Filename
                if (data.filename) { 
                    statusHTML += `<p><strong>File:</strong> ${data.filename}</p>`;
                }

                // Progress (Line number / Total)
                // Check if BOTH current_line and total_lines are valid numbers > 0
                if (typeof data.current_line === 'number' && data.current_line > 0 &&
                    typeof data.total_lines === 'number' && data.total_lines > 0)
                {
                    statusHTML += `<p><strong>Progress:</strong> ${data.current_line} / ${data.total_lines} lines</p>`;
                    const percent = Math.round((data.current_line / data.total_lines) * 100);
                    statusHTML += `
                        <div class="progress-bar-container">
                            <div class="progress-bar" style="width: ${percent}%"></div>
                        </div>
                    `;
                } else if (data.line_number > 0) {
                     // Fallback to just showing the current line number if total isn't available yet
                     statusHTML += `<p><strong>Processing Line:</strong> ${data.line_number}</p>`;
                }

                // Translation Details for current line
                statusHTML += `<div class="translation-item current">`;
                statusHTML += `<h3>Current Line</h3>`;
                if (data.original) {
                    statusHTML += `<p><strong>Original:</strong> ${data.original}</p>`;
                }
                if (data.first_pass) {
                    // Include timing if available
                    let timingInfo = '';
                    if (data.timing && data.timing.first_pass) {
                        timingInfo = ` <span class="timing">(${data.timing.first_pass.toFixed(2)}s)</span>`;
                    }
                    statusHTML += `<p><strong>First Pass:</strong> ${data.first_pass}${timingInfo}</p>`;
                }
                
                // Enhanced critic information
                if (data.critic) {
                    // Include timing and more details about what the critic did
                    let timingInfo = '';
                    let actionInfo = '';
                    
                    if (data.timing && data.timing.critic) {
                        timingInfo = ` <span class="timing">(${data.timing.critic.toFixed(2)}s)</span>`;
                    }
                    
                    if (data.critic_action) {
                        if (data.critic_action.feedback) {
                            actionInfo = `<div class="critic-feedback"><em>${data.critic_action.feedback}</em></div>`;
                        }
                    }
                    
                    statusHTML += `<p><strong>Critic:</strong> ${data.critic} ${data.critic_changed ? '<span class="improved">(Improved)</span>' : ''}${timingInfo}</p>`;
                    statusHTML += actionInfo;
                }
                
                // Display final only if it's different from critic or first_pass, or if they don't exist
                const finalToShow = data.final || data.critic || data.first_pass;
                if (finalToShow) {
                    // Include total timing if available
                    let timingInfo = '';
                    if (data.timing && data.timing.total) {
                        timingInfo = ` <span class="timing">(Total: ${data.timing.total.toFixed(2)}s)</span>`;
                    }
                    statusHTML += `<p><strong>Current Best:</strong> ${finalToShow}${timingInfo}</p>`;
                }
                statusHTML += `</div>`; // End translation-item

                statusHTML += `</div>`; // End current-translation

                // History Section - Enhanced to show more details
                if (data.processed_lines && data.processed_lines.length > 0) {
                    statusHTML += `<div class="history-section">
                        <h3>Recent Translation History</h3>
                        <div class="history-container" id="history-container">`;
                    
                    // Show the history items in reverse order (newest first)
                    data.processed_lines.slice().reverse().forEach((line, index) => {
                        // Get timing info if available
                        let timingInfo = '';
                        if (line.timing && line.timing.total) {
                            timingInfo = ` <span class="timing">(${line.timing.total.toFixed(2)}s)</span>`;
                        }
                        
                        // Use line.line_number as the unique key
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
                                    
                        // Only show first_pass if it exists
                        if (line.first_pass) {
                            statusHTML += `<p><strong>First Pass:</strong> ${line.first_pass || ''}</p>`;
                        }
                        
                        // Only show critic if it exists
                        if (line.critic) {
                            statusHTML += `<p><strong>Critic:</strong> ${line.critic || ''} ${line.critic_changed ? '<span class="improved">(Improved)</span>' : ''}</p>`;
                        }
                        
                        // Always show final translation
                        statusHTML += `<p><strong>Final:</strong> ${line.final || line.critic || line.first_pass || ''}</p>
                                </div>
                            </div>`;
                    });
                    
                    statusHTML += `</div></div>`; // End history-container and history-section
                }

                // Update the DOM
                liveStatusDisplay.innerHTML = statusHTML;
                // Ensure the container itself is visible
                liveStatusDisplay.style.display = 'block';
                
                console.log("Live status HTML updated."); // Confirm update

                // ** NEW CODE ** - Setup event handlers AFTER DOM is updated
                setupHistoryItemEventHandlers();

            } else if (data.status === 'processing' && !hasMeaningfulData) {
                 // If job is processing but no line data yet (e.g., initializing)
                 if (statusContainer && statusContainer.style.display === 'none') {
                    statusContainer.style.display = 'block';
                 }
                 liveStatusDisplay.innerHTML = `<p>Initializing translation, please wait...</p>`;
                 liveStatusDisplay.style.display = 'block';

            } else if (data.status === 'idle' || data.status === 'completed' || data.status === 'failed') {
                // If explicitly idle, completed, or failed according to live status
                // Don't necessarily hide the container, pollJobStatus handles final state
                // Just show a waiting message if no job is active
                if (!currentJobId) { // Only show waiting if no job is supposed to be running
                    liveStatusDisplay.innerHTML = `<p>Waiting for translation to start...</p>`;
                    // Optionally hide the main status container if truly idle
                    // if (statusContainer && data.status === 'idle') {
                    //     statusContainer.style.display = 'none';
                    // }
                } else {
                    // If a job IS active but live status is idle/completed, maybe show "Waiting for next line..."
                     liveStatusDisplay.innerHTML = `<p>Waiting for next line data...</p>`;
                }

            } else {
                 // Default case or unexpected data
                 // console.log("Live status: No active data or idle.");
                 // Keep the "Waiting..." message if no job is active
                 if (!currentJobId) {
                    liveStatusDisplay.innerHTML = `<p>Waiting for translation to start...</p>`;
                 }
            }
        })
        .catch(error => {
            console.error("Error fetching live status:", error);
            // Optionally display error in the live status box
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
            bulkStatusMessage.textContent = data.message || 'Processing...';

            if (data.total_files > 0) {
                const progress = Math.round((data.done_files / data.total_files) * 100);
                bulkProgressBar.style.width = `${progress}%`;
                bulkProgressText.textContent = `${progress}%`;
            } else {
                 bulkProgressBar.style.width = `0%`; // Reset if no total files yet
                 bulkProgressText.textContent = `0%`;
            }


            if (data.status === 'completed' || data.status === 'failed') {
                if (bulkProgressInterval) clearInterval(bulkProgressInterval);

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