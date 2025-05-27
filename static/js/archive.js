// Archive page functionality
document.addEventListener('DOMContentLoaded', function() {
    // Load subtitle archive
    window.loadSubtitleArchive = function() {
        const archiveList = document.getElementById('subtitle-archive');
        if (archiveList) {
            archiveList.innerHTML = '<li class="loading-state">Loading archive...</li>';
            
            fetch('/api/list_subs')
            .then(response => response.json())
            .then(data => {
                archiveList.innerHTML = '';
                
                if (!data.files || data.files.length === 0) {
                    archiveList.innerHTML = '<li class="empty-state">No subtitles found in archive.</li>';
                    return;
                }
                
                data.files.forEach(file => {
                    const li = document.createElement('li');
                    li.className = 'file-item';
                    
                    const fileExt = file.split('.').pop().toLowerCase();
                    let iconClass = 'fas fa-file-alt';
                    if (fileExt === 'srt') {
                        iconClass = 'fas fa-closed-captioning';
                    } else if (fileExt === 'ass') {
                        iconClass = 'fas fa-file-video';
                    } else if (fileExt === 'vtt') {
                        iconClass = 'fas fa-file-video';
                    }
                    
                    // Get file modification time using a data attribute
                    const timestamp = new Date().toISOString();
                    
                    li.innerHTML = `
                        <div class="file-info">
                            <div class="file-name"><i class="${iconClass}"></i> ${file}</div>
                            <div class="file-details">
                                <span class="file-date" data-file="${file}">Recently modified</span>
                            </div>
                        </div>
                        <div class="file-actions">
                            <button class="btn btn-sm btn-info view-file-btn" data-file="${file}">
                                <i class="fas fa-eye"></i>
                            </button>
                            <a href="/download_sub/${encodeURIComponent(file)}" class="btn btn-sm btn-success">
                                <i class="fas fa-download"></i>
                            </a>
                            <button class="btn btn-sm btn-danger delete-file-btn" data-file="${file}">
                                <i class="fas fa-trash"></i>
                            </button>
                            <button class="btn btn-sm btn-primary report-btn" data-file="${file}">
                                <i class="fas fa-chart-bar"></i>
                            </button>
                        </div>
                    `;
                    
                    archiveList.appendChild(li);
                });
                
                // Add event listeners for view buttons
                document.querySelectorAll('.view-file-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const fileName = this.getAttribute('data-file');
                        viewSubtitleFile(fileName);
                    });
                });
                
                // Add event listeners for delete buttons
                document.querySelectorAll('.delete-file-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const fileName = this.getAttribute('data-file');
                        const listItem = this.closest('.file-item');
                        deleteSubtitleFile(fileName, listItem);
                    });
                });
                
                // Add event listeners for report buttons
                document.querySelectorAll('.report-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const fileName = this.getAttribute('data-file');
                        showTranslationReport(fileName);
                    });
                });
            })
            .catch(error => {
                console.error('Error loading subtitle archive:', error);
                archiveList.innerHTML = '<li class="error-state">Error loading archive: ' + error.message + '</li>';
            });
        }
    };
    
    // Load recent files
    window.loadRecentFiles = function() {
        const recentFilesList = document.getElementById('recent-files-list');
        if (recentFilesList) {
            fetch('/api/recent_files')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    recentFilesList.innerHTML = '';
                    
                    if (data.files.length === 0) {
                        recentFilesList.innerHTML = '<li class="empty-state">No recent files.</li>';
                        return;
                    }
                    
                    data.files.forEach(file => {
                        const li = document.createElement('li');
                        li.className = 'file-item';
                        
                        const fileExt = file.name.split('.').pop().toLowerCase();
                        let iconClass = 'fas fa-file-alt';
                        if (fileExt === 'srt') {
                            iconClass = 'fas fa-closed-captioning';
                        } else if (fileExt === 'ass') {
                            iconClass = 'fas fa-file-video';
                        } else if (fileExt === 'vtt') {
                            iconClass = 'fas fa-file-video';
                        }
                        
                        // Format the date
                        const date = new Date(file.date);
                        const formattedDate = date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
                        
                        li.innerHTML = `
                            <div class="file-info">
                                <div class="file-name"><i class="${iconClass}"></i> ${file.name}</div>
                                <div class="file-details">
                                    <span class="file-date">${formattedDate}</span>
                                </div>
                            </div>
                            <div class="file-actions">
                                <button class="btn btn-sm btn-info view-file-btn" data-file="${file.path}">
                                    <i class="fas fa-eye"></i>
                                </button>
                                <a href="/download_sub/${encodeURIComponent(file.path)}" class="btn btn-sm btn-success">
                                    <i class="fas fa-download"></i>
                                </a>
                            </div>
                        `;
                        
                        // Add event listener for the view button
                        li.querySelector('.view-file-btn').addEventListener('click', function() {
                            viewSubtitleFile(this.getAttribute('data-file'));
                        });
                        
                        recentFilesList.appendChild(li);
                    });
                } else {
                    recentFilesList.innerHTML = `<li class="error-state">Error: ${data.message}</li>`;
                }
            })
            .catch(error => {
                console.error('Error:', error);
                recentFilesList.innerHTML = `<li class="error-state">Error: ${error.message}</li>`;
            });
        }
    };
    
    // Filter archive based on search input
    window.filterArchive = function(searchTerm) {
        const fileItems = document.querySelectorAll('#subtitle-archive .file-item');
        searchTerm = searchTerm.toLowerCase();
        
        fileItems.forEach(item => {
            const fileName = item.querySelector('.file-name').textContent.toLowerCase();
            if (fileName.includes(searchTerm)) {
                item.style.display = '';
            } else {
                item.style.display = 'none';
            }
        });
    };
    
    // Function to view subtitle file
    function viewSubtitleFile(filePath) {
        fetch(`/api/view_subtitle/${encodeURIComponent(filePath)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
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
    
    // Function to display translation report
    function showTranslationReport(filePath) {
        fetch(`/api/translation_report/${encodeURIComponent(filePath)}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const reportModal = document.getElementById('report-modal');
                const reportTitle = document.getElementById('report-modal-title');
                const reportContent = document.getElementById('report-modal-content');
                
                reportTitle.textContent = `Translation Report: ${data.filename || filePath}`;
                
                if (data.report) {
                    reportContent.innerHTML = formatReportContent(data.report);
                } else {
                    reportContent.innerHTML = '<p>No report available for this file.</p>';
                }
                
                reportModal.style.display = 'block';
            } else {
                alert('Error loading report: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error loading report:', error);
            alert('Error loading report: ' + error.message);
        });
    }
    
    // Helper function to format report content with HTML
    function formatReportContent(report) {
        if (typeof report === 'string') {
            // Assuming report is a string with line breaks
            return report.split('\n').map(line => {
                if (line.trim().length === 0) return '<br>';
                if (line.includes(':')) {
                    const [key, value] = line.split(':', 2);
                    return `<strong>${key}:</strong> ${value}`;
                }
                return line;
            }).join('<br>');
        } else if (typeof report === 'object') {
            // Assuming report is a JSON object
            return Object.entries(report)
                .map(([key, value]) => `<strong>${key}:</strong> ${value}`)
                .join('<br>');
        }
        return String(report);
    }
    
    // Function to delete subtitle file
    function deleteSubtitleFile(filePath, listItem) {
        if (confirm('Are you sure you want to delete this file? This action cannot be undone.')) {
            fetch(`/api/delete_sub/${encodeURIComponent(filePath)}`, {
                method: 'DELETE'
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    // Remove the list item from the DOM
                    listItem.remove();
                    
                    // Check if the archive is now empty
                    const archiveList = document.getElementById('subtitle-archive');
                    if (archiveList && archiveList.childElementCount === 0) {
                        archiveList.innerHTML = '<li class="empty-state">No subtitles found in archive.</li>';
                    }
                } else {
                    alert('Error deleting file: ' + data.message);
                }
            })
            .catch(error => {
                console.error('Error deleting file:', error);
                alert('Error deleting file: ' + error.message);
            });
        }
    }
    
    // Helper function to format file size
    function formatFileSize(bytes) {
        if (bytes < 1024) {
            return bytes + ' B';
        } else if (bytes < 1024 * 1024) {
            return (bytes / 1024).toFixed(2) + ' KB';
        } else {
            return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
        }
    }
    
    // Close modals when clicking outside
    window.addEventListener('click', function(event) {
        const modal = document.getElementById('modal');
        const reportModal = document.getElementById('report-modal');
        
        if (event.target == modal) {
            modal.style.display = 'none';
        }
        
        if (event.target == reportModal) {
            reportModal.style.display = 'none';
        }
    });
    
    // Load archive on page load
    loadSubtitleArchive();
    
    // Load recent files on page load if function exists
    if (typeof loadRecentFiles === 'function') {
        loadRecentFiles();
    }
});
