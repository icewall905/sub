// Log viewer JavaScript functionality
document.addEventListener('DOMContentLoaded', function() {
    const logContainer = document.getElementById('log-container');
    const refreshBtn = document.getElementById('refresh-btn');
    const autoRefreshCheckbox = document.getElementById('auto-refresh');
    const followLogsCheckbox = document.getElementById('follow-logs');
    const logLevelSelect = document.getElementById('log-level');
    const statusSpan = document.getElementById('status');
    
    let autoRefreshInterval;
    let lastTimestamp = 0;
    
    // Function to fetch logs from the server
    async function fetchLogs() {
        try {
            const response = await fetch('/api/logs');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            displayLogs(data.logs);
            
            // Update timestamp of last fetch
            lastTimestamp = new Date().getTime();
            statusSpan.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
            
            return data.logs;
        } catch (error) {
            console.error('Error fetching logs:', error);
            logContainer.innerHTML = `<span class="error">Error loading logs: ${error.message}</span>`;
            return [];
        }
    }
    
    // Function to display logs with color coding
    function displayLogs(logs) {
        if (!logs || logs.length === 0) {
            logContainer.innerHTML = '<span class="info">No logs available</span>';
            return;
        }
        
        const filteredLogs = filterLogsByLevel(logs);
        
        if (filteredLogs.length === 0) {
            logContainer.innerHTML = `<span class="info">No logs matching the selected level</span>`;
            return;
        }
        
        let html = '';
        filteredLogs.forEach(log => {
            let logClass = '';
            if (log.includes('[ERROR]')) {
                logClass = 'error';
            } else if (log.includes('[WARNING]')) {
                logClass = 'warning';
            } else if (log.includes('[INFO]')) {
                logClass = 'info';
            } else if (log.includes('[DEBUG]')) {
                logClass = 'debug';
            }
            
            html += `<div class="${logClass}">${log}</div>`;
        });
        
        logContainer.innerHTML = html;
        
        // Auto-scroll to bottom if enabled
        if (followLogsCheckbox.checked) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }
    
    // Filter logs by selected level
    function filterLogsByLevel(logs) {
        const level = logLevelSelect.value;
        
        if (level === 'all') {
            return logs;
        }
        
        return logs.filter(log => {
            if (level === 'error') {
                return log.includes('[ERROR]');
            } else if (level === 'warning') {
                return log.includes('[ERROR]') || log.includes('[WARNING]');
            } else if (level === 'info') {
                return log.includes('[ERROR]') || log.includes('[WARNING]') || log.includes('[INFO]');
            }
            return true;
        });
    }
    
    // Initial fetch
    fetchLogs();
    
    // Set up auto-refresh
    function setupAutoRefresh() {
        clearInterval(autoRefreshInterval);
        
        if (autoRefreshCheckbox.checked) {
            autoRefreshInterval = setInterval(fetchLogs, 3000); // 3 seconds
        }
    }
    
    // Event listeners
    refreshBtn.addEventListener('click', () => {
        fetchLogs();
    });
    
    autoRefreshCheckbox.addEventListener('change', setupAutoRefresh);
    
    logLevelSelect.addEventListener('change', () => {
        fetchLogs();
    });
    
    // Set up initial auto-refresh state
    setupAutoRefresh();
});