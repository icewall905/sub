// Transcribe page functionality
document.addEventListener('DOMContentLoaded', function() {
    // Video file browser functionality
    const browseVideoBtn = document.getElementById('browse-video-btn');
    const videoFileBrowser = document.getElementById('video-file-browser');
    const videoPathDisplay = document.getElementById('selected-video-path-display');
    const videoPathHidden = document.getElementById('selected-video-path');
    const videoDirsList = document.getElementById('video-dirs-list');
    const videoFilesList = document.getElementById('video-files-list');
    const videoCurrentPath = document.getElementById('video-current-path');
    
    let currentVideoPath = '/';
    
    // Initialize video transcription form
    const videoTranscribeForm = document.getElementById('video-transcribe-form');
    if (videoTranscribeForm) {
        videoTranscribeForm.addEventListener('submit', function(e) {
            e.preventDefault();
            
            const videoPath = videoPathHidden.textContent;
            if (!videoPath) {
                alert('Please select a video file first.');
                return;
            }
            
            const videoLanguage = document.getElementById('video-language').value;
            
            // Show status container
            const statusContainer = document.getElementById('status-container');
            if (statusContainer) {
                statusContainer.style.display = 'block';
                document.getElementById('status-message').textContent = 'Starting transcription...';
                document.getElementById('progress-bar').style.width = '0%';
                document.getElementById('progress-text').textContent = '0%';
                document.getElementById('live-status-display').innerHTML = '<p>Initializing transcription...</p>';
            }
            
            // Send transcription request
            const formData = new FormData();
            formData.append('video_path', videoPath);
            formData.append('language', videoLanguage);
            
            // Generate a unique job ID
            const jobId = Date.now().toString(36) + Math.random().toString(36).substr(2);
            formData.append('job_id', jobId);
            
            fetch('/api/transcribe', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    // Start polling for progress
                    pollTranscriptionProgress(jobId);
                } else {
                    document.getElementById('status-message').textContent = 'Error: ' + data.message;
                    document.getElementById('live-status-display').innerHTML = '<p class="error">Transcription failed: ' + data.message + '</p>';
                }
            })
            .catch(error => {
                console.error('Error:', error);
                document.getElementById('status-message').textContent = 'Error: ' + error.message;
                document.getElementById('live-status-display').innerHTML = '<p class="error">Transcription failed: ' + error.message + '</p>';
            });
        });
    }
    
    // Video browser functionality
    if (browseVideoBtn) {
        browseVideoBtn.addEventListener('click', function() {
            if (videoFileBrowser.style.display === 'none') {
                videoFileBrowser.style.display = 'block';
                loadVideoBrowserContent(currentVideoPath);
            } else {
                videoFileBrowser.style.display = 'none';
            }
        });
    }
    
    function loadVideoBrowserContent(path) {
        videoCurrentPath.textContent = path;
        currentVideoPath = path;
        
        // Clear existing lists
        videoDirsList.innerHTML = '';
        if (document.getElementById('video-files-list')) {
            document.getElementById('video-files-list').innerHTML = '';
        }
        
        // Add parent directory option if not at root
        if (path !== '/') {
            const parentDir = path.split('/').slice(0, -2).join('/') + '/';
            const parentItem = document.createElement('li');
            parentItem.classList.add('directory-item');
            parentItem.innerHTML = '<i class="fas fa-level-up-alt"></i> Parent Directory';
            parentItem.addEventListener('click', function() {
                loadVideoBrowserContent(parentDir);
            });
            videoDirsList.appendChild(parentItem);
        }
        
        // Fetch directories and video files
        fetch(`/api/browse_dirs?path=${encodeURIComponent(path)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                data.directories.forEach(dir => {
                    const dirItem = document.createElement('li');
                    dirItem.classList.add('directory-item');
                    dirItem.innerHTML = `<i class="fas fa-folder"></i> ${dir.name}`;
                    dirItem.addEventListener('click', function() {
                        loadVideoBrowserContent(dir.path);
                    });
                    videoDirsList.appendChild(dirItem);
                });
            }
        });
        
        fetch(`/api/browse_files?path=${encodeURIComponent(path)}&filter=video`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                if (document.getElementById('video-files-list')) {
                    data.files.forEach(file => {
                        const fileItem = document.createElement('li');
                        fileItem.classList.add('file-item');
                        fileItem.innerHTML = `<i class="fas fa-file-video"></i> ${file.name}`;
                        fileItem.addEventListener('click', function() {
                            videoPathDisplay.value = file.name;
                            videoPathHidden.textContent = file.path;
                            videoFileBrowser.style.display = 'none';
                        });
                        document.getElementById('video-files-list').appendChild(fileItem);
                    });
                }
            }
        });
    }
    
    // Function to poll for transcription progress
    function pollTranscriptionProgress(jobId) {
        const progressInterval = setInterval(function() {
            fetch(`/api/transcription_progress/${jobId}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'error') {
                    clearInterval(progressInterval);
                    document.getElementById('status-message').textContent = 'Error: ' + data.message;
                    document.getElementById('live-status-display').innerHTML += '<p class="error">Transcription failed: ' + data.message + '</p>';
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
                    document.getElementById('status-message').textContent = 'Transcription complete!';
                    
                    // Show download link
                    const resultContainer = document.getElementById('result-container');
                    if (resultContainer) {
                        resultContainer.style.display = 'block';
                        resultContainer.innerHTML = `
                            <div class="download-links">
                                <a href="/download/${jobId}" class="btn btn-success">
                                    <i class="fas fa-download"></i> Download Subtitle File
                                </a>
                                <button class="btn btn-info view-file-btn" data-file="${jobId}">
                                    <i class="fas fa-eye"></i> View Subtitle
                                </button>
                            </div>
                        `;
                        
                        // Add event listener for view button
                        const viewBtn = resultContainer.querySelector('.view-file-btn');
                        if (viewBtn) {
                            viewBtn.addEventListener('click', function() {
                                const fileId = this.getAttribute('data-file');
                                viewSubtitleFile(fileId);
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
    
    // Function to view subtitle file
    function viewSubtitleFile(fileId) {
        fetch(`/api/view_subtitle/${fileId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const modal = document.getElementById('modal');
                const modalTitle = document.getElementById('modal-title');
                const modalContent = document.getElementById('modal-text-content');
                
                modalTitle.textContent = data.filename || 'Subtitle Content';
                modalContent.textContent = data.content;
                modal.style.display = 'block';
            } else {
                alert('Error viewing file: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error viewing file:', error);
            alert('Error viewing file: ' + error.message);
        });
    }
    
    // Close modal when clicking outside
    window.addEventListener('click', function(event) {
        const modal = document.getElementById('modal');
        if (event.target == modal) {
            modal.style.display = 'none';
        }
    });
});
