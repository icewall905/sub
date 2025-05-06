#!/usr/bin/env python3

import os
import sys
import logging
import time
import tempfile
import shutil
from typing import Dict, Any, Optional, List, Tuple

class LocalWhisperTranscriber:
    """
    Handles transcription using local Whisper models without requiring an external server.
    This allows for more stable transcription by avoiding the connection reset issues.
    """
    
    def __init__(self, model_size="medium", device="cpu", compute_type=None, logger=None):
        """
        Initialize the local Whisper transcriber.
        
        Args:
            model_size (str): Whisper model size to use: tiny, base, small, medium, large
            device (str): Device to use for inference: cpu, cuda, mps
            compute_type (str): Compute type to use: float32, float16, int8
            logger: Optional logger instance
        """
        self.model_size = model_size
        self.device = device
        # Auto-select compute type based on device if not specified
        self.compute_type = compute_type
        self.logger = logger or logging.getLogger(__name__)
        self._whisper = None
        self._model = None
        self._server_checked = False
        self._server_available = False
        
    def log(self, level, message):
        """Helper function to log messages with the appropriate level."""
        if self.logger:
            if level == 'debug':
                self.logger.debug(message)
            elif level == 'info':
                self.logger.info(message)
            elif level == 'warning':
                self.logger.warning(message)
            elif level == 'error':
                self.logger.error(message)
            else:
                self.logger.info(message)
    
    def _check_server_availability(self):
        """Check if the Whisper server is available before downloading the model."""
        if self._server_checked:
            return self._server_available
            
        try:
            # Try to import the VideoTranscriber to check server availability
            from py.video_transcriber import VideoTranscriber
            
            # Create a transcriber instance and check server
            self.log('info', "Checking if Whisper server is available before downloading model...")
            transcriber = VideoTranscriber(logger=self.logger)
            success, message = transcriber.ping_server()
            
            self._server_checked = True
            self._server_available = success
            
            if success:
                self.log('warning', f"Remote Whisper server is available. Using local model only as fallback.")
            else:
                self.log('warning', f"Remote Whisper server is NOT available. We will need to download and use local model.")
                
            return success
        except Exception as e:
            self.log('warning', f"Error checking server availability: {str(e)}")
            self._server_checked = True
            self._server_available = False
            return False
    
    def _ensure_dependencies_installed(self):
        """Ensure that the required dependencies are installed."""
        try:
            # Try to import faster_whisper
            import faster_whisper
            return True
        except ImportError:
            # Check if server is available before installing
            if self._check_server_availability():
                self.log('warning', "Remote server is available. No need to install local dependencies.")
                # Return True even though we don't have the dependency, as we'll rely on the server
                return False
                
            self.log('warning', "faster_whisper not installed. Attempting to install...")
            try:
                import subprocess
                
                # Make sure pip is up to date first
                self.log('info', "Updating pip...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
                
                # Make sure wheel is installed first (required for faster-whisper installation)
                self.log('info', "Installing wheel package...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "wheel"])
                
                # Install CT2 first as required by faster-whisper
                self.log('info', "Installing CTranslate2...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "ctranslate2>=3.16.0"])
                
                # Install faster-whisper with increased timeout
                self.log('info', "Installing faster-whisper package...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--timeout", "180", "faster-whisper>=0.9.0"])
                
                # Verify installation worked
                try:
                    import faster_whisper
                    self.log('info', "faster-whisper installed successfully.")
                    return True
                except ImportError as verify_err:
                    self.log('error', f"Installation appeared to succeed but import still failed: {str(verify_err)}")
                    return False
                    
            except subprocess.CalledProcessError as e:
                self.log('error', f"Failed to install faster-whisper: Command '{e.cmd}' returned error ({e.returncode}): {e.output if hasattr(e, 'output') else 'No output'}")
                return False
            except Exception as e:
                self.log('error', f"Failed to install faster-whisper: {str(e)}")
                return False
    
    def _determine_compute_type(self):
        """Determine the appropriate compute type based on the device."""
        if self.compute_type:
            return self.compute_type
            
        # If compute_type wasn't specified, select based on device
        if self.device == "cpu":
            # CPU always supports float32
            return "float32"
        elif self.device == "cuda":
            # Try to detect if GPU supports float16
            try:
                import torch
                if torch.cuda.is_available():
                    capabilities = torch.cuda.get_device_capability()
                    # NVIDIA GPUs with compute capability 7.0+ support efficient float16
                    if capabilities[0] >= 7:
                        return "float16"
            except:
                pass
            # Default to float32 which is universally supported
            return "float32"
        else:
            # For other devices (like MPS on Mac), use float32
            return "float32"
    
    def _load_model(self):
        """Load the Whisper model."""
        # Check if server is available first
        if not self._server_checked:
            self._check_server_availability()
            
        # If server is available, don't bother loading the model - we'll fallback to server
        if self._server_available:
            self.log('info', "Whisper server is available - skipping local model download")
            # We'll return None when the model is needed indicating server should be used
            return False
        
        if not self._ensure_dependencies_installed():
            self.log('warning', "Failed to install dependencies, but server is available. Will use server for transcription.")
            return False
        
        try:
            from faster_whisper import WhisperModel
            
            # Determine the best compute type for the current device
            compute_type = self._determine_compute_type()
            self.log('info', f"Loading Whisper model '{self.model_size}' on {self.device} using {compute_type}...")
            
            # Try 3 times with progressively safer options if there are failures
            attempts = 0
            max_attempts = 3
            
            while attempts < max_attempts:
                try:
                    start_time = time.time()
                    
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=compute_type,
                        download_root=os.path.join(os.path.expanduser("~"), ".cache", "whisper")
                    )
                    
                    elapsed = time.time() - start_time
                    self.log('info', f"Model loaded in {elapsed:.2f} seconds")
                    return True
                    
                except ValueError as e:
                    attempts += 1
                    error_msg = str(e).lower()
                    
                    if "compute type" in error_msg and attempts < max_attempts:
                        # If there's a compute type error, fall back to float32
                        self.log('warning', f"Failed with compute_type={compute_type}: {str(e)}")
                        compute_type = "float32"
                        self.log('info', f"Trying again with compute_type={compute_type}")
                        continue
                    elif attempts < max_attempts:
                        # Try on CPU if device was CUDA
                        if self.device == "cuda":
                            self.log('warning', f"Failed on {self.device}: {str(e)}")
                            self.device = "cpu"
                            compute_type = "float32"
                            self.log('info', f"Falling back to CPU with float32")
                            continue
                    
                    # If all retries fail or it's an unhandled error type
                    raise
                    
                except Exception as e:
                    if attempts < max_attempts:
                        attempts += 1
                        self.log('warning', f"Attempt {attempts} failed: {str(e)}, retrying...")
                        # If device is cuda, try falling back to CPU
                        if self.device == "cuda":
                            self.device = "cpu"
                            compute_type = "float32"
                            self.log('info', "Falling back to CPU for transcription")
                        continue
                    raise
                    
        except Exception as e:
            self.log('error', f"Failed to load Whisper model: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            return False
    
    def transcribe_file(self, audio_path: str, language: Optional[str] = None, 
                       task: str = "transcribe", beam_size: int = 5, 
                       word_timestamps: bool = True) -> Dict[str, Any]:
        """
        Transcribe an audio file using the local Whisper model.
        
        Args:
            audio_path (str): Path to the audio file
            language (str, optional): Language code for transcription
            task (str): Task to perform: transcribe or translate
            beam_size (int): Beam size for decoding
            word_timestamps (bool): Whether to include word-level timestamps
            
        Returns:
            dict: Transcription result with text and segments
        """
        # Check if we should try server first
        if not self._server_checked:
            self._check_server_availability()
            
        # If we know server is available and we haven't loaded the model yet, fallback to server
        # But let the caller handle this by raising an appropriate error
        if self._server_available and self._model is None:
            self.log('info', "Server is available but local model is not loaded")
            return {
                "error": "Server available but local model not loaded. Should use server transcription instead.",
                "text": "",
                "use_server": True
            }
            
        # Now try to load model if needed
        if self._model is None and not self._load_model():
            if self._server_available:
                # If server is available, tell caller to use that instead
                return {
                    "error": "Local model loading failed, but server is available. Use server transcription instead.",
                    "text": "",
                    "use_server": True
                }
            else:
                # If server is not available and model loading failed, this is a real error
                return {
                    "error": "Failed to load local Whisper model and server is not available",
                    "text": ""
                }
        
        try:
            # If we get here, we either have a loaded model or we're about to fail
            if self._model is None:
                return {
                    "error": "No transcription model available",
                    "text": ""
                }
                
            start_time = time.time()
            self.log('info', f"Transcribing audio with local model: {audio_path}")
            
            # Configure transcription options
            options = {
                "beam_size": beam_size,
                "word_timestamps": word_timestamps,
                "best_of": beam_size,
                "language": language,
                "task": task,
                "vad_filter": True,
                "vad_parameters": {"threshold": 0.5}
            }
            
            # Remove None values
            options = {k: v for k, v in options.items() if v is not None}
            
            # Perform transcription
            segments, info = self._model.transcribe(audio_path, **options)
            
            # Collect results
            all_segments = []
            full_text = ""
            
            for segment in segments:
                segment_dict = {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text
                }
                
                # Add word-level timestamps if available
                if word_timestamps and segment.words:
                    segment_dict["words"] = [
                        {"word": word.word, "start": word.start, "end": word.end, "probability": word.probability}
                        for word in segment.words
                    ]
                
                all_segments.append(segment_dict)
                full_text += segment.text + " "
            
            elapsed = time.time() - start_time
            self.log('info', f"Transcription completed in {elapsed:.2f} seconds")
            
            result = {
                "text": full_text.strip(),
                "segments": all_segments,
                "language": info.language,
                "duration": elapsed
            }
            
            return result
        except Exception as e:
            self.log('error', f"Transcription error: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            return {"error": str(e), "text": ""}
    
    def transcribe_audio_segments(self, audio_segments: List[str], language: Optional[str] = None) -> List[Dict]:
        """
        Transcribe multiple audio segments and return results for each.
        
        Args:
            audio_segments (List[str]): List of paths to audio files
            language (str, optional): Language code for transcription
        
        Returns:
            List[Dict]: List of transcription results for each segment
        """
        results = []
        
        for i, segment_path in enumerate(audio_segments):
            self.log('info', f"Processing segment {i+1}/{len(audio_segments)}: {os.path.basename(segment_path)}")
            try:
                result = self.transcribe_file(segment_path, language)
                results.append({
                    "chunk": i+1,
                    "path": segment_path,
                    "result": result
                })
            except Exception as e:
                self.log('error', f"Error transcribing segment {i+1}: {str(e)}")
                results.append({
                    "chunk": i+1,
                    "path": segment_path,
                    "result": {"text": f"[Error: {str(e)}]", "error": str(e)}
                })
        
        return results

# Simple test if run directly
if __name__ == "__main__":
    import argparse
    
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Transcribe audio with local Whisper model")
    parser.add_argument('audio_path', help='Path to audio file')
    parser.add_argument('--model', default='medium', help='Whisper model size (tiny, base, small, medium, large)')
    parser.add_argument('--device', default='cpu', help='Device to use for inference (cpu, cuda)')
    parser.add_argument('--language', help='Language code')
    parser.add_argument('--compute_type', help='Compute type (float32, float16, int8) - if not specified, will automatically select')
    
    args = parser.parse_args()
    
    # Create transcriber
    transcriber = LocalWhisperTranscriber(
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type
    )
    
    # Transcribe audio
    result = transcriber.transcribe_file(args.audio_path, args.language)
    
    # Print results
    print(f"\nTranscription Result:")
    print(f"-------------------")
    print(result['text'])
    print(f"\nDetected language: {result.get('language', 'unknown')}")
    print(f"Duration: {result.get('duration', 0):.2f} seconds")
    print(f"Segments: {len(result.get('segments', []))}")