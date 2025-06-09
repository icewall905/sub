// Main JavaScript file for Subtitle Translator

// --- Global Variables ---
let currentPath = '';
let selectedDirectory = '';
let bulkProgressInterval = null;
let currentJobId = null; // Keep track of the current single translation job
let currentLineHistory = []; // Store line history for the current job

// Track expanded history items by their line_number - default to expanded
let expandedHistoryItems = new Set();
// Always expand all history items by default
let expandAllByDefault = true;
let browserVisible = false; // File Browser State Management
let isTranslationActive = false; // Flag to track if a translation is running
let selectedVideoPath = null;
let videoFileCache = {};
let currentVideoPath = '';

// Helper function to log debug messages
function debug(message) {
    // Corrected: Ensure template literals are properly formatted.
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
            console.log("Form submitted, attempting to process and send request.");

            try { // Add a top-level try-catch for the handler's synchronous part
                const fileInput = document.getElementById('subtitle-file');
                const hostFilePathInput = document.getElementById('host-file-path'); // Get the input element
                const hostFilePath = hostFilePathInput ? hostFilePathInput.value : ''; // Get its value safely

                // Check if we have either a file upload or a host file path
                if (!fileInput.files.length && !hostFilePath) {
                    alert("Please select a subtitle file to translate or provide a host file path.");
                    console.log("[DEBUG] Validation failed: No file selected and no host file path provided.");
                    return;
                }

                console.log("Using host file path:", hostFilePath);
                console.log("Selected file:", fileInput.files.length ? fileInput.files[0].name : "None (using host file)");

                const formData = new FormData();
                
                if (hostFilePath) {
                    formData.append('host_file_path', hostFilePath);
                } else if (fileInput.files.length > 0) { // Ensure files[0] exists
                    formData.append('file', fileInput.files[0]);
                } else {
                    console.error("[DEBUG] Critical error: No file source available despite passing validation.");
                    alert("Critical error: No file source identified. Please try again.");
                    return;
                }
                
                formData.append('source_language', document.getElementById('source-language').value);
                formData.append('target_language', document.getElementById('target-language').value);
                
                console.log("[DEBUG] #upload-form submit: Collecting special meanings...");
                const specialMeanings = collectSpecialMeanings();
                console.log("[DEBUG] #upload-form submit: Collected special meanings:", JSON.stringify(specialMeanings));
                if (specialMeanings && specialMeanings.length > 0) { // Added null check for specialMeanings
                    const specialMeaningsJson = JSON.stringify(specialMeanings);
                    formData.append('special_meanings', specialMeaningsJson);
                    console.log("[DEBUG] #upload-form submit: Appended special_meanings to formData:", specialMeaningsJson);
                } else {
                    console.log("[DEBUG] #upload-form submit: No special meanings to append or specialMeanings is null/empty.");
                }

                currentLineHistory = [];
                const viewHistoryBtn = document.getElementById('view-history-btn');
                if (viewHistoryBtn) {
                    viewHistoryBtn.style.display = 'none';
                }

                const statusContainer = document.getElementById('status-container');
                if (statusContainer) {
                    statusContainer.style.display = 'block';
                    const liveStatusDisplay = document.getElementById('live-status-display');
                    if (liveStatusDisplay) {
                        liveStatusDisplay.innerHTML = '<p>Initializing translation...</p>';
                    }
                    console.log("Showing status container and initializing live display");
                }

                const resultContainer = document.getElementById('result-container');
                if (resultContainer) resultContainer.style.display = 'none';

                console.log("[DEBUG] #upload-form submit: Preparing to send POST request to /api/translate.");
                console.log("[DEBUG] FormData entries to be sent:");
                for (var pair of formData.entries()) {
                    console.log(`[DEBUG] FormData: ${pair[0]} = ${pair[1]}`);
                }

                fetch('/api/translate', {
                    method: 'POST',
                    body: formData
                    // IMPORTANT: Do NOT manually set 'Content-Type': 'multipart/form-data'.
                    // The browser handles this automatically for FormData, including the boundary.
                })
                .then(response => {
                    console.log("[DEBUG] /api/translate RAW response object:", response);
                    console.log("[DEBUG] /api/translate response status:", response.status);
                    if (!response.ok) {
                        return response.text().then(text => {
                            console.error(`[DEBUG] /api/translate non-OK response text (status ${response.status}): ${text}`);
                            let errorMsg = `Server error: ${response.status} ${response.statusText}.`;
                            try {
                                const jsonError = JSON.parse(text);
                                if (jsonError && jsonError.message) {
                                    errorMsg += ` Message: ${jsonError.message}`;
                                } else if (jsonError && jsonError.error) {
                                    errorMsg += ` Error: ${jsonError.error}`;
                                } else if (text) {
                                    errorMsg += ` Details: ${text.substring(0, 200)}`;
                                }
                            } catch (e) {
                                if (text) {
                                     errorMsg += ` Details: ${text.substring(0,200)}`;
                                }
                            }
                            throw new Error(errorMsg);
                        }).catch(textError => {
                            console.error("[DEBUG] Error processing non-OK response text:", textError);
                            // Fallback error if response.text() or subsequent processing fails
                            throw new Error(`Server error: ${response.status} ${response.statusText}. Failed to retrieve detailed error message.`);
                        });
                    }
                    return response.json().then(data => ({ status: response.status, body: data }));
                })
                .then(({ status, body }) => {
                    console.log("[DEBUG] /api/translate response JSON body:", body);
                    if (body.job_id) {
                        console.log("Job ID received:", body.job_id);
                        pollJobStatus(body.job_id);
                    } else {
                        const errorMessage = body.message || body.error || "Unknown error, no job ID received.";
                        console.error("[DEBUG] No job ID received from /api/translate. Message:", errorMessage);
                        alert("Error: " + errorMessage);
                        if (statusContainer) statusContainer.style.display = 'none';
                    }
                })
                .catch(error => {
                    console.error("[DEBUG] Error in fetch chain for /api/translate:", error);
                    alert("Error during translation request: " + error.message);
                    if (statusContainer) statusContainer.style.display = 'none';
                });

            } catch (syncError) {
                console.error("[DEBUG] Synchronous error in uploadForm submit handler BEFORE fetch:", syncError);
                alert("A client-side error occurred before sending the request: " + syncError.message);
                const statusContainer = document.getElementById('status-container');
                if (statusContainer) statusContainer.style.display = 'none';
            }
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

    // --- History Modal Handling ---
    const historyModal = document.getElementById('history-modal');
    const closeHistoryModalBtn = document.getElementById('close-history-modal-btn');
    const viewHistoryBtnGlobal = document.getElementById('view-history-btn'); // Renamed to avoid conflict in pollJobStatus

    if (closeHistoryModalBtn && historyModal) {
        closeHistoryModalBtn.addEventListener('click', function() {
            historyModal.style.display = 'none';
        });
    }

    // Event listener for the initial view history button (if it exists before polling)
    if (viewHistoryBtnGlobal && historyModal) {
        viewHistoryBtnGlobal.addEventListener('click', function() {
            populateAndShowHistoryModal();
        });
    }
    
    window.addEventListener('click', function(event) {
        if (event.target == modal && modal) {
            modal.style.display = 'none';
        }
        if (event.target == historyModal && historyModal) { // Close history modal on outside click
            historyModal.style.display = 'none';
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
            
            // Always show the browser and update UI state
            showInlineFileBrowser();
            
            // Always reload directory content
            const lastPath = localStorage.getItem('lastBrowsedPath') || '';
            debug(`Browsing to directory: ${lastPath || 'root'}`); // Ensured template literal is correct
            browseInlineDirectory(lastPath);
            
            // Check if browser is visible after changes
            const browser = document.getElementById('inline-file-browser');
            if (browser) {
                debug(`Browser display state after click: ${getComputedStyle(browser).display}`); // Ensured template literal is correct
            }
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

    // --- Set up video file browser ---
    const browseVideoBtn = document.getElementById('browse-video-btn');
    const videoFileBrowser = document.getElementById('video-file-browser');
    const videoDirsList = document.getElementById('video-dirs-list');
    const videoFilesList = document.getElementById('video-files-list');
    const videoCurrentPath = document.getElementById('video-current-path');
    const selectedVideoPathSpan = document.getElementById('selected-video-path');
    
    if (browseVideoBtn) {
        browseVideoBtn.addEventListener('click', function() {
            debug("Video browser button clicked");
            if (videoFileBrowser.style.display === 'none' || videoFileBrowser.style.display === '') {
                videoFileBrowser.style.display = 'block';
                loadVideoDirectories(''); // Load root directories
            } else {
                videoFileBrowser.style.display = 'none';
            }
        });
    }
    
    // Function to load directories for the video file browser
    function loadVideoDirectories(path) {
        debug(`Loading video directories for path: ${path}`); // Ensured template literal is correct
        currentVideoPath = path;
        
        // Use the cache if available
        if (videoFileCache[path]) {
            renderVideoFilesBrowser(videoFileCache[path]);
            return;
        }
        
        fetch(`/api/browse_videos?path=${encodeURIComponent(path)}`) // Ensured template literal is correct
            .then(response => {
                if (!response.ok) {
                    // Try to parse error message from JSON response
                    return response.json()
                        .then(data => {
                            throw new Error(data.error || `Server error: ${response.status}`); // Ensured template literal is correct
                        })
                        .catch(jsonError => {
                            // If not JSON, throw with status
                            throw new Error(`Error ${response.status}: ${response.statusText}`); // Ensured template literal is correct
                        });
                }
                
                // Check for JSON content type
                const contentType = response.headers.get('content-type');
                if (!contentType || !contentType.includes('application/json')) {
                    throw new Error('Server returned non-JSON response (HTML instead of JSON)');
                }
                
                return response.json();
            })
            .then(data => {
                // Cache the response
                videoFileCache[path] = data;
                renderVideoFilesBrowser(data);
            })
            .catch(error => {
                console.error('Error loading directories:', error);
                videoDirsList.innerHTML = '<div class="error">Error loading directories</div>';
            });
    }
    
    // Function to render the video files browser content
    function renderVideoFilesBrowser(data) {
        if (data.error) {
            videoDirsList.innerHTML = `<div class="error">${data.error}</div>`;
            videoFilesList.innerHTML = '';
            return;
        }
        
        // Update current path display
        videoCurrentPath.textContent = data.current_path || 'Root';
        
        // Create a parent directory link if we're not at root
        let dirsHtml = '';
        if (data.parent_path !== null && data.parent_path !== '') {
            dirsHtml += `<div class="dir-item parent-dir" data-path="${data.parent_path}">
                <span class="dir-icon">📁</span>
                <span class="dir-name">..</span>
            </div>`; // Ensured template literal is correct
        }
        
        // Add all directories
        if (data.directories && data.directories.length > 0) {
            data.directories.forEach(dir => {
                dirsHtml += `<div class="dir-item" data-path="${dir.path}">
                    <span class="dir-icon">📁</span>
                    <span class="dir-name">${dir.name}</span>
                </div>`; // Ensured template literal is correct
            });
        } else if (!data.parent_path) {
            dirsHtml += '<div class="no-dirs">No directories found</div>';
        }
        
        videoDirsList.innerHTML = dirsHtml;
        
        // Add click events to directory items
        videoDirsList.querySelectorAll('.dir-item').forEach(item => {
            item.addEventListener('click', function() {
                const dirPath = this.getAttribute('data-path');
                loadVideoDirectories(dirPath);
            });
        });
        
        // Add video files
        let filesHtml = '';
        if (data.files && data.files.length > 0) {
            data.files.forEach(file => {
                filesHtml += `<div class="file-item" data-path="${file.path}">
                    <span class="file-icon">🎬</span>
                    <span class="file-name">${file.name}</span>
                </div>`; // Ensured template literal is correct
            });
        } else {
            filesHtml = '<div class="no-files">No video files found</div>';
        }
        
        videoFilesList.innerHTML = filesHtml;
        
        // Add click events to file items
        videoFilesList.querySelectorAll('.file-item').forEach(item => {
            item.addEventListener('click', function() {
                const filePath = this.getAttribute('data-path');
                const fileName = this.querySelector('.file-name').textContent;
                selectedVideoPath = filePath;
                
                // Update the display and hide the browser
                const selectedVideoPathDisplay = document.getElementById('selected-video-path-display');
                if (selectedVideoPathDisplay) {
                    selectedVideoPathDisplay.value = fileName;
                    selectedVideoPathDisplay.title = filePath;
                }
                
                selectedVideoPathSpan.textContent = fileName;
                selectedVideoPathSpan.title = filePath;
                videoFileBrowser.style.display = 'none';
                
                debug(`Selected video file: ${filePath}`); // Ensured template literal is correct
            });
        });
    }
    
    // --- Setup video transcription form ---
    const videoTranscribeForm = document.getElementById('video-transcribe-form');
    if (videoTranscribeForm) {
        videoTranscribeForm.addEventListener('submit', function(e) {
            e.preventDefault();
            
            if (!selectedVideoPath) {
                alert('Please select a video file first');
                return;
            }
            
            // Show the status container with a checking server message
            const statusContainer = document.getElementById('status-container');
            const progressBar = document.getElementById('progress-bar');
            const progressText = document.getElementById('progress-text'); // Ensure this ID matches HTML
            const statusMessage = document.getElementById('status-message');
            const liveStatusDisplay = document.getElementById('live-status-display');

            if(statusContainer) statusContainer.style.display = 'block';
            if(progressBar) {
                progressBar.style.width = '0%';
                progressBar.style.backgroundColor = '#28a745'; // Reset to green
            }
            if(progressText) progressText.textContent = '0%';
            if(statusMessage) statusMessage.textContent = 'Checking whisper server connection...';
            if(liveStatusDisplay) liveStatusDisplay.innerHTML = '<p>Checking if transcription server is available...</p>';
            
            // First check if the server is reachable
            fetch('/api/whisper/check_server')
                .then(response => response.json())
                .then(data => {
                    // Always proceed if TCP connection is successful (regardless of HTTP endpoint status)
                    // faster-whisper servers often don't implement health or root endpoints
                    if (data.success) {
                        let message = data.message;
                        if (data.partial) {
                            message = "Server appears to be running but not responding to HTTP test requests. Proceeding with transcription anyway.";
                        }
                        
                        if(statusMessage) statusMessage.textContent = 'Server is reachable, starting transcription...';
                        if(liveStatusDisplay) liveStatusDisplay.innerHTML = '<p>Preparing to send video to transcription server...</p>';
                        
                        startVideoTranscription();
                    } else {
                        // Server is not reachable at all
                        if(statusMessage) statusMessage.textContent = `Error: ${data.message}`; // Ensured template literal is correct
                        if(progressBar) progressBar.style.backgroundColor = '#ff4444';
                        if(liveStatusDisplay) liveStatusDisplay.innerHTML = `
                            <p class="error-message">Cannot connect to the transcription server at ${data.server_url}</p>
                            <p>Please check that the server is running and accessible from your network.</p>
                            <p>Error details: ${data.message}</p>
                        `; // Ensured template literal is correct
                    }
                })
                .catch(error => {
                    console.error('Error checking server availability:', error);
                    if(statusMessage) statusMessage.textContent = `Error checking server: ${error.message}`; // Ensured template literal is correct
                    if(progressBar) progressBar.style.backgroundColor = '#ff4444';
                    if(liveStatusDisplay) liveStatusDisplay.innerHTML = `
                        <p class="error-message">Network error while checking server availability.</p>
                        <p>Please check your network connection and try again.</p>
                    `; // Ensured template literal is correct
                });
        });
    }

    // Function to start video transcription after server check
    function startVideoTranscription() {
        // Get the selected language or leave empty for auto-detect
        const language = document.getElementById('video-language').value;
        
        // Create form data
        const formData = new FormData();
        formData.append('video_file_path', selectedVideoPath);
        if (language) {
            formData.append('language', language);
        }
        
        // Update status
        const statusMessage = document.getElementById('status-message');
        const liveStatusDisplay = document.getElementById('live-status-display');
        if(statusMessage) statusMessage.textContent = 'Sending video to transcription service...';
        if(liveStatusDisplay) liveStatusDisplay.innerHTML = '<p>Sending video file to server...</p>';
        
        // Make the API call
        fetch('/api/video_to_srt', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // Start polling for job status
                const jobId = data.job_id;
                currentJobId = jobId; // Set currentJobId here
                pollJobStatus(jobId); // This will now handle transcription progress internally
                
                if(statusMessage) statusMessage.textContent = 'Transcription job started successfully';
                if(liveStatusDisplay) liveStatusDisplay.innerHTML = '<p>Video uploaded, transcription in progress...</p>';
            } else {
                throw new Error(data.error || 'Failed to start transcription');
            }
        })
        .catch(error => {
            console.error('Error starting transcription:', error);
            if(statusMessage) statusMessage.textContent = `Error: ${error.message}`; // Ensured template literal is correct
            const progressBar = document.getElementById('progress-bar');
            if(progressBar) progressBar.style.backgroundColor = '#ff4444';
            if(liveStatusDisplay) liveStatusDisplay.innerHTML = `
                <p class="error-message">Error starting transcription:</p>
                <p>${error.message}</p>
            `; // Ensured template literal is correct
        });
    }

    console.log("Initialization complete.");
}); // --- End of Consolidated DOMContentLoaded Listener ---


