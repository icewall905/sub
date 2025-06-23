// Configuration Editor JavaScript functionality
document.addEventListener('DOMContentLoaded', function() {
    const configForm = document.getElementById('config-form');
    const configSections = document.getElementById('config-sections');
    const resetBtn = document.getElementById('reset-btn');
    const saveBtn = document.getElementById('save-btn');
    const notification = document.getElementById('notification');
    const searchInput = document.getElementById('search-config');
    
    let originalConfig = {};
    
    // Fetch configuration from the server
    async function fetchConfig() {
        try {
            const response = await fetch('/api/config');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            originalConfig = data;
            renderConfigForm(data);
            return data;
        } catch (error) {
            console.error('Error fetching configuration:', error);
            showNotification('Error loading configuration: ' + error.message, 'error');
            return null;
        }
    }
    
    // Render the configuration form
    function renderConfigForm(config) {
        configSections.innerHTML = '';
        
        // For each section in the config
        Object.keys(config).forEach(section => {
            const sectionDiv = document.createElement('div');
            sectionDiv.className = 'section';
            sectionDiv.dataset.section = section;
            
            const sectionTitle = document.createElement('h2');
            sectionTitle.textContent = formatSectionName(section);
            sectionDiv.appendChild(sectionTitle);
            
            // For each option in the section
            Object.keys(config[section]).forEach(option => {
                const formGroup = document.createElement('div');
                formGroup.className = 'form-group';
                formGroup.dataset.option = option;
                
                const label = document.createElement('label');
                label.setAttribute('for', `${section}-${option}`);
                label.textContent = formatOptionName(option);
                
                // Add a small description if available
                if (getOptionDescription(section, option)) {
                    const description = document.createElement('small');
                    description.textContent = ' - ' + getOptionDescription(section, option);
                    label.appendChild(description);
                }
                
                const input = createInputForOption(section, option, config[section][option]);
                
                formGroup.appendChild(label);
                formGroup.appendChild(input);
                sectionDiv.appendChild(formGroup);
            });
            
            configSections.appendChild(sectionDiv);
        });
    }
    
    // Create appropriate input element based on option type
    function createInputForOption(section, option, value) {
        const inputId = `${section}-${option}`;
        let input;
        
        // Special case for boolean values
        if (typeof value === 'boolean' || value === 'true' || value === 'false') {
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = (value === true || value === 'true');
        } 
        // Special case for certain known dropdowns
        else if (isSelectOption(section, option)) {
            input = document.createElement('select');
            getSelectOptions(section, option).forEach(opt => {
                const optionEl = document.createElement('option');
                optionEl.value = opt.value;
                optionEl.textContent = opt.label;
                if (value === opt.value) {
                    optionEl.selected = true;
                }
                input.appendChild(optionEl);
            });
        }
        // Default text input
        else {
            input = document.createElement('input');
            input.type = 'text';
            input.value = value;
        }
        
        input.id = inputId;
        input.name = `${section}:${option}`;
        
        return input;
    }
    
    // Format section names for display (e.g., "deepl_api" -> "DeepL API")
    function formatSectionName(section) {
        return section
            .split('_')
            .map(word => word.charAt(0).toUpperCase() + word.slice(1))
            .join(' ');
    }
    
    // Format option names for display (e.g., "api_key" -> "API Key")
    function formatOptionName(option) {
        return option
            .split('_')
            .map(word => {
                // Keep API, URL etc. uppercase
                if (['api', 'url', 'id'].includes(word.toLowerCase())) {
                    return word.toUpperCase();
                }
                return word.charAt(0).toUpperCase() + word.slice(1);
            })
            .join(' ');
    }
    
    // Check if an option should be a select dropdown
    function isSelectOption(section, option) {
        const selectOptions = {
            'general': ['default_source_language', 'default_target_language'],
            'translation_services': ['service_priority']
        };
        
        return selectOptions[section] && selectOptions[section].includes(option);
    }
    
    // Get options for select dropdowns
    function getSelectOptions(section, option) {
        if (section === 'general' && (option === 'default_source_language' || option === 'default_target_language')) {
            return [
                { value: 'en', label: 'English' },
                { value: 'es', label: 'Spanish' },
                { value: 'fr', label: 'French' },
                { value: 'de', label: 'German' },
                { value: 'it', label: 'Italian' },
                { value: 'pt', label: 'Portuguese' },
                { value: 'ru', label: 'Russian' },
                { value: 'ja', label: 'Japanese' },
                { value: 'ko', label: 'Korean' },
                { value: 'zh', label: 'Chinese' },
                { value: 'da', label: 'Danish' },
                { value: 'nl', label: 'Dutch' },
                { value: 'fi', label: 'Finnish' },
                { value: 'sv', label: 'Swedish' },
                { value: 'no', label: 'Norwegian' }
            ];
        } else if (section === 'translation_services' && option === 'service_priority') {
            return [
                { value: 'deepl,openai,ollama', label: 'DeepL → OpenAI → Ollama' },
                { value: 'openai,deepl,ollama', label: 'OpenAI → DeepL → Ollama' },
                { value: 'ollama,deepl,openai', label: 'Ollama → DeepL → OpenAI' },
                { value: 'deepl,ollama,openai', label: 'DeepL → Ollama → OpenAI' },
                { value: 'openai,ollama,deepl', label: 'OpenAI → Ollama → DeepL' },
                { value: 'ollama,openai,deepl', label: 'Ollama → OpenAI → DeepL' }
            ];
        }
        
        return [];
    }
    
    // Get descriptions for options
    function getOptionDescription(section, option) {
        const descriptions = {
            'deepl_api': {
                'api_key': 'Your DeepL API key',
                'use_pro': 'Whether to use DeepL Pro API'
            },
            'openai_api': {
                'api_key': 'Your OpenAI API key',
                'model': 'The OpenAI model to use (e.g., gpt-4)'
            },
            'ollama': {
                'enabled': 'Enable Ollama local model translation',
                'host': 'Ollama host URL',
                'model': 'Model to use (e.g., llama3)'
            },
            'general': {
                'debug_mode': 'Enable detailed logging for debugging',
                'save_intermediates': 'Save intermediate translation steps'
            },
            'translation_services': {
                'service_priority': 'Order in which translation services are tried'
            }
        };
        
        return descriptions[section] && descriptions[section][option] 
            ? descriptions[section][option] 
            : '';
    }
    
    // Show notification
    function showNotification(message, type) {
        notification.textContent = message;
        notification.className = 'notification ' + type;
        notification.style.display = 'block';
        
        // Hide after 5 seconds
        setTimeout(() => {
            notification.style.display = 'none';
        }, 5000);
    }
    
    // Save configuration
    async function saveConfig(configData) {
        try {
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(configData)
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();
            
            if (result.success) {
                showNotification('Configuration saved successfully!', 'success');
                originalConfig = configData; // Update original config
            } else {
                showNotification('Error saving configuration: ' + result.message, 'error');
            }
        } catch (error) {
            console.error('Error saving configuration:', error);
            showNotification('Error saving configuration: ' + error.message, 'error');
        }
    }
    
    // Get form data as object
    function getFormData() {
        const formData = {};
        
        document.querySelectorAll('.section').forEach(section => {
            const sectionName = section.dataset.section;
            formData[sectionName] = {};
            
            section.querySelectorAll('.form-group').forEach(group => {
                const optionName = group.dataset.option;
                const input = group.querySelector('input, select');
                
                let value;
                if (input.type === 'checkbox') {
                    value = input.checked;
                } else {
                    value = input.value;
                    if (value === 'true' || value === 'false') {
                        value = value === 'true';
                    }
                }
                
                formData[sectionName][optionName] = value;
            });
        });
        
        return formData;
    }
    
    // Handle form submission
    configForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const configData = getFormData();
        saveConfig(configData);
    });
    
    // Handle reset button
    resetBtn.addEventListener('click', function() {
        renderConfigForm(originalConfig);
        showNotification('Form reset to original values', 'info');
    });
    
    // Search functionality
    searchInput.addEventListener('input', function() {
        const searchTerm = this.value.toLowerCase();
        
        document.querySelectorAll('.section').forEach(section => {
            let sectionVisible = false;
            const sectionName = formatSectionName(section.dataset.section).toLowerCase();
            
            // If section name matches, show entire section
            if (sectionName.includes(searchTerm)) {
                section.style.display = 'block';
                sectionVisible = true;
                
                // Reset all form groups in this section
                section.querySelectorAll('.form-group').forEach(group => {
                    group.style.display = 'block';
                    group.classList.remove('highlight-search');
                });
            } else {
                // Check individual options
                section.querySelectorAll('.form-group').forEach(group => {
                    const optionName = formatOptionName(group.dataset.option).toLowerCase();
                    const inputValue = group.querySelector('input, select').value.toLowerCase();
                    const description = getOptionDescription(section.dataset.section, group.dataset.option).toLowerCase();
                    
                    if (optionName.includes(searchTerm) || inputValue.includes(searchTerm) || description.includes(searchTerm)) {
                        group.style.display = 'block';
                        group.classList.add('highlight-search');
                        sectionVisible = true;
                    } else {
                        group.style.display = 'none';
                        group.classList.remove('highlight-search');
                    }
                });
                
                section.style.display = sectionVisible ? 'block' : 'none';
            }
        });
    });
    
    // Initialize
    fetchConfig();
});