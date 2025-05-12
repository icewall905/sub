// Debug utility for browser visibility issues
console.log("Browser Visibility Debugger Loading...");

function inspectElement(id) {
    const element = document.getElementById(id);
    if (!element) {
        console.error(`Element #${id} not found`);
        return null;
    }
    
    const styles = window.getComputedStyle(element);
    console.log(`===== Element #${id} Inspection =====`);
    console.log(`Element exists: ${!!element}`);
    console.log(`Display: ${styles.display}`);
    console.log(`Visibility: ${styles.visibility}`);
    console.log(`Position: ${styles.position}`);
    console.log(`z-index: ${styles.zIndex}`);
    console.log(`Height: ${styles.height}`);
    console.log(`Width: ${styles.width}`);
    console.log(`Opacity: ${styles.opacity}`);
    console.log(`Classes: ${element.className}`);
    console.log(`Inline style: ${element.style.cssText}`);
    console.log(`Parent: ${element.parentElement?.tagName}`);
    console.log(`Siblings: ${element.parentElement?.childElementCount || 0}`);
    
    return {
        display: styles.display,
        visibility: styles.visibility,
        position: styles.position,
        zIndex: styles.zIndex,
        height: styles.height,
        width: styles.width,
        opacity: styles.opacity,
        classes: element.className,
        inlineStyle: element.style.cssText,
        parent: element.parentElement?.tagName,
        siblings: element.parentElement?.childElementCount || 0
    };
}

// Run inspection on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log("Running element inspections...");
    
    // Check the inline file browser
    const browserInfo = inspectElement('inline-file-browser');
    
    // Check the browse button
    const btnInfo = inspectElement('browse-btn');
    
    // Create a visual debugger overlay
    const debugDiv = document.createElement('div');
    debugDiv.style.position = 'fixed';
    debugDiv.style.bottom = '10px';
    debugDiv.style.right = '10px';
    debugDiv.style.backgroundColor = 'rgba(0,0,0,0.8)';
    debugDiv.style.color = '#0f0';
    debugDiv.style.padding = '10px';
    debugDiv.style.borderRadius = '5px';
    debugDiv.style.zIndex = '9999';
    debugDiv.style.fontSize = '12px';
    debugDiv.style.maxWidth = '400px';
    debugDiv.style.maxHeight = '200px';
    debugDiv.style.overflow = 'auto';
    debugDiv.style.fontFamily = 'monospace';
    
    debugDiv.innerHTML = `
        <div style="margin-bottom:5px;font-weight:bold;">Browser Visibility Debug</div>
        <div id="debug-browser-status">Browser hidden</div>
        <div style="margin-top:5px;">
            <button id="debug-toggle-browser" style="background:#333;color:#0f0;border:1px solid #0f0;padding:5px;">Toggle Browser</button>
        </div>
    `;
    
    document.body.appendChild(debugDiv);
    
    // Add functionality to debug toggle
    const debugToggle = document.getElementById('debug-toggle-browser');
    const debugStatus = document.getElementById('debug-browser-status');
    
    if (debugToggle && debugStatus) {
        debugToggle.addEventListener('click', function() {
            const browser = document.getElementById('inline-file-browser');
            if (!browser) return;
            
            if (browser.style.display === 'block') {
                browser.style.display = 'none';
                debugStatus.textContent = 'Browser hidden';
                debugStatus.style.color = '#f00';
            } else {
                browser.style.display = 'block';
                debugStatus.textContent = 'Browser visible';
                debugStatus.style.color = '#0f0';
            }
            
            // Re-inspect after toggle
            inspectElement('inline-file-browser');
        });
    }
    
    // Monitor browser visibility
    setInterval(() => {
        const browser = document.getElementById('inline-file-browser');
        if (browser) {
            const display = window.getComputedStyle(browser).display;
            if (debugStatus) {
                debugStatus.textContent = display !== 'none' ? 'Browser visible' : 'Browser hidden';
                debugStatus.style.color = display !== 'none' ? '#0f0' : '#f00';
            }
        }
    }, 1000);
});

// Add a listener for when browse button is clicked
document.addEventListener('click', function(e) {
    if (e.target && e.target.id === 'browse-btn') {
        console.log('Browse button clicked!');
        setTimeout(() => {
            console.log('Checking browser visibility after click...');
            inspectElement('inline-file-browser');
        }, 100);
    }
});

console.log("Browser Visibility Debugger Loaded!");
