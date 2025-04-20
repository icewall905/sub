import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(name, log_file, level=logging.INFO, max_size_mb=5, backup_count=3):
    """
    Set up a logger with file and console handlers.
    
    Args:
        name: Logger name
        log_file: Path to log file
        level: Logging level
        max_size_mb: Maximum size of log file in MB
        backup_count: Number of backup files to keep
        
    Returns:
        Logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Create file handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

def get_logger(name=None):
    """
    Get an existing logger by name.
    
    Args:
        name: Logger name (defaults to root logger)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)