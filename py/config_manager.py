import os
import configparser
from typing import Dict, Any, Optional

class ConfigManager:
    """
    Manager class for handling configuration settings for the subtitle translator.
    Handles reading, writing, and creating default configurations.
    """
    
    def __init__(self, config_path: str) -> None:
        """
        Initialize the ConfigManager with the path to the config file.
        
        Args:
            config_path (str): Path to the configuration file
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        
        # Read the config if it exists
        if os.path.exists(config_path):
            self.config.read(config_path)
        
    def get_config(self) -> configparser.ConfigParser:
        """
        Get the current configuration object.
        
        Returns:
            configparser.ConfigParser: The configuration object
        """
        return self.config
    
    def get_config_as_dict(self) -> Dict[str, Dict[str, str]]:
        """
        Convert the current configuration to a dictionary.
        
        Returns:
            Dict[str, Dict[str, str]]: The configuration as a nested dictionary
        """
        config_dict: Dict[str, Dict[str, str]] = {}
        for section in self.config.sections():
            config_dict[section] = {}
            for key, value in self.config[section].items():
                config_dict[section][key] = value
        return config_dict
    
    def save_config(self, config_dict: Dict[str, Dict[str, Any]]) -> None:
        """
        Save a configuration dictionary to the config file.
        
        Args:
            config_dict (Dict[str, Dict[str, Any]]): Configuration as a nested dictionary
        """
        # Create a new ConfigParser
        new_config = configparser.ConfigParser()
        
        # Add sections and options from the dictionary
        for section, options in config_dict.items():
            if not new_config.has_section(section):
                new_config.add_section(section)
            
            for key, value in options.items():
                new_config.set(section, key, str(value))
        
        # Save to file
        with open(self.config_path, 'w') as f:
            new_config.write(f)
        
        # Update our config
        self.config = new_config
    
    def create_default_config(self) -> None:
        """
        Create a default configuration file.
        """
        config = configparser.ConfigParser()
        
        # General section
        config.add_section('general')
        config.set('general', 'default_source_language', 'en')
        config.set('general', 'default_target_language', 'es')
        
        # WebUI section
        config.add_section('webui')
        config.set('webui', 'host', '127.0.0.1')
        config.set('webui', 'port', '5000')
        config.set('webui', 'debug', 'false')
        
        # Translation service section
        config.add_section('translation')
        config.set('translation', 'service', 'google')
        config.set('translation', 'api_key', '')
        config.set('translation', 'use_cache', 'true')
        config.set('translation', 'cache_dir', 'translation_cache')
        
        # Save to file
        with open(self.config_path, 'w') as f:
            config.write(f)
        
        # Update our config
        self.config = config