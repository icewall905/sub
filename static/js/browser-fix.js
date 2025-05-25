// Dedicated browser fix script
console.log("=== Browser Fix Script Loaded ===");

// Add global function for directory browser troubleshooting
window.forceBrowserVisible = function() {
    const browser = document.getElementById('inline-file-browser');
    if (browser) {
        console.log("Forcing browser visibility");
        browser.style.display = 'block';
        browser.style.zIndex = '999';
        browser.style.opacity = '1';
        browser.style.visibility = 'visible';
        browser.classList.add('active');
        return "Browser visibility forced";
    } else {
        return "Browser element not found";
    }
};

document.addEventListener('DOMContentLoaded', function() {
    console.log("Applying browser visibility fixes...");
    
    // Get direct references to relevant elements
    const inlineBrowser = document.getElementById('inline-file-browser');
    const browseBtn = document.getElementById('browse-btn');
    const toggleBtn = document.getElementById('toggle-browser-btn');
    const dirList = document.getElementById('inline-directory-list');
    
    // Ensure toggle button is visible
    if (toggleBtn) {
        toggleBtn.style.display = 'inline-block';
        console.log("Made toggle button visible");
    }
    
    // Create browser element if it's missing
    if (!inlineBrowser) {
        console.log("Browser element missing - creating a new one");
        const newBrowser = document.createElement('div');
        newBrowser.id = 'inline-file-browser';
        newBrowser.className = 'inline-file-browser';
        newBrowser.innerHTML = `
            <div class="browser-header">
                <span>Directory Browser</span>
                <span id="current-inline-path" class="current-path-display"></span>
            </div>
            <div class="browser-body">
                <ul id="inline-directory-list" class="directory-list"></ul>
            </div>
            <div class="browser-actions">
                <button type="button" id="inline-select-dir-btn" class="button primary">Translate This Directory</button>
            </div>
        `;
        
        // Find where to insert it
        const container = document.querySelector('.card');
        if (container) {
            container.appendChild(newBrowser);
            // Update our reference
            inlineBrowser = newBrowser;
            dirList = newBrowser.querySelector('.directory-list');
            console.log("Created new browser element");
        } else {
            console.error("Could not find container for browser element");
        }
    }
    
    // Add backup direct click handler for the browse button
    if (browseBtn) {
        console.log("Adding additional click handler to browse button");
        browseBtn.addEventListener('click', function(e) {
            console.log("Backup handler: Browse button clicked");
            
            if (inlineBrowser) {
                // Force display regardless of other settings
                inlineBrowser.style.display = 'block';
                inlineBrowser.classList.add('active');
                
                // Check if display worked
                setTimeout(() => {
                    console.log(`Inline browser display status: ${getComputedStyle(inlineBrowser).display}`);
                }, 10);
                
                // Try to load directory listing
                if (dirList) {
                    // Show loading indicator
                    dirList.innerHTML = '<li class="loading">Loading directory list...</li>';
                    
                    // Fetch from the API directly
                    fetch('/api/browse_dirs?path=')
                        .then(response => {
                            if (!response.ok) {
                                throw new Error(`Error ${response.status}: ${response.statusText}`);
                            }
                            
                            // Check if response is JSON (to handle HTML error pages)
                            const contentType = response.headers.get('content-type');
                            if (!contentType || !contentType.includes('application/json')) {
                                throw new Error('Server returned non-JSON response');
                            }
                            
                            return response.json();
                        })
                        .then(data => {
                            console.log("Directory data received:", data);
                            
                            // Display directories
                            let html = '';
                            if (data.directories && data.directories.length > 0) {
                                data.directories.forEach(dir => {
                                    html += `<li class="directory-item" data-path="${dir.path}">
                                              <span class="dir-icon">📁</span> ${dir.name}
                                            </li>`;
                                });
                            } else {
                                html = '<li class="empty-message">No directories found</li>';
                            }
                            dirList.innerHTML = html;
                            
                            // Add click handlers to directory items
                            dirList.querySelectorAll('.directory-item').forEach(item => {
                                item.addEventListener('click', function() {
                                    // Get path from data attribute
                                    const path = this.getAttribute('data-path');
                                    if (path && typeof browseInlineDirectory === 'function') {
                                        browseInlineDirectory(path);
                                    } else {
                                        console.log(`Would navigate to: ${path}`);
                                    }
                                });
                            });
                        })
                        .catch(error => {
                            console.error("Error fetching directories:", error);
                            dirList.innerHTML = '<li class="error-message">Error loading directories</li>';
                        });
                }
            }
        });
    }
    
    console.log("Browser fixes applied.");
});
