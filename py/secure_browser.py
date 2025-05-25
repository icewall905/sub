"""
Enhanced file browser security utilities.
Provides functions for secure file system operations and access validation.
"""
import os
import re
import logging
from typing import List, Tuple, Optional, Set

logger = logging.getLogger(__name__)

class SecureFileBrowser:
    """A secure file browser utility that prevents unauthorized directory access."""
    
    def __init__(
        self, 
        allowed_paths: List[str], 
        denied_patterns: Optional[List[str]] = None,
        enable_parent_navigation: bool = True,
        max_depth: int = 10,
        hide_dot_files: bool = True,
        restrict_to_media_dirs: bool = False
    ):
        """
        Initialize the secure file browser.
        
        Args:
            allowed_paths: List of allowed base paths
            denied_patterns: Optional list of glob patterns or paths that should be denied
            enable_parent_navigation: Whether to allow "up one level" navigation
            max_depth: Maximum directory depth to prevent deep traversal
            hide_dot_files: Whether to hide files and directories that start with a dot
            restrict_to_media_dirs: Whether to only show directories likely to contain media
        """
        self.allowed_paths = [os.path.abspath(p) for p in allowed_paths if p.strip()]
        self.denied_patterns = denied_patterns or []
        self.enable_parent_navigation = enable_parent_navigation
        self.max_depth = max_depth
        self.hide_dot_files = hide_dot_files
        self.restrict_to_media_dirs = restrict_to_media_dirs
        
        # Compile denied patterns into regexes for faster matching
        self._denied_regexes = []
        for pattern in self.denied_patterns:
            try:
                # Convert glob patterns to regex
                pattern = pattern.replace('.', r'\.')  # Escape dots
                pattern = pattern.replace('*', r'.*')  # Convert * to regex equivalent
                self._denied_regexes.append(re.compile(pattern))
            except Exception as e:
                logger.warning(f"Invalid denied pattern '{pattern}': {str(e)}")
        
        # Set of common media file extensions
        self.media_extensions = {
            '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', 
            '.ts', '.mts', '.m2ts', '.vob', '.3gp', '.ogv', '.divx', '.xvid'
        }
    
    def is_path_allowed(self, path: str) -> bool:
        """
        Check if a path is allowed based on the configured allowed paths.
        
        Args:
            path: The path to check
            
        Returns:
            bool: True if the path is allowed, False otherwise
        """
        if not path or not self.allowed_paths:
            return False
        
        # Normalize the path to prevent directory traversal
        abs_path = os.path.abspath(os.path.normpath(path))
        
        # Check path depth to prevent excessive nesting
        if self._get_path_depth(abs_path) > self.max_depth:
            logger.warning(f"Path exceeds maximum depth: {abs_path}")
            return False
        
        # Check if path is within any allowed base path
        is_allowed = False
        for allowed_base in self.allowed_paths:
            try:
                # Using commonpath is safer than just string operations
                if os.path.commonpath([abs_path, allowed_base]) == allowed_base:
                    is_allowed = True
                    break
            except ValueError:
                # This happens when paths are on different drives in Windows
                continue
        
        if not is_allowed:
            logger.warning(f"Path not within allowed bases: {abs_path}")
            return False
        
        # Check against denied patterns
        for regex in self._denied_regexes:
            if regex.search(abs_path):
                logger.warning(f"Path matches denied pattern: {abs_path}")
                return False
        
        return True
    
    def get_safe_parent_path(self, path: str) -> Optional[str]:
        """
        Get the parent path if it's allowed to navigate up.
        
        Args:
            path: The current path
            
        Returns:
            Optional[str]: The parent path if allowed, None otherwise
        """
        if not self.enable_parent_navigation:
            return None
        
        parent_path = os.path.dirname(path)
        
        # If we're already at the root of an allowed path, don't go higher
        if parent_path == path or not self.is_path_allowed(parent_path):
            return None
        
        return parent_path
    
    def filter_items(self, path: str, items: List[str]) -> List[str]:
        """
        Filter directory items based on security rules.
        
        Args:
            path: The base path
            items: List of filenames or directory names
            
        Returns:
            List[str]: Filtered list of items
        """
        filtered = []
        for item in items:
            # Skip hidden files/dirs if configured
            if self.hide_dot_files and item.startswith('.'):
                continue
                
            full_path = os.path.join(path, item)
            
            # Always check if the full path is allowed
            if not self.is_path_allowed(full_path):
                continue
                
            # For directories, additional checks
            if os.path.isdir(full_path):
                # No special restrictions for directories
                filtered.append(item)
            else:
                # For files, check media extensions if restrict_to_media_dirs is enabled
                _, ext = os.path.splitext(item.lower())
                if not self.restrict_to_media_dirs or ext in self.media_extensions:
                    filtered.append(item)
        
        return filtered
    
    def _get_path_depth(self, path: str) -> int:
        """
        Calculate the depth of a path.
        
        Args:
            path: The path to check
            
        Returns:
            int: The depth of the path
        """
        norm_path = os.path.normpath(path)
        return len(norm_path.split(os.sep))