// --- Function Definitions (pollJobStatus, updateLiveStatusDisplay, etc.) ---

// Poll for overall job completion status (distinct from live line-by-line status)
function pollJobStatus(jobId) {
    currentJobId = jobId; // Store the current job ID
    console.log("Polling status for job ID:", jobId);

    // Clear any existing interval for bulk progress if it's running
    if (bulkProgressInterval) {
        clearInterval(bulkProgressInterval);
        bulkProgressInterval = null;
    }
    
    // Reset UI elements for a new job
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const statusMessage = document.getElementById('status-message');
    const resultContainer = document.getElementById('result-container');
    const liveStatusDisplay = document.getElementById('live-status-display');
    // const viewHistoryBtnFromStatusContainer = document.getElementById('view-history-btn'); // Will get this inside .then

    // REMOVED: Initial hiding of viewHistoryBtnFromStatusContainer.
    // Its visibility will be controlled by currentLineHistory.length.

    if(progressBar) progressBar.style.width = '0%';
    if(progressText) progressText.textContent = '0%';
    if(statusMessage) statusMessage.textContent = 'Initializing...';
    if(resultContainer) resultContainer.style.display = 'none';
    if(liveStatusDisplay) liveStatusDisplay.innerHTML = '<p>Waiting for job to start...</p>' // Clear previous live status

    // Start a new interval for this job
    const intervalId = setInterval(() => {
        fetch(`/api/job_status/${jobId}`) // Ensured template literal is correct
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`); // Ensured template literal is correct
                }
                return response.json();
            })
            .then(data => {
                console.log(`[DEBUG] pollJobStatus (${jobId}): Received data:`, data);
                const progressBar = document.getElementById('progress-bar'); // Re-fetch or use outer scope var
                if(progressBar) progressBar.style.width = data.progress + '%';
                if(progressText) progressText.textContent = data.progress + '%'; // Use outer scope var
                if(statusMessage) statusMessage.textContent = data.message || 'Processing...'; // Use outer scope var

                // Only update currentLineHistory from /api/job_status if it provides a non-empty array.
                // This prevents overwriting a potentially more detailed history from /api/live_status with an empty one from this poll.
                if (data.line_history && Array.isArray(data.line_history) && data.line_history.length > 0) {
                    currentLineHistory = data.line_history;
                }

                // Set button visibility based on the global currentLineHistory
                const viewHistoryBtnFromStatusContainer = document.getElementById('view-history-btn');
                if (viewHistoryBtnFromStatusContainer) {
                    if (currentLineHistory && currentLineHistory.length > 0) {
                        viewHistoryBtnFromStatusContainer.style.display = 'inline-block';
                    } else {
                        viewHistoryBtnFromStatusContainer.style.display = 'none';
                    }
                }

                // IMPORTANT: Corrected condition for clearInterval. Only stop on terminal states.
                if (data.status === 'completed' || data.status === 'error') {
                    clearInterval(intervalId); // Stop polling
                    console.log("Job finished, clearing interval for job ID:", jobId);
                    if (data.status === 'completed') {
                        if(progressBar) progressBar.style.backgroundColor = '#28a745'; // Green for success
                        if(statusMessage) statusMessage.textContent = data.message || 'Translation completed!';
                        if(resultContainer) { // Use outer scope var
                            resultContainer.style.display = 'block';
                            // IMPORTANT: Removed the duplicate "View History" button from here.
                            resultContainer.innerHTML = `
                                <p><strong>Translation successful!</strong></p>
                                <p>Download your translated file:</p>
                                <a href="/api/download/${data.job_id}/${encodeURIComponent(data.output_filename)}" class="btn btn-success" download>Download ${data.output_filename}</a>
                            `; // Ensured template literal is correct
                            // Event listener for history button is already on viewHistoryBtnFromStatusContainer (globally or via DOMContentLoaded)
                        }
                        loadSubtitleArchive(); // Refresh archive list
                    } else { // Failed (data.status === 'error')
                        if(progressBar) progressBar.style.backgroundColor = '#dc3545'; // Red for failure
                        if(statusMessage) statusMessage.textContent = data.message || 'Translation failed.';
                        if(resultContainer) { // Use outer scope var
                             resultContainer.style.display = 'block';
                             resultContainer.innerHTML = `<p class="error-message"><strong>Translation failed:</strong> ${data.message || 'Unknown error'}</p>`; // Ensured template literal is correct
                        }
                    }
                    // The View History button in status-container is already handled by the logic
                    // that checks currentLineHistory.length after fetching data.
                    // The 'finalViewHistoryBtn' logic previously here is no longer needed.
                } else if (data.status === 'processing' || data.status === 'translating') {
                    // Live status updates are handled by updateLiveStatusDisplay
                    // History can arrive during these states, making the button visibility update (done above) important.
                }
            })
            .catch(error => {
                console.error(`[DEBUG] Error polling job status for ${jobId}:`, error);
                // Optionally update status message on error
                // bulkStatusMessage.textContent = 'Error checking progress.';
                // Consider stopping polling after multiple errors?
            });
    }, 2000); // Poll every 2 seconds
}

function populateAndShowHistoryModal() {
    const historyModal = document.getElementById('history-modal');
    const historyModalContent = document.getElementById('history-modal-content');

    if (!historyModal || !historyModalContent) {
        console.error("History modal elements not found!");
        return;
    }

    if (currentLineHistory.length === 0) {
        historyModalContent.innerHTML = '<p>No detailed history available for this translation.</p>';
    } else {
        let htmlContent = '<dl class="history-list">';
        currentLineHistory.forEach(item => {
            htmlContent += `<dt>Line ${item.line_number}</dt>`; // Ensured template literal is correct
            htmlContent += '<dd>';
            htmlContent += `<strong>Original:</strong> <pre>${escapeHtml(item.original)}</pre>`; // Ensured template literal is correct
            
            if (item.suggestions && Object.keys(item.suggestions).length > 0) {
                htmlContent += '<strong>Suggestions:</strong><ul>';
                for (const [service, text] of Object.entries(item.suggestions)) {
                    htmlContent += `<li><em>${escapeHtml(service)}:</em> <pre>${escapeHtml(text)}</pre></li>`; // Ensured template literal is correct
                }
                htmlContent += '</ul>';
            } else {
                htmlContent += '<strong>Suggestions:</strong> <p>None provided.</p>';
            }

            htmlContent += `<strong>First Pass:</strong> <pre>${item.first_pass ? escapeHtml(item.first_pass) : 'N/A'}</pre>`; // Ensured template literal is correct

            if (item.standard_critic) {
                htmlContent += '<strong>Critic Review:</strong>';
                htmlContent += '<ul>';
                htmlContent += `<li><em>Feedback:</em> <pre>${escapeHtml(item.standard_critic.feedback)}</pre></li>`; // Ensured template literal is correct
                if (item.standard_critic.made_change) {
                    htmlContent += `<li><em>Revised Text:</em> <pre>${escapeHtml(item.standard_critic.revised_text)}</pre></li>`; // Ensured template literal is correct
                    htmlContent += '<li><em>Critic Made Change:</em> Yes</li>';
                } else {
                    htmlContent += '<li><em>Critic Made Change:</em> No</li>';
                }
                htmlContent += '</ul>';
            } else {
                htmlContent += '<strong>Critic Review:</strong> <p>Not available or not enabled.</p>';
            }

            htmlContent += `<strong>Final Translation:</strong> <pre>${item.final ? escapeHtml(item.final) : 'N/A'}</pre>`; // Ensured template literal is correct

            if (item.timing) {
                htmlContent += '<strong>Timing (seconds):</strong><ul>';
                if (typeof item.timing.preprocessing === 'number') htmlContent += `<li><em>Preprocessing:</em> ${item.timing.preprocessing.toFixed(3)}s</li>`; // Ensured template literal is correct
                if (typeof item.timing.first_pass === 'number') htmlContent += `<li><em>First Pass:</em> ${item.timing.first_pass.toFixed(3)}s</li>`; // Ensured template literal is correct
                if (typeof item.timing.critic === 'number') htmlContent += `<li><em>Critic:</em> ${item.timing.critic.toFixed(3)}s</li>`; // Ensured template literal is correct
                if (typeof item.timing.total === 'number') htmlContent += `<li><em>Total Line Time:</em> ${item.timing.total.toFixed(3)}s</li>`; // Ensured template literal is correct
                htmlContent += '</ul>';
            }
            htmlContent += '</dd>';
        });
        htmlContent += '</dl>';
        historyModalContent.innerHTML = htmlContent;
    }

    historyModal.style.display = 'block';
}

function escapeHtml(unsafe) {
    if (unsafe === null || typeof unsafe === 'undefined') {
        return '';
    }
    return unsafe
         .toString()
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}


// --- Live Status Updates ---
function updateLiveStatusDisplay() {
    let critic_changed = false; // Defensively declare critic_changed
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

                let statusHTML = `<div class="current-translation">`; // Ensured template literal is correct

                // Filename
                const filename = data.filename || data.current_file || '';
                if (filename) { 
                    statusHTML += `<p><strong>File:</strong> ${filename}</p>`; // Ensured template literal is correct
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
                    `; // Ensured template literal is correct
                } else if (currentLine > 0) {
                    statusHTML += `<p><strong>Processing Line:</strong> ${currentLine}</p>`; // Ensured template literal is correct
                }

                // Current line details
                statusHTML += `<div class="translation-item current">`; // Ensured template literal is correct
                statusHTML += `<h3>Current Line</h3>`; // Ensured template literal is correct
                
                // Extract current line details from either data.current or directly from data
                const original = data.current ? data.current.original : data.original;
                const firstPass = data.current ? data.current.first_pass : data.first_pass;
                const critic = data.current ? (data.current.standard_critic || data.current.critic) : data.critic;
                const criticChanged = data.current ? data.current.critic_changed : data.critic_changed;
                const final = data.current ? data.current.final : data.final;
                const timing = data.current && data.current.timing ? data.current.timing : (data.timing || {});
                
                if (original) {
                    statusHTML += `<p><strong>Original:</strong> ${original}</p>`; // Ensured template literal is correct
                }
                
                if (firstPass) {
                    let timingInfo = '';
                    if (timing.first_pass) {
                        timingInfo = ` <span class="timing">(${timing.first_pass.toFixed(2)}s)</span>`; // Ensured template literal is correct
                    }
                    statusHTML += `<p><strong>First Pass:</strong> ${firstPass}${timingInfo}</p>`; // Ensured template literal is correct
                }
                
                if (critic) {
                    let timingInfo = '';
                    let actionInfo = '';
                    
                    if (timing.critic) {
                        timingInfo = ` <span class="timing">(${timing.critic.toFixed(2)}s)</span>`; // Ensured template literal is correct
                    }
                    
                    // Critic feedback if available
                    if (data.critic_action && data.critic_action.feedback) {
                        actionInfo = `<div class="critic-feedback"><em>${data.critic_action.feedback}</em></div>`; // Ensured template literal is correct
                    } else if (data.current && data.current.critic_action && data.current.critic_action.feedback) {
                        actionInfo = `<div class="critic-feedback"><em>${data.current.critic_action.feedback}</em></div>`; // Ensured template literal is correct
                    }
                    
                    statusHTML += `<p><strong>Critic:</strong> ${critic} ${criticChanged ? '<span class="improved">(Improved)</span>' : ''}${timingInfo}</p>`; // Ensured template literal is correct
                    statusHTML += actionInfo;
                }
                
                // Display final translation (or best available)
                const finalToShow = final || critic || firstPass;
                if (finalToShow) {
                    let timingInfo = '';
                    if (timing.total) {
                        timingInfo = ` <span class="timing">(Total: ${timing.total.toFixed(2)}s)</span>`; // Ensured template literal is correct
                    }
                    
                    // Include critic feedback in parentheses after the final translation if available
                    let feedbackInfo = '';
                    if (critic_changed && data.current && data.current.critic_action && data.current.critic_action.feedback) {
                        feedbackInfo = ` <span class="critic-comment">(${data.current.critic_action.feedback})</span>`; // Ensured template literal is correct
                    }
                    
                    statusHTML += `<p><strong>Current Best:</strong> ${finalToShow}${feedbackInfo}${timingInfo}</p>`; // Ensured template literal is correct
                }
                statusHTML += `</div>`; // End translation-item // Ensured template literal is correct
                statusHTML += `</div>`; // End current-translation // Ensured template literal is correct

                // Process history data
                const processedLines = data.processed_lines || 
                                     (data.current && data.current.processed_lines) || 
                                     [];
            
                // Update global currentLineHistory if processed_lines has data from live_status
                // This allows pollJobStatus to also show the View History button based on these live updates
                if (processedLines.length > 0) {
                    currentLineHistory = processedLines; // Assign directly from live updates
                    console.log("[DEBUG] updateLiveStatusDisplay: Updated global currentLineHistory. Length:", currentLineHistory.length);

                    // Attempt to show the button immediately for responsiveness
                    const viewHistoryBtn = document.getElementById('view-history-btn');
                    if (viewHistoryBtn) {
                        const computedStyle = window.getComputedStyle(viewHistoryBtn);
                        if (computedStyle.display === 'none' || viewHistoryBtn.style.display === 'none') {
                            viewHistoryBtn.style.display = 'inline-block';
                            console.log("[DEBUG] updateLiveStatusDisplay: Made view-history-btn visible directly.");
                        }
                    }
                }
                // Note: pollJobStatus will also manage button visibility based on currentLineHistory.
                // Hiding the button if history becomes empty is primarily handled by pollJobStatus.
                                     
                if (processedLines.length > 0) { // This is the existing block for building HTML for display
                    statusHTML += `<div class="history-section">
                        <h3>Recent Translation History</h3>
                        <div class="history-container" id="history-container">`; // Ensured template literal is correct
                    
                    // Show the history items in reverse order (newest first)
                    processedLines.slice().reverse().forEach((line, index) => {
                        let timingInfo = '';
                        if (line.timing && line.timing.total) {
                            timingInfo = ` <span class="timing">(${line.timing.total.toFixed(2)}s)</span>`; // Ensured template literal is correct
                        }
                        
                        const lineNum = line.line_number;
                        // Check if we should expand this item (either it's in the set or expandAllByDefault is true)
                        const isExpanded = expandAllByDefault || expandedHistoryItems.has(lineNum);
                        statusHTML += `
                            <div class="history-item" data-line-number="${lineNum}">
                                <div class="history-header">
                                    <span class="line-number">Line #${lineNum}</span>
                                    <span class="expand-btn" data-line-number="${lineNum}">${isExpanded ? '▲' : '▼'}</span>
                                    ${timingInfo}
                                </div>
                                <div class="history-content" id="history-content-${lineNum}" style="display: ${isExpanded ? 'block' : 'none'};">
                                    <p><strong>Original:</strong> ${line.original || ''}</p>`; // Ensured template literal is correct
                                    
                        if (line.first_pass) {
                            statusHTML += `<p><strong>First Pass:</strong> ${line.first_pass || ''}</p>`; // Ensured template literal is correct
                        }
                        
                        if (line.critic || line.standard_critic) {
                            const criticText = line.critic || line.standard_critic;
                            statusHTML += `<p><strong>Critic:</strong> ${criticText} ${line.critic_changed ? '<span class="improved">(Improved)</span>' : ''}</p>`; // Ensured template literal is correct
                        }
                        
                        // Always show final translation
                        const lineFinal = line.final || line.critic || line.standard_critic || line.first_pass || '';
                        
                        // Include critic feedback in parentheses after the final translation if available
                        let feedbackInfo = '';
                        if (line.critic_changed && line.critic_action && line.critic_action.feedback) {
                            feedbackInfo = ` <span class="critic-comment">(${line.critic_action.feedback})</span>`; // Ensured template literal is correct
                        }
                        
                        statusHTML += `<p><strong>Final:</strong> ${lineFinal}${feedbackInfo}</p>
                                </div>
                            </div>`; // Ensured template literal is correct
                    });
                    
                    statusHTML += `</div></div>`; // End history-container and history-section // Ensured template literal is correct
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
                liveStatusDisplay.innerHTML = `<p>Initializing translation, please wait...</p>`; // Ensured template literal is correct
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
                    liveStatusDisplay.innerHTML = `<p>Waiting for translation to start...</p>`; // Ensured template literal is correct
                } else {
                    liveStatusDisplay.innerHTML = `<p>Waiting for next line data...</p>`; // Ensured template literal is correct
                }
            } else {
                // Default fallback
                if (!currentJobId && !window.bulkTranslationActive) {
                    liveStatusDisplay.innerHTML = `<p>Waiting for translation to start...</p>`; // Ensured template literal is correct
                }
            }
        })
        .catch(error => {
            console.error("Error fetching live status:", error);
            const liveStatusDisplay = document.getElementById('live-status-display');
            if (liveStatusDisplay) {
                liveStatusDisplay.innerHTML = `<p class="error">Error fetching live status updates.</p>`; // Ensured template literal is correct
            }
        });
}

// ** NEW FUNCTION ** - Set up event handlers for history items
function setupHistoryItemEventHandlers() {
    // Set up click handlers for history item headers
    document.querySelectorAll('.history-header').forEach(header => {
        header.addEventListener('click', function(event) {
            // Check if the clicked element is an expand button or its parent header
            const expandBtn = event.target.closest('.expand-btn');
            if (expandBtn) {
                const lineNum = parseInt(expandBtn.getAttribute('data-line-number'));
                if (!isNaN(lineNum)) {
                    const historyContent = document.getElementById(`history-content-${lineNum}`); // Ensured template literal is correct
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
    });
    
    // Set up toggle all button
    const toggleAllBtn = document.getElementById('toggle-all-history');
    if (toggleAllBtn) {
        toggleAllBtn.addEventListener('click', function() {
            // Check the current state based on the button text
            const isCollapsing = toggleAllBtn.textContent.includes('Collapse');
            
            // Get all history items
            const historyItems = document.querySelectorAll('.history-item');
            historyItems.forEach(item => {
                const lineNum = parseInt(item.getAttribute('data-line-number'));
                if (!isNaN(lineNum)) {
                    const content = document.getElementById(`history-content-${lineNum}`); // Ensured template literal is correct
                    const expandBtn = item.querySelector('.expand-btn');
                    
                    if (content && expandBtn) {
                        // Set all to collapsed or expanded based on the button state
                        content.style.display = isCollapsing ? 'none' : 'block';
                        expandBtn.textContent = isCollapsing ? '▼' : '▲';
                        
                        // Update our tracking set
                        if (isCollapsing) {
                            expandedHistoryItems.delete(lineNum);
                        } else {
                            expandedHistoryItems.add(lineNum);
                        }
                    }
                }
            });
            
            // Toggle the button text
            toggleAllBtn.textContent = isCollapsing ? 'Expand All' : 'Collapse All';
            // Update our global preference
            expandAllByDefault = !isCollapsing;
        });
    }
    
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
        `; // Ensured template literal is correct
    });
    html += '</ul>';
    subtitleArchiveContainer.innerHTML = html;
}

// Make download/delete/view functions globally accessible
// These are now primarily triggered by event delegation in DOMContentLoaded
window.downloadSubtitle = function(filename) {
    window.location.href = `/download_sub/${encodeURIComponent(filename)}`; // Ensured template literal is correct
};

window.deleteSubtitle = function(filename) {
    if (confirm(`Are you sure you want to delete ${filename}?`)) { // Ensured template literal is correct
        fetch(`/api/delete_sub/${encodeURIComponent(filename)}`, { method: 'DELETE' }) // Ensured template literal is correct
            .then(response => response.json())
            .then(data => { // Corrected: added parentheses around data
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
    const modalTextContent = document.getElementById('modal-text-content');

    if (!modal || !modalTitle || !modalTextContent) {
        console.error("Modal elements not found for viewing subtitle.", {
            modal: !!modal,
            modalTitle: !!modalTitle,
            modalTextContent: !!modalTextContent
        });
        return;
    }

    fetch(`/api/view_subtitle/${encodeURIComponent(fileIdOrName)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                modalTitle.textContent = data.filename || 'Subtitle Preview'; // Use filename from response if available
                modalTextContent.textContent = data.content || 'No content available.';
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
    console.log(`Opening translation report for: ${filename}`); // Ensured template literal is correct
    
    // Create or get the report modal
    let reportModal = document.getElementById('report-modal');
    
    // If the report modal doesn't exist yet, create it
    if (!reportModal) {
        reportModal = document.createElement('div');
        reportModal.id = 'report-modal';
        reportModal.className = 'modal';
        
        // Create modal content with proper styling
        reportModal.innerHTML = `
            <div class="modal-content card report-modal-content">
                <span class="close report-modal-close">&times;</span>
                <h2 id="report-modal-title">Translation Report</h2>
                <div id="report-loading">Loading report data...</div>
                <div id="report-content" class="report-content"></div>
            </div>
        `; // Ensured template literal is correct
        
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
    console.log('Setting report modal display to block');
    reportModal.style.display = 'block';
    const reportLoading = document.getElementById('report-loading');
    const reportContent = document.getElementById('report-content');
    const reportTitle = document.getElementById('report-modal-title');
    
    if (reportLoading) reportLoading.style.display = 'block';
    if (reportContent) reportContent.innerHTML = '';
    
    // Fetch the translation report data
    fetch(`/api/translation_report/${encodeURIComponent(filename)}`) // Ensured template literal is correct
        .then(response => response.json())
        .then(data => {
            console.log('Received translation report data:', data);
            if (reportLoading) reportLoading.style.display = 'none';
            
            if (data.success) {
                // Set the title
                if (reportTitle) reportTitle.textContent = `Translation Report: ${data.filename}`; // Ensured template literal is correct
                
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
                        `; // Ensured template literal is correct
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
                    `; // Ensured template literal is correct
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
                    `; // Ensured template literal is correct
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
                `; // Ensured template literal is correct
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

    fetch(`/api/browse_dirs?path=${encodeURIComponent(path)}`) // Ensured template literal is correct
        .then(response => {
            if (!response.ok) {
                // Try to parse error message from JSON response
                return response.json()
                    .then(data => {
                        throw new Error(data.error || `Server error: ${response.status}`); // Ensured template literal is correct
                    })
                    .catch(jsonError => {
                        // If not JSON, throw with status
                        throw new Error(`Error ${response.status}: ${response.statusText}`); // Ensured template literal is correct
                    });
            }
            
            // Check for JSON content type
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                throw new Error('Server returned non-JSON response (HTML instead of JSON)');
            }
            
            return response.json();
        })
        .then(data => {
            currentPath = data.current_path;
            currentPathDisplay.textContent = `Current: ${currentPath || '/'}`; // Ensured template literal is correct

            let html = '';
            if (data.parent_path !== null && data.parent_path !== undefined) { // Check parent path exists
                html += `<li class="directory-item up-level" data-path="${data.parent_path}">.. (Up)</li>`; // Ensured template literal is correct
            }

            if (data.directories && data.directories.length > 0) {
                data.directories.forEach(dir => {
                    html += `<li class="directory-item" data-path="${dir.path}">${dir.name}/</li>`; // Ensured template literal is correct
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
                    selectedDirectory = this.getAttribute('data-path');
                    console.log("Selected directory:", selectedDirectory);
                });
            });
        })
        .catch(error => {
            console.error('Error loading directories:', error);
            directoryList.innerHTML = '<li class="error">Error loading directories</li>';
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
            bulkStatusMessage.textContent = `Error: ${data.error || 'Failed to start bulk translation'}`; // Ensured template literal is correct
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
                bulkProgressBar.style.width = `${progress}%`; // Ensured template literal is correct
                bulkProgressText.textContent = `${progress}%`; // Ensured template literal is correct
            } else {
                 bulkProgressBar.style.width = `0%`; // Ensured template literal is correct
                 bulkProgressText.textContent = `0%`; // Ensured template literal is correct
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
                        downloadZipLink.href = `/download-zip?temp=${encodeURIComponent(data.zip_path)}`; // Ensured template literal is correct
                        bulkDownloadLink.style.display = 'block';
                    }
                    loadSubtitleArchive(); // Refresh archive
                } else {
                    // Failed state already covered by message update
                     bulkStatusMessage.textContent = `Error: ${data.message || 'Bulk translation failed'}`; // Ensured template literal is correct
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
                    console.log(`Reconnected to translation job ${currentJobId}`); // Ensured template literal is correct
                    pollJobStatus(currentJobId);
                }
            } else if (data.status === 'completed') {
                // Handle completed translation that wasn't acknowledged
                console.log("Found completed translation:", data);
                
                // Show the result container
                const resultContainer = document.getElementById('result-container');
                const resultMessage = document.getElementById('result-message');
                
                if (resultContainer) resultContainer.style.display = 'block';
                if (resultMessage) resultMessage.innerHTML = `<p>Translation completed: ${data.message || 'Translation completed successfully!'}</p>`; // Ensured template literal is correct
                
                // Show download link if zip file is available for bulk translations
                if (data.mode === 'bulk' && data.zip_path) {
                    const bulkDownloadLink = document.getElementById('bulk-download-link');
                    const downloadZipLink = document.getElementById('download-zip-link');
                    
                    if (bulkDownloadLink && downloadZipLink) {
                        downloadZipLink.href = `/download-zip?temp=${encodeURIComponent(data.zip_path)}`; // Ensured template literal is correct
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
    `; // Ensured template literal is correct
    
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
    console.log("[DEBUG] collectSpecialMeanings: Function called.");
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
    
    console.log('[DEBUG] collectSpecialMeanings: Collected special meanings:', JSON.stringify(specialMeanings));
    return specialMeanings;
}

// Function to load special meanings from the file when the page loads
function loadSpecialMeaningsFromFile() {
    console.log("[DEBUG] loadSpecialMeaningsFromFile: Function called.");
    
    fetch('/api/special_meanings')
        .then(response => {
            console.log("[DEBUG] loadSpecialMeaningsFromFile: /api/special_meanings response status:", response.status);
            return response.json().then(data => ({ status: response.status, body: data }));
        })
        .then(({ status, body }) => {
            console.log("[DEBUG] loadSpecialMeaningsFromFile: /api/special_meanings response JSON body:", body);
            if (body.success && body.meanings && body.meanings.length > 0) {
                console.log(`[DEBUG] loadSpecialMeaningsFromFile: Loaded ${body.meanings.length} special meanings from file:`, JSON.stringify(body.meanings));
                
                // Get the container element
                const container = document.getElementById('special-meanings-container');
                if (!container) {
                    console.error("Special meanings container not found");
                    return;
                }
                
                // Clear existing rows
                container.innerHTML = '';
                
                // Create a row for each meaning
                body.meanings.forEach(meaning => {
                    const row = document.createElement('div');
                    row.className = 'special-meaning-row';
                    
                    row.innerHTML = `
                        <input type="text" class="word-input" placeholder="Word or phrase" value="${escapeHtml(meaning.word)}">
                        <input type="text" class="meaning-input" placeholder="Meaning/context" value="${escapeHtml(meaning.meaning)}">
                        <button type="button" class="remove-meaning-btn">×</button>
                    `; // Ensured template literal is correct
                    
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
                console.log("[DEBUG] loadSpecialMeaningsFromFile: No special meanings found in file or error loading. Response success:", body.success);
            }
        })
        .catch(error => {
            console.error("[DEBUG] loadSpecialMeaningsFromFile: Error loading special meanings from file:", error);
        });
}

// Function to save special meanings to the file
function saveSpecialMeanings() {
    console.log("[DEBUG] saveSpecialMeanings: Function called.");
    const meanings = collectSpecialMeanings();
    console.log("[DEBUG] saveSpecialMeanings: Meanings to save:", JSON.stringify(meanings));
    
    fetch('/api/special_meanings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ meanings: meanings })
    })
    .then(response => {
        console.log("[DEBUG] saveSpecialMeanings: /api/special_meanings POST response status:", response.status);
        return response.json().then(data => ({ status: response.status, body: data }));
    })
    .then(({ status, body }) => {
        console.log("[DEBUG] saveSpecialMeanings: /api/special_meanings POST response JSON body:", body);
        if (body.success) {
            alert(`Saved ${meanings.length} special meanings to file`); // Ensured template literal is correct
        } else {
            alert(`Error saving special meanings: ${body.message || 'Unknown error'}`); // Ensured template literal is correct
        }
    })
    .catch(error => {
        console.error("[DEBUG] saveSpecialMeanings: Error saving special meanings:", error);
        alert(`Error saving special meanings: ${error.message}`); // Ensured template literal is correct
    });
}

// --- Host File Browser Functions ---
function browseHostFiles(path) {
    console.log(`Browsing host files at path: ${path}`); // Ensured template literal is correct
    const hostFileList = document.getElementById('host-file-list');
    
    // Show loading indicator
    if (hostFileList) {
        hostFileList.innerHTML = '<li class="loading">Loading files...</li>';
    }
    
    // Call the API to browse files
    fetch(`/api/browse_files?path=${encodeURIComponent(path)}`)
        .then(response => {
            if (!response.ok) {
                // Try to parse error message from JSON response
                return response.json()
                    .then(data => {
                        throw new Error(data.error || `Server error: ${response.status}`); // Ensured template literal is correct
                    })
                    .catch(jsonError => {
                        // If not JSON, throw with status
                        throw new Error(`Error ${response.status}: ${response.statusText}`); // Ensured template literal is correct
                    });
            }
            
            // Check for JSON content type
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                throw new Error('Server returned non-JSON response (HTML instead of JSON)');
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
                    dirItem.innerHTML = `<span class="dir-icon">📁</span> <span class="dir-name">${dir.name}</span>`; // Ensured template literal is correct
                    dirItem.dataset.path = dir.path;
                    hostFileList.appendChild(dirItem);
                });
            }
            
            // Add files
            if (data.files && data.files.length > 0) {
                data.files.forEach(file => {
                    const fileItem = document.createElement('li');
                    fileItem.className = 'file-item';
                    fileItem.innerHTML = `<span class="file-icon">📄</span> <span class="file-name">${file.name}</span>`; // Ensured template literal is correct
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
            hostFileList.innerHTML = `<li class="error-message">${error.message}</li>`; // Ensured template literal is correct
        });
}

// Function to select a host file for translation
function selectHostFile(filePath, fileName) {
    console.log(`Selected host file: ${filePath}`); // Ensured template literal is correct
    
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