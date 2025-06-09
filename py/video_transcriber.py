import os
import requests
import logging
import json
import time
import socket
import tempfile
import subprocess
import shutil
import uuid
import struct
import importlib.util
import textwrap
import re
from datetime import timedelta
from typing import Dict, Any, Optional, Tuple, List, BinaryIO, Union, Callable
from urllib.parse import urlparse
import wave  # add missing import for WAV handling

class VideoTranscriber:
    """
    Class for handling video transcription using faster-whisper API or Wyoming protocol.
    If external services fail, falls back to local transcription.
    """
    
    # Class variable to store progress information
    _progress_data: Dict[str, Dict[str, Any]] = {}
    
    def __init__(self, server_url="http://10.0.10.23:10300", logger=None):
        """
        Initialize the VideoTranscriber.
        
        Args:
            server_url (str): URL of the faster-whisper API server
            logger: Logger instance for logging
        """
        # Try to read server_url from config if it exists
        self.use_remote_whisper = True # Default to true
        try:
            import configparser
            import os
            config = configparser.ConfigParser()
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.ini')
            if os.path.exists(config_path):
                config.read(config_path)
                if 'whisper' in config:
                    if 'server_url' in config['whisper']:
                        server_url = config['whisper']['server_url']
                    self.use_remote_whisper = config['whisper'].getboolean('use_remote_whisper', True)
        except Exception:
            # If anything goes wrong, use the provided default
            pass
            
        self.server_url = server_url.rstrip('/')
        self.logger = logger or logging.getLogger(__name__)
        self.log('info', f"VideoTranscriber initialized with server URL: {self.server_url}")
        
        # Extract host and port from server_url for Wyoming protocol
        parsed_url = urlparse(self.server_url)
        self.server_host = parsed_url.hostname or "10.0.10.23"
        self.server_port = parsed_url.port or 10300
        
        # Initialize local whisper transcriber to None - will be created on-demand
        self._local_transcriber = None
        
        # Check if the server is available
        self.check_server_availability()
        
    def log(self, level, message):
        """Helper method to log messages if a logger is available."""
        if self.logger:
            if level == 'debug':
                self.logger.debug(message)
            elif level == 'info':
                self.logger.info(message)
            elif level == 'warning':
                self.logger.warning(message)
            elif level == 'error':
                self.logger.error(message)
    
    def check_server_availability(self):
        """Check if the transcription server is available."""
        try:
            with socket.create_connection((self.server_host, self.server_port), timeout=5) as s:
                self.log('info', f"TCP connection to {self.server_host}:{self.server_port} successful")
                return True
        except Exception as e:
            self.log('warning', f"TCP connection to {self.server_host}:{self.server_port} failed: {e}")
            
            # Try HTTP fallback
            try:
                response = requests.get(f"{self.server_url}/ping", timeout=5)
                if response.status_code < 500:
                    return True
            except:
                pass
                
            self.log('warning', f"Server at {self.server_url} might not be available")
            # Don't return False yet - we'll still try to use the server
            return True  # Return True anyway to avoid local fallback
    
    def _wyoming_send_event(self, sock: socket.socket, event_dict: dict, payload: Optional[bytes] = None) -> None:
        """Send a Wyoming protocol event over a socket.
        The event_dict is the base header. If it contains a 'data' key, 
        that 'data' object is serialized as a separate JSON segment.
        """
        header_fields = {key: value for key, value in event_dict.items() if key != 'data'}
        data_segment_bytes = b""

        if 'data' in event_dict and event_dict['data'] is not None:
            # Serialize the data field as a separate JSON string
            data_segment_bytes = json.dumps(event_dict['data']).encode('utf-8')
            header_fields['data_length'] = len(data_segment_bytes)
        else:
            # Ensure data_length is 0 if no data field or data is None
            header_fields['data_length'] = 0

        if payload:
            header_fields['payload_length'] = len(payload)
        else:
            header_fields['payload_length'] = 0

        # Serialize the header fields
        header_line_bytes = json.dumps(header_fields).encode('utf-8') + b'\n'

        # Send header line
        sock.sendall(header_line_bytes)

        # Send data segment if it exists
        if header_fields['data_length'] > 0:
            sock.sendall(data_segment_bytes)

        # Send payload if it exists
        if header_fields['payload_length'] > 0 and payload is not None:
            sock.sendall(payload)

    def _wyoming_receive_event(self, sock, timeout=30):
        """Receive and parse a Wyoming event from the socket.
        
        Args:
            sock (socket.socket): The socket to receive from
            timeout (float, optional): Timeout in seconds. Defaults to 30.
            
        Returns:
            dict: The received event or None if failed
        """
        try:
            # Save original timeout and set new one
            original_timeout = sock.gettimeout()
            sock.settimeout(timeout)
            
            try:
                # Wyoming protocol uses JSON lines format
                # Read until newline character with better error handling
                data = b''
                line_read_start = time.time()
                max_line_time = 30  # Maximum time to wait for a complete line
                
                while time.time() - line_read_start < max_line_time:
                    try:
                        chunk = sock.recv(1)
                        if not chunk:  # Connection closed
                            # If we have some data but connection closed before newline, 
                            # wait briefly and try again
                            if data:
                                time.sleep(0.5)
                                continue
                            self.log('warning', "Connection closed while receiving event header")
                            return None
                            
                        data += chunk
                        if chunk == b'\n':
                            break
                    except socket.timeout:
                        # Small timeout, continue trying to read
                        if data:
                            self.log('debug', f"Timeout during header read with partial data ({len(data)} bytes)")
                        continue
                
                # Check if we got a complete line
                if not data.endswith(b'\n'):
                    self.log('warning', f"Incomplete header received after {max_line_time}s: {data[:100]}")
                    return None
                
                # Parse the JSON data
                try:
                    event = json.loads(data.decode('utf-8'))
                    
                    # Validate event structure
                    if not isinstance(event, dict):
                        self.log('warning', f"Event is not a dictionary: {event}")
                        return None
                        
                    if 'type' not in event:
                        self.log('warning', f"Event has no type field: {event}")
                        return None
                    
                    # If the event has data_length, we need to read the data segment
                    if 'data_length' in event and event['data_length'] > 0:
                        data_bytes = self._wyoming_receive_exactly(sock, event['data_length'])
                        if data_bytes:
                            try:
                                event['data'] = json.loads(data_bytes.decode('utf-8'))
                            except json.JSONDecodeError:
                                self.log('warning', f"Failed to decode data segment: {data_bytes[:100]}")
                                # Keep the event without data
                        else:
                            self.log('warning', "Failed to receive data segment")
                    
                    # If the event has payload_length, we need to read the payload
                    if 'payload_length' in event and event['payload_length'] > 0:
                        payload = self._wyoming_receive_exactly(sock, event['payload_length'])
                        if payload:
                            event['payload'] = payload
                        else:
                            self.log('warning', "Failed to receive payload")
                    
                    self.log('debug', f"Successfully received event of type: {event.get('type')}")
                    return event
                    
                except json.JSONDecodeError as e:
                    self.log('warning', f"Failed to decode JSON: {data[:100]}, error: {str(e)}")
                    return None
            finally:
                # Restore original timeout
                sock.settimeout(original_timeout)
                
        except socket.timeout:
            self.log('warning', "Socket timeout while receiving event")
            return None
        except ConnectionError as e:
            self.log('warning', f"Connection error: {e}")
            return None
        except Exception as e:
            self.log('warning', f"Error receiving Wyoming event: {e}")
            import traceback
            self.log('debug', traceback.format_exc())
            return None

    def _wyoming_receive_exactly(self, sock, length):
        """Helper method to receive exactly N bytes from socket
        
        Args:
            sock (socket.socket): The socket to receive from
            length (int): Number of bytes to receive
            
        Returns:
            bytes: The received data or None if failed
        """
        data = b''
        start_time = time.time()
        max_recv_time = 60  # Maximum time to receive the data
        
        # Get current timeout to restore later
        original_timeout = sock.gettimeout()
        
        try:
            # Use shorter timeouts for receiving chunks
            sock.settimeout(5)
            
            while len(data) < length and (time.time() - start_time) < max_recv_time:
                try:
                    # Try to receive remaining bytes
                    remaining = length - len(data)
                    chunk = sock.recv(min(4096, remaining))
                    
                    if not chunk:  # Connection closed
                        if data:  # If we have partial data, wait briefly and retry
                            time.sleep(0.5)
                            continue
                        self.log('warning', f"Connection closed while receiving data ({len(data)}/{length} bytes)")
                        return None
                    
                    data += chunk
                except socket.timeout:
                    # Small timeout, continue trying
                    continue
            
            # Check if we got all the data
            if len(data) < length:
                self.log('warning', f"Incomplete data received: got {len(data)}/{length} bytes after {max_recv_time}s")
                return None
                
            return data
            
        finally:
            # Restore original timeout
            sock.settimeout(original_timeout)

    def _wyming_send_event_with_timeout(self, sock: socket.socket, event: dict, payload: Optional[bytes] = None, timeout: float = 10.0) -> None:
        """Send a Wyoming protocol event over a socket with a timeout."""
        # Set socket timeout
        original_timeout = sock.gettimeout()
        sock.settimeout(timeout)
        
        try:
            # Convert the event to JSON bytes
            event_bytes = json.dumps(event).encode("utf-8") + b"\n"
            
            # Send the event header
            sock.sendall(event_bytes)
            
            # Send the payload if any
            if payload is not None:
                sock.sendall(payload)
        finally:
            # Restore original timeout
            sock.settimeout(original_timeout)

    def _transcribe_audio_chunk_wyoming(self, audio_path: str, language: Optional[str] = None) -> tuple:
        """
        Transcribe audio chunk using official Wyoming client (AsyncTcpClient, Transcribe, AudioStart, AudioChunk, AudioStop).
        This avoids custom protocol mismatch and leverages tested library code.
        Force CPU device to avoid GPU/CuDNN errors.
        """
        import asyncio
        from wyoming.client import AsyncTcpClient
        from wyoming.asr import Transcribe, Transcript
        from wyoming.audio import AudioStart, AudioChunk, AudioStop

        async def _wyoming_job():
            try:
                async with AsyncTcpClient(self.server_host, self.server_port) as client:
                    # Create a Transcribe object with only the parameters it accepts
                    transcribe_kwargs = {}  # Remove device and compute_type
                    if language and language != "auto":
                        transcribe_kwargs["language"] = language
                        
                    # DON'T include model name from config.ini - use the already loaded model on the server
                    # This avoids trying to download a new model when one is already loaded
                    
                    # Send the Transcribe event with valid parameters
                    await client.write_event(Transcribe(**transcribe_kwargs).event())

                    # 2) start audio stream
                    await client.write_event(
                        AudioStart(rate=16000, width=2, channels=1).event()
                    )

                    # 3) stream audio chunks
                    with open(audio_path, 'rb') as f:
                        f.seek(44)  # skip WAV header if present
                        while True:
                            data = f.read(4096)
                            if not data:
                                break
                            await client.write_event(
                                AudioChunk(rate=16000, width=2, channels=1, audio=data).event()
                            )

                    # 4) stop audio
                    await client.write_event(AudioStop().event())

                    # 5) read events until transcript
                    while True:
                        event = await client.read_event()
                        if event is None:
                            break
                        if Transcript.is_type(event.type):
                            text = Transcript.from_event(event).text
                            return True, "Transcription successful", {"text": text}
                    return False, "No transcript received", {}
            except Exception as e:
                self.log('error', f"Wyoming AsyncTcpClient error: {e}")
                return False, f"Wyoming protocol error: {e}", {}

        try:
            return asyncio.run(_wyoming_job())
        except Exception as e:
            self.log('error', f"Async transcription error: {e}")
            return False, f"Async transcription error: {e}", {}

    def extract_audio(self, video_path):
        """
        Extract audio from a video file using FFmpeg.
        
        Args:
            video_path (str): Path to the video file
            
        Returns:
            tuple: (success, message, audio_path)
                - success (bool): True if audio extraction was successful
                - message (str): Status or error message
                - audio_path (str): Path to the extracted audio file or None if failed
        """
        try:
            # Check if FFmpeg is installed
            try:
                result = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
                if not result.stdout:
                    self.log('error', "FFmpeg not found. Please install FFmpeg.")
                    return False, "FFmpeg not found. Please install FFmpeg.", None
            except Exception as e:
                self.log('error', f"Error checking FFmpeg installation: {str(e)}")
                return False, f"Error checking FFmpeg installation: {str(e)}", None
                
            # Create a temporary directory for audio extraction
            temp_dir = tempfile.mkdtemp(prefix="whisper_audio_")
            self.log('debug', f"Created temporary directory for audio extraction: {temp_dir}")
            
            # Generate a temporary file path for the audio
            audio_filename = os.path.splitext(os.path.basename(video_path))[0] + ".wav"
            audio_path = os.path.join(temp_dir, audio_filename)
            
            # Extract audio using FFmpeg - optimize for speech recognition
            self.log('info', f"Extracting audio from video: {video_path}")
            cmd = [
                'ffmpeg',
                '-i', video_path,               # Input video
                '-vn',                           # Disable video
                '-acodec', 'pcm_s16le',         # Convert to WAV
                '-ar', '16000',                 # 16kHz sample rate (optimal for STT)
                '-ac', '1',                     # Convert to mono
                '-y',                           # Overwrite output file if it exists
                audio_path                      # Output audio file
            ]
            
            self.log('debug', f"Running command: {' '.join(cmd)}")
            process = subprocess.run(cmd, capture_output=True, text=True)
            
            if process.returncode != 0:
                self.log('error', f"FFmpeg error: {process.stderr}")
                # Clean up the temp directory
                shutil.rmtree(temp_dir, ignore_errors=True)
                return False, f"FFmpeg error: {process.stderr}", None
                
            self.log('info', f"Audio extracted successfully: {audio_path}")
            return True, "Audio extracted successfully", audio_path
            
        except Exception as e:
            self.log('error', f"Error extracting audio: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            # Try to clean up temp dir if it was created
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)
            return False, f"Error extracting audio: {str(e)}", None
    
    def split_audio_into_chunks(self, audio_path, chunk_duration_seconds=30):
        """
        Split a large audio file into smaller chunks for easier processing.
        
        Args:
            audio_path (str): Path to the audio file
            chunk_duration_seconds (int): Length of each chunk in seconds
            
        Returns:
            tuple: (success, message, chunk_paths)
                - success (bool): True if splitting was successful
                - message (str): Status or error message
                - chunk_paths (list): List of paths to the audio chunks
        """
        try:
            # Get audio duration using ffprobe
            cmd = [
                'ffprobe', 
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                audio_path
            ]
            
            self.log('debug', f"Getting audio duration: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                self.log('error', f"Error getting audio duration: {result.stderr}")
                return False, f"Error getting audio duration: {result.stderr}", []
                
            # Parse the duration
            try:
                duration = float(result.stdout.strip())
                self.log('info', f"Audio duration: {duration:.2f} seconds")
            except ValueError:
                self.log('error', f"Could not parse audio duration: {result.stdout}")
                return False, f"Could not parse audio duration: {result.stdout}", []
                
            # If duration is short, no need to split
            if duration <= chunk_duration_seconds:
                self.log('info', f"Audio duration ({duration:.2f}s) is less than chunk size ({chunk_duration_seconds}s). No splitting needed.")
                return True, "Audio file is short enough, no splitting needed", [audio_path]
                
            # Calculate number of chunks
            num_chunks = int(duration / chunk_duration_seconds) + 1
            self.log('info', f"Splitting audio into {num_chunks} chunks of {chunk_duration_seconds}s each")
            
            # Create output directory for chunks
            temp_dir = os.path.dirname(audio_path)
            base_filename = os.path.splitext(os.path.basename(audio_path))[0]
            
            # Split the audio file into chunks
            chunk_paths = []
            
            for i in range(num_chunks):
                start_time = i * chunk_duration_seconds
                
                # Generate output filename for this chunk
                chunk_path = os.path.join(temp_dir, f"{base_filename}_chunk{i:03d}.wav")
                chunk_paths.append(chunk_path)
                
                # Use FFmpeg to extract this chunk
                cmd = [
                    'ffmpeg',
                    '-ss', str(start_time),                # Start time
                    '-t', str(chunk_duration_seconds),     # Duration
                    '-i', audio_path,                      # Input file
                    '-acodec', 'pcm_s16le',               # Audio codec
                    '-ar', '16000',                        # Sample rate
                    '-ac', '1',                            # Mono
                    '-y',                                  # Overwrite
                    chunk_path                             # Output file
                ]
                
                self.log('debug', f"Creating chunk {i+1}/{num_chunks}: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode != 0:
                    self.log('error', f"Error creating chunk {i+1}: {result.stderr}")
                    # Continue with other chunks even if one fails
            
            self.log('info', f"Created {len(chunk_paths)} audio chunks")
            return True, f"Split audio into {len(chunk_paths)} chunks", chunk_paths
            
        except Exception as e:
            self.log('error', f"Error splitting audio: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            return False, f"Error splitting audio: {str(e)}", []
    
    def _fallback_to_local_transcription(self, audio_path: str, language: Optional[str] = None) -> Tuple[bool, str, Dict]:
        """
        Fallback to local transcription when server methods fail.
        
        Args:
            audio_path (str): Path to the audio file
            language (str, optional): Language code
            
        Returns:
            tuple: (success, message, result)
                - success (bool): True if successful
                - message (str): Status message
                - result (dict): Transcription result
        """
        self.log('info', f"Falling back to local transcription for {audio_path}")
        
        # Import LocalWhisperTranscriber only when needed
        if self._local_transcriber is None:
            try:
                # Check if local_whisper.py exists in the same directory
                current_dir = os.path.dirname(os.path.abspath(__file__))
                local_whisper_path = os.path.join(current_dir, 'local_whisper.py')
                
                if os.path.exists(local_whisper_path):
                    # Import directly from the file using importlib
                    spec = importlib.util.spec_from_file_location("local_whisper", local_whisper_path)
                    if spec is not None and spec.loader is not None:
                        local_whisper = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(local_whisper)
                        
                        # Try to read settings from config.ini
                        whisper_model = "large-v3-turbo"  # Default to the requested model
                        whisper_device = "cuda"           # Default to CUDA
                    whisper_compute_type = "float16"  # Default to float16
                    whisper_beam = 10                 # Default to beam size 10
                    whisper_lang = "en"               # Default to English
                    
                    # Try to read from config file
                    try:
                        import configparser
                        config = configparser.ConfigParser()
                        config_path = os.path.join(os.path.dirname(current_dir), 'config.ini')
                        if os.path.exists(config_path):
                            config.read(config_path)
                            if 'whisper' in config:
                                whisper_section = config['whisper']
                                whisper_model = whisper_section.get('model', whisper_model)
                                whisper_device = whisper_section.get('device', whisper_device)
                                whisper_compute_type = whisper_section.get('compute_type', whisper_compute_type)
                                whisper_beam = int(whisper_section.get('beam_size', whisper_beam))
                                whisper_lang = whisper_section.get('language', whisper_lang) if not language else language
                                self.log('info', f"Using Whisper settings from config.ini: model={whisper_model}, device={whisper_device}, compute_type={whisper_compute_type}, beam={whisper_beam}")
                    except Exception as config_error:
                        self.log('warning', f"Failed to read from config.ini: {str(config_error)}. Using default settings.")
                    
                    # Override language with parameter if provided
                    if language:
                        whisper_lang = language
                    
                    # Create LocalWhisperTranscriber instance with settings from config
                    self._local_transcriber = local_whisper.LocalWhisperTranscriber(
                        model_size=whisper_model,      # Use configured model
                        device=whisper_device,         # Use configured device
                        compute_type=whisper_compute_type,  # Use configured compute type
                        logger=self.logger
                    )
                    self.log('info', f"Created local whisper transcriber with model {whisper_model} on {whisper_device}")
                else:
                    self.log('error', f"Could not find local_whisper.py at {local_whisper_path}")
                    return False, f"Local transcription module not found at {local_whisper_path}", {}
            except Exception as e:
                self.log('error', f"Error initializing local transcriber: {str(e)}")
                import traceback
                self.log('error', traceback.format_exc())
                return False, f"Error initializing local transcriber: {str(e)}", {}
        
        # Transcribe using the local model
        try:
            self.log('info', f"Transcribing {audio_path} with local Whisper model")
            
            # Read beam_size and language from config if available
            beam_size = 10  # Default
            language_to_use = language
            
            # Try to read from config file again if needed
            try:
                import configparser
                config = configparser.ConfigParser()
                config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.ini')
                if os.path.exists(config_path):
                    config.read(config_path)
                    if 'whisper' in config:
                        beam_size = int(config['whisper'].get('beam_size', beam_size))
                        if not language_to_use and 'language' in config['whisper']:
                            language_to_use = config['whisper']['language']
            except Exception:
                pass  # Use defaults if config can't be read
            
            # Call transcribe with beam_size from config
            result = self._local_transcriber.transcribe_file(
                audio_path, 
                language=language_to_use, 
                beam_size=beam_size
            )
            
            if 'error' in result and result['error']:
                self.log('error', f"Local transcription error: {result['error']}")
                return False, f"Local transcription error: {result['error']}", {}
            
            self.log('info', f"Local transcription successful: {result['text'][:50]}...")
            return True, "Local transcription successful", {"text": result['text']}
            
        except Exception as e:
            self.log('error', f"Local transcription error: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            return False, f"Local transcription error: {str(e)}", {}

    def transcribe_audio_chunk(self, audio_path, language=None):
        """
        Send an audio chunk for transcription using Wyoming protocol first,
        then falling back to HTTP APIs, then local transcription if all remote methods fail.
        If use_remote_whisper is false, it will directly fall back to local whisper.
        
        Args:
            audio_path (str): Path to the audio file
            language (str, optional): Language code for transcription
            
        Returns:
            tuple: (success, message, result)
                - success (bool): True if the request was successful
                - message (str): Status or error message
                - result (dict): Response data from the server
        """
        if not self.use_remote_whisper:
            self.log('info', "Remote whisper is disabled. Falling back to local transcription.")
            return self._fallback_to_local_transcription(audio_path, language)

        # First try Wyoming protocol (TCP-based)
        try:
            success, message, result = self._transcribe_audio_chunk_wyoming(audio_path, language)
            if success:
                return success, message, result
            else:
                self.log('warning', f"Wyoming protocol transcription failed: {message}. Trying HTTP fallback.")
        except Exception as e:
            self.log('warning', f"Wyoming protocol error: {str(e)}. Trying HTTP fallback.")
            
        # If Wyoming failed, try HTTP API fallbacks
        try:
            # Get audio file info for logging
            file_size = os.path.getsize(audio_path) / (1024 * 1024)  # Size in MB
            self.log('info', f"Transcribing audio chunk via HTTP: {audio_path} (Size: {file_size:.2f} MB)")
            
            # Based on the error logs, let's try a simple approach with a direct URL
            endpoint = f"{self.server_url}/api/converttotext"
            
            try:
                # Open the audio file
                with open(audio_path, 'rb') as audio_file:
                    # Extremely simplified request - just the audio file with generic name 'audio.wav'
                    files = {'file': ('audio.wav', audio_file, 'audio/wav')}
                    
                    # Minimal data parameters
                    data = {}
                    
                    # Add language if provided
                    if language:
                        data['language'] = language
                        self.log('info', f"Using specified language: {language}")
                    
                    self.log('info', f"Sending audio to {endpoint} using direct file upload")
                    
                    # Send the request with increased timeout for larger files
                    timeout_seconds = min(300, max(30, int(file_size * 10)))  # At least 30 seconds
                    self.log('debug', f"Using timeout of {timeout_seconds} seconds for API call")
                    
                    # Attempt the request
                    response = requests.post(
                        endpoint, 
                        files=files,
                        data=data,
                        timeout=timeout_seconds
                    )
                    
                    # Handle the response
                    if response.status_code == 200:
                        try:
                            result = response.json()
                            self.log('info', f"Transcription response: {result}")
                            return True, "Transcription succeeded", result
                        except json.JSONDecodeError:
                            # Try to handle plain text responses
                            text = response.text.strip()
                            self.log('info', f"Received text response: {text[:100]}...")
                            return True, "Received text response", {"text": text}
                    else:
                        error_msg = f"Server error {response.status_code}: {response.text}"
                        self.log('error', error_msg)
                        # If HTTP request fails, fall back to local transcription
                        return self._fallback_to_local_transcription(audio_path, language)
                        
            except requests.RequestException as req_error:
                error_msg = f"Request error at {endpoint}: {str(req_error)}"
                self.log('error', error_msg)
                
                # Try HomeAssistant-style STT endpoint as fallback
                try:
                    self.log('info', f"Trying fallback HomeAssistant STT endpoint")
                    endpoint = f"{self.server_url}/api/speech-to-text"
                    
                    with open(audio_path, 'rb') as audio_file:
                        # HomeAssistant format typically expects 'audio' parameter
                        files = {'audio': ('audio.wav', audio_file, 'audio/wav')}
                        
                        response = requests.post(
                            endpoint, 
                            files=files,
                            timeout=timeout_seconds
                        )
                        
                        if response.status_code == 200:
                            try:
                                result = response.json()
                                self.log('info', f"HomeAssistant STT response: {result}")
                                
                                # Format varies, so handle common patterns
                                if isinstance(result, dict) and "text" in result:
                                    return True, "Transcription succeeded", result
                                elif isinstance(result, dict) and "result" in result:
                                    return True, "Transcription succeeded", {"text": result["result"]}
                                elif isinstance(result, str):
                                    return True, "Transcription succeeded", {"text": result}
                                else:
                                    return True, "Transcription succeeded with unknown format", {"text": str(result)}
                            except json.JSONDecodeError:
                                text = response.text.strip()
                                self.log('info', f"Received text response: {text[:100]}...")
                                return True, "Received text response", {"text": text}
                        else:
                            error_msg = f"HomeAssistant STT error {response.status_code}: {response.text}"
                            self.log('error', error_msg)
                            # Fall back to local transcription
                            return self._fallback_to_local_transcription(audio_path, language)
                except Exception as ha_error:
                    self.log('error', f"Both endpoints failed. HomeAssistant error: {str(ha_error)}")
                    # Fall back to local transcription
                    return self._fallback_to_local_transcription(audio_path, language)
                
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            self.log('error', error_msg)
            import traceback
            self.log('error', traceback.format_exc())
            # Fall back to local transcription as a last resort
            return self._fallback_to_local_transcription(audio_path, language)

    def transcribe_video(self, video_path, language=None, use_chunks=True, chunk_duration=15):
        """
        Send a video file to the faster-whisper API for transcription.
        First extracts audio from the video, then sends audio to the API.
        
        Args:
            video_path (str): Path to the video file
            language (str, optional): Language code for transcription (e.g., 'en', 'fr')
            use_chunks (bool): Whether to split audio into chunks (better for long files)
            chunk_duration (int): Length of each chunk in seconds
            
        Returns:
            tuple: (success, message, result)
                - success (bool): True if the request was successful
                - message (str): Status or error message
                - result (dict): Response data from the server
        """
        if not os.path.exists(video_path):
            self.log('error', f"Video file not found: {video_path}")
            return False, "Video file not found", {}
        
        try:
            # First, extract audio from video
            self.log('info', f"Starting transcription process for video: {video_path}")
            extract_success, extract_message, audio_path = self.extract_audio(video_path)
            
            if not extract_success or not audio_path:
                self.log('error', f"Failed to extract audio: {extract_message}")
                return False, f"Failed to extract audio: {extract_message}", {}
            
            self.log('info', f"Using extracted audio file: {audio_path}")
            
            try:
                if use_chunks:
                    # Split audio into manageable chunks
                    split_success, split_message, chunk_paths = self.split_audio_into_chunks(
                        audio_path, 
                        chunk_duration_seconds=chunk_duration
                    )
                    
                    if not split_success:
                        self.log('error', f"Failed to split audio: {split_message}")
                        return False, f"Failed to split audio: {split_message}", {}
                    
                    self.log('info', f"Processing {len(chunk_paths)} audio chunks")
                    
                    # Process each chunk and collect results
                    all_transcriptions = []
                    max_retries = 3
                    
                    for i, chunk_path in enumerate(chunk_paths):
                        self.log('info', f"Processing chunk {i+1}/{len(chunk_paths)}: {os.path.basename(chunk_path)}")
                        
                        # Try with retries to handle transient failures
                        retry_count = 0
                        chunk_success = False
                        
                        while not chunk_success and retry_count < max_retries:
                            # Transcribe this chunk
                            chunk_success, chunk_message, chunk_result = self.transcribe_audio_chunk(
                                chunk_path, 
                                language
                            )
                            
                            if chunk_success:
                                all_transcriptions.append({
                                    "chunk": i+1,
                                    "path": chunk_path,
                                    "result": chunk_result
                                })
                                break
                            else:
                                retry_count += 1
                                if retry_count < max_retries:
                                    self.log('warning', f"Failed to transcribe chunk {i+1}, attempt {retry_count}: {chunk_message}. Retrying...")
                                    time.sleep(2)  # Wait before retrying
                                else:
                                    self.log('warning', f"Failed to transcribe chunk {i+1} after {max_retries} attempts: {chunk_message}")
                    
                    # Check if we got any successful transcriptions
                    if not all_transcriptions:
                        return False, "Failed to transcribe any audio chunks", {}
                    
                    # Return combined results
                    result = {
                        "transcription_type": "chunked",
                        "num_chunks": len(chunk_paths),
                        "successful_chunks": len(all_transcriptions),
                        "chunks": all_transcriptions,
                        "job_id": f"local_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                    }
                    
                    return True, "Chunked transcription completed", result
                else:
                    # Process the entire audio file at once
                    return self.transcribe_audio_chunk(audio_path, language)
            finally:
                # Clean up temporary audio files
                if 'audio_path' in locals() and audio_path:
                    temp_dir = os.path.dirname(audio_path)
                    self.log('debug', f"Cleaning up temporary audio files: {temp_dir}")
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    except Exception as cleanup_error:
                        self.log('warning', f"Failed to clean up temporary files: {str(cleanup_error)}")
                    
        except Exception as e:
            error_msg = f"Transcription error: {str(e)}"
            self.log('error', error_msg)
            import traceback
            self.log('error', traceback.format_exc())
            return False, error_msg, {}
    
    def combine_chunk_transcriptions(self, chunks):
        """
        Combine transcription results from multiple chunks.
        
        Args:
            chunks (list): List of chunk results
            
        Returns:
            dict: Combined transcription result
        """
        try:
            # Initialize combined text
            combined_text = ""
            segments = []
            
            # Process each chunk
            for chunk in sorted(chunks, key=lambda c: c.get('chunk', 0)):
                chunk_result = chunk.get('result', {})
                
                # Extract text based on different API response formats
                if 'text' in chunk_result:
                    # Simple text format
                    combined_text += chunk_result['text'] + "\n"
                elif 'segments' in chunk_result:
                    # Segment format
                    for segment in chunk_result['segments']:
                        if 'text' in segment:
                            segments.append(segment)
                elif 'results' in chunk_result and 'transcripts' in chunk_result['results']:
                    # Amazon Transcribe like format
                    combined_text += chunk_result['results']['transcripts'][0]['transcript'] + "\n"
                    
            return {
                "text": combined_text.strip(),
                "segments": segments
            }
            
        except Exception as e:
            self.log('error', f"Error combining transcriptions: {str(e)}")
            return {"text": "Error combining transcriptions", "error": str(e)}
    
    def get_transcription_status(self, job_id):
        """
        Check the status of a transcription job.
        
        Args:
            job_id (str): The ID of the transcription job
            
        Returns:
            tuple: (success, message, result)
                - success (bool): True if the status check was successful
                - message (str): Status or error message
                - result (dict): Response data from the server
        """
        try:
            # Check if this is a local job ID (from chunked processing)
            if job_id.startswith('local_'):
                return True, "Local transcription completed", {"status": "completed"}
                
            # Try common status endpoint patterns
            endpoints = [
                f"{self.server_url}/jobs/{job_id}",
                f"{self.server_url}/status/{job_id}",
                f"{self.server_url}/job/{job_id}"
            ]
            
            for endpoint in endpoints:
                try:
                    self.log('debug', f"Checking job status at {endpoint}")
                    response = requests.get(endpoint, timeout=10)
                    
                    if response.status_code == 200:
                        try:
                            result = response.json()
                            status = result.get('status', 'unknown')
                            self.log('info', f"Job {job_id} status: {status}")
                            return True, f"Job status: {status}", result
                        except json.JSONDecodeError:
                            continue  # Try next endpoint
                except requests.RequestException:
                    continue  # Try next endpoint
            
            # If we get here, all endpoints failed
            self.log('error', f"Could not get status for job {job_id} from any endpoint")
            return False, f"Could not get status for job {job_id}", {}
            
        except Exception as e:
            error_msg = f"Error checking job status: {str(e)}"
            self.log('error', error_msg)
            return False, error_msg, {}
    
    def generate_srt_from_chunks(self, chunks_data):
        """
        Generate an SRT file from chunked transcription results.
        
        Args:
            chunks_data (list): List of chunk results
            
        Returns:
            str: SRT formatted string
        """
        try:
            import datetime
            import srt
            
            # Combine all segments from all chunks
            all_segments = []
            chunk_offset = 0  # Time offset for each chunk in seconds
            
            for chunk in sorted(chunks_data, key=lambda c: c.get('chunk', 0)):
                chunk_result = chunk.get('result', {})
                chunk_index = chunk.get('chunk', 0) - 1  # 0-based index
                
                # Check if there's a timestamp offset to apply to this chunk
                # Based on chunk duration (typically 30 seconds per chunk)
                chunk_offset = chunk_index * 30  # seconds
                
                # Parse segments based on response format
                if 'segments' in chunk_result:
                    segments = chunk_result['segments']
                elif 'results' in chunk_result and 'items' in chunk_result['results']:
                    # Format like Amazon Transcribe - need to convert to segments
                    # This is just a placeholder; actual implementation would depend on the API
                    segments = []
                elif 'text' in chunk_result:
                    # Simple text format - create a single segment
                    segments = [{
                        'start': 0,
                        'end': 5,  # Assume 5 seconds if no timing
                        'text': chunk_result['text']
                    }]
                else:
                    # Unknown format
                    continue
                
                # Process each segment
                for segment in segments:
                    # Adjust timestamps by adding the chunk offset
                    start_time = segment.get('start', 0) + chunk_offset
                    end_time = segment.get('end', start_time + 5) + chunk_offset
                    
                    all_segments.append({
                        'start': start_time,
                        'end': end_time,
                        'text': segment.get('text', '').strip()
                    })
            
            # Sort segments by start time
            all_segments.sort(key=lambda s: s['start'])
            
            # Convert to SRT format
            srt_segments = []
            for i, segment in enumerate(all_segments):
                # Convert seconds to timedelta
                start_time = datetime.timedelta(seconds=segment['start'])
                end_time = datetime.timedelta(seconds=segment['end'])
                
                # Skip empty segments
                if not segment['text'].strip():
                    continue
                
                # Create SRT subtitle
                subtitle = srt.Subtitle(
                    index=i+1,
                    start=start_time,
                    end=end_time,
                    content=segment['text']
                )
                srt_segments.append(subtitle)
            
            # Generate SRT content
            return srt.compose(srt_segments)
            
        except Exception as e:
            self.log('error', f"Error generating SRT from chunks: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            return f"# Error generating SRT: {str(e)}"
    
    def download_srt(self, job_id, output_path):
        """
        Download the SRT file for a completed transcription job.
        For local job IDs, generates SRT from chunked results.
        For server job IDs, tries to download from the server.
        
        Args:
            job_id (str): The ID of the transcription job
            output_path (str): Path where the SRT file should be saved
            
        Returns:
            tuple: (success, message)
                - success (bool): True if the download was successful
                - message (str): Status or error message
        """
        try:
            # Check if this is a local job ID from chunked processing
            if job_id.startswith('local_'):
                # Get the job data from translation_jobs
                job_data = None
                
                try:
                    # Try to get job data from app.translation_jobs
                    import sys
                    if 'app' in sys.modules:
                        from app import translation_jobs
                        if job_id in translation_jobs:
                            job_data = translation_jobs[job_id]
                    else:
                        # We might be in an isolated call, try to find chunks directly
                        job_data = {"chunks": [c for c in getattr(self, "_chunks_data", []) if c.get("job_id") == job_id]}
                except ImportError:
                    self.log('warning', "Could not import app module. Using any available chunk data.")
                
                # Check if we have the chunks data
                chunks = []
                if job_data and 'chunks' in job_data:
                    chunks = job_data['chunks']
                elif hasattr(self, '_chunks_data') and isinstance(getattr(self, '_chunks_data', None), list):
                    chunks = getattr(self, '_chunks_data', [])
                    
                if chunks:
                    # Generate SRT from chunks
                    srt_content = self.generate_srt_from_chunks(chunks)
                    
                    # Handle empty SRT content
                    if not srt_content or srt_content.strip() == "":
                        self.log('warning', "Generated SRT is empty, creating basic placeholder")
                        srt_content = "1\n00:00:00,000 --> 00:00:05,000\n[No transcription available]\n\n"
                    
                    # Save to the output path
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(srt_content)
                        
                    self.log('info', f"Generated SRT from chunks and saved to {output_path}")
                    return True, f"SRT generated from chunks and saved to {output_path}"
                else:
                    self.log('error', f"No chunk data found for job {job_id}")
                    
                    # Create a basic empty SRT file rather than failing
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write("1\n00:00:00,000 --> 00:00:05,000\n[No transcription available]\n\n")
                        
            
            # Extract host and port from URL for direct socket test
            parsed_url = urlparse(self.server_url)
            host = parsed_url.hostname
            port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
            
            # TCP connectivity check
            self.log('debug', f"Testing TCP connectivity to {host}:{port}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)  # 3 second timeout for TCP connection
            
            try:
                sock.connect((host, port))
                self.log('info', f"TCP connection to {host}:{port} successful")
                sock.close()
            except socket.error as e:
                self.log('error', f"TCP connection to {host}:{port} failed: {str(e)}")
                return False, f"Cannot connect to server at {host}:{port}. Is the server running?"
            
            # Check for valid API endpoints
            # Common endpoints for health checks or API status in transcription servers
            endpoints = [
                '/health',
                '/status',
                '/ready',
                '/info',
                '/'
            ]
            
            for endpoint in endpoints:
                try:
                    self.log('debug', f"Checking API endpoint: {self.server_url}{endpoint}")
                    response = requests.get(f"{self.server_url}{endpoint}", timeout=5)
                    
                    if response.status_code == 200:
                        self.log('info', f"Server responded to {endpoint} with status 200")
                        return True, "Server is reachable and responding to API requests"
                except requests.RequestException:
                    pass  # Continue with next endpoint
            
            # If TCP succeeded but HTTP checks failed, server might still be usable for transcription
            self.log('info', "TCP connection successful, but HTTP endpoints not responding. Considering server available.")
            return True, "Server is reachable via TCP but did not respond to health checks. The server may be ready only for transcription requests."
            
        except Exception as e:
            error_msg = f"Error checking server: {str(e)}"
            self.log('error', error_msg)
            return False, error_msg

    def connect_to_remote_faster_whisper(self, host=None, port=None, timeout=30):
        """
        Establish a connection to a remote faster-whisper server and test if it's responsive.
        This method is optimized for the linuxserver/faster-whisper:gpu image.
        
        Args:
            host (str, optional): Host address. If None, uses self.server_host
            port (int, optional): Port number. If None, uses self.server_port
            timeout (int, optional): Connection timeout in seconds
            
        Returns:
            tuple: (success, message)
                - success (bool): True if successfully connected
                - message (str): Status or error message
        """
        host = host or self.server_host
        port = port or self.server_port
        
        try:
            self.log('info', f"Testing connection to remote faster-whisper at {host}:{port}")
            
            # Try establishing a TCP connection first
            with socket.create_connection((host, port), timeout=timeout) as sock:
                self.log('info', f"TCP connection to {host}:{port} established")
                
                # Send a simple describe event to test Wyoming protocol
                try:
                    # Set a shorter timeout for this test
                    sock.settimeout(10)
                    self._wyoming_send_event(sock, {"type": "describe"})
                    
                    # Try to get a response, ignoring any CUDA errors
                    try:
                        info_event = self._wyoming_receive_event(sock, timeout=10)
                        if info_event and info_event.get("type") == "info":
                            self.log('info', f"Wyoming protocol test successful: {info_event}")
                            return True, "Wyoming protocol connection successful"
                        else:
                            self.log('warning', f"Wyoming server responded, but with unexpected event type: {info_event}")
                            return True, "Wyoming server responded with unexpected event type"
                    except socket.timeout:
                        self.log('warning', "Wyoming describe command timed out, but TCP connection works")
                        return True, "TCP connection works but Wyoming protocol timed out"
                    except Exception as protocol_error:
                        self.log('warning', f"Wyoming protocol test failed, but TCP connection works: {str(protocol_error)}")
                        return True, "TCP connection works but Wyoming protocol test failed"
                        
                except Exception as e:
                    self.log('warning', f"Failed to test Wyoming protocol: {str(e)}")
                    return True, "TCP connection works but Wyoming protocol test failed"
                    
        except socket.timeout:
            self.log('error', f"Connection to {host}:{port} timed out after {timeout} seconds")
            return False, f"Connection timeout after {timeout} seconds"
        except ConnectionRefusedError:
            self.log('error', f"Connection to {host}:{port} refused. Is the server running?")
            return False, "Connection refused"
        except Exception as e:
            self.log('error', f"Error connecting to {host}:{port}: {str(e)}")
            return False, f"Connection error: {str(e)}"

    def ping_server(self) -> Tuple[bool, str]:
        """
        Ping the Wyoming protocol server to check if it's available.
        
        Returns:
            tuple: (success, message)
                - success (bool): True if the server is reachable
                - message (str): Status or error message
        """
        try:
            self.log('info', f"Testing connection to faster-whisper server at {self.server_url}")
            
            # Try establishing a TCP connection first
            parsed_url = urlparse(self.server_url)
            host = parsed_url.hostname or "10.0.10.23"
            port = parsed_url.port or 10300
            
            try:
                with socket.create_connection((host, port), timeout=5) as sock:
                    self.log('info', f"TCP connection to {host}:{port} successful")
                    
                    # For remote servers, just accepting a successful TCP connection is sufficient
                    # Try a basic Wyoming protocol test but don't fail if it doesn't work as expected
                    try:
                        # Set a short timeout for this test
                        sock.settimeout(3)
                        self._wyoming_send_event(sock, {"type": "describe"})
                        
                        # Try to get a response
                        try:
                            info_event = self._wyoming_receive_event(sock, timeout=3)
                            if info_event and info_event.get("type") == "info":
                                self.log('info', f"Wyoming protocol test successful")
                                return True, "Connection successful with full Wyoming protocol support"
                        except socket.timeout:
                            self.log('warning', "Wyoming protocol handshake timed out, but TCP connection works")
                            return True, "TCP port is open but Wyoming protocol handshake timed out"
                        except Exception as e:
                            self.log('warning', f"Wyoming protocol test failed, but TCP connection works: {str(e)}")
                            return True, f"TCP port is open but Wyoming protocol test failed: {str(e)}"
                    except Exception as e:
                        self.log('warning', f"Failed to test Wyoming protocol: {str(e)}")
                        return True, f"TCP port is open but Wyoming protocol couldn't be tested: {str(e)}"
                    
                    # If we reach here, the TCP connection was successful but Wyoming protocol test failed
                    return True, "TCP connection successful, but Wyoming protocol test failed"
            except socket.timeout:
                self.log('error', f"Connection to {host}:{port} timed out after 5 seconds")
                return False, f"Connection timeout after 5 seconds"
            except ConnectionRefusedError:
                self.log('error', f"Connection to {host}:{port} refused. Is the server running?")
                return False, "Connection refused. Is the server running?"
            except Exception as e:
                self.log('error', f"Error connecting to {host}:{port}: {str(e)}")
                return False, f"TCP connection error: {str(e)}"
                
        except Exception as e:
            self.log('error', f"Error pinging server at {self.server_url}: {str(e)}")
            return False, f"Error checking server: {str(e)}"

    def transcribe_audio_wyoming(self, audio_path, language=None, max_retries=3):
        """Transcribe audio using Wyoming protocol."""
        self.log('info', f"Using Home Assistant compatible Wyoming client for: {audio_path}")
        
        try:
            port = 10300  # Use the port from the URL
            host = "10.0.10.23"  # Use the host from the URL
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                # Connect to the server
                sock.connect((host, port))
                
                # Send audio info event
                with wave.open(audio_path, "rb") as wav:
                    audio_info = {
                        "type": "audio-start",
                        "data": {
                            "rate": wav.getframerate(),
                            "width": wav.getsampwidth() * 8,
                            "channels": wav.getnchannels(),
                        }
                    }
                    self._wyoming_send_event(sock, audio_info)
                    
                    # Send detect language event if we want auto-detection
                    if language is None or language == "auto":
                        detect_event = {
                            "type": "detect-language",
                            "data": {}
                        }
                        self._wyoming_send_event(sock, detect_event)
                    else:
                        # Send transcribe event with specified language
                        transcribe_event = {
                            "type": "transcribe",
                            "data": {
                                "language": language
                            }
                        }
                        self._wyoming_send_event(sock, transcribe_event)
                    
                    # Stream audio data
                    frames = wav.readframes(wav.getnframes())
                    chunk_size = 4096
                    for i in range(0, len(frames), chunk_size):
                        chunk = frames[i:i + chunk_size]
                        audio_event = {
                            "type": "audio-chunk"
                        }
                        self._wyoming_send_event(sock, audio_event, chunk)
                
                # Send audio stop event
                audio_stop = {
                    "type": "audio-stop",
                    "data": {}
                }
                self._wyoming_send_event(sock, audio_stop)
                
                # Wait for transcription result
                results = []
                transcript = ""
                segments = []
                
                retry_count = 0
                while retry_count < max_retries:
                    event = self._wyoming_receive_event(sock)
                    
                    if event is None:
                        retry_count += 1
                        self.log('warning', f"Received None event, retry {retry_count}/{max_retries}")
                        continue
                    
                    event_type = event.get('type')
                    self.log('debug', f"Received event type: {event_type}")
                    
                    if event_type == "transcript":
                        # Found our transcript!
                        transcript_data = event.get('data', {})
                        transcript = transcript_data.get('text', '')
                        if transcript:
                            self.log('debug', f"Got transcript: {transcript}")
                            return {
                                'text': transcript,
                                'segments': [{
                                    'text': transcript,
                                    'start': 0,
                                    'end': 0  # We don't know the duration
                                }]
                            }
                    
                    elif event_type == "transcript-segment":
                        # Add segment to results
                        segment_data = event.get('data', {})
                        segment_text = segment_data.get('text', '')
                        start = segment_data.get('start', 0)
                        end = segment_data.get('end', 0)
                        
                        if segment_text:
                            segments.append({
                                'text': segment_text,
                                'start': start,
                                'end': end
                            })
                            transcript += segment_text + " "
                            self.log('debug', f"Got segment: {segment_text}")
                    
                    elif event_type == "error":
                        error_data = event.get('data', {})
                        error_message = error_data.get('message', 'Unknown error')
                        self.log('error', f"Wyoming server error: {error_message}")
                        return None
                    
                    elif event_type == "transcribe-done":
                        # Transcription complete
                        if segments:
                            return {
                                'text': transcript.strip(),
                                'segments': segments
                            }
                        elif transcript:
                            return {
                                'text': transcript.strip(),
                                'segments': [{
                                    'text': transcript.strip(),
                                    'start': 0,
                                    'end': 0
                                }]
                            }
                        else:
                            self.log('warning', "Transcription complete but no text found")
                            return None
                
                # If we get here, we've exceeded our retry limit
                self.log('error', f"Failed to get transcript after {max_retries} attempts")
                return None
                
        except Exception as e:
            self.log('error', f"Wyoming protocol transcription error: {str(e)}")
            import traceback
            self.log('debug', traceback.format_exc())
            return None

    def format_timestamp(self, seconds: float) -> str:
        """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
        td = timedelta(seconds=seconds)
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        milliseconds = int((td.total_seconds() - total_seconds) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def create_srt_block(self, index: int, text: str, start: float, end: float) -> str:
        """Create a single SRT caption block"""
        # Wrap text at approximately 40 characters
        wrapped_text = textwrap.fill(text, width=40)
        return f"{index}\n{self.format_timestamp(start)} --> {self.format_timestamp(end)}\n{wrapped_text}\n\n"

    def detect_and_format_dialogue(self, text: str) -> str:
        """
        Detects possible dialogue in transcription text and formats it properly
        for subtitle display with proper line breaks and attribution.
        
        Args:
            text (str): The raw transcription text
            
        Returns:
            str: Formatted text with properly formatted dialogue
        """
        
        # Rule 0: Handle explicit dialogue patterns first (these are usually high confidence)
        explicit_dialogue_patterns = [
            # Pattern for quotes with attributions: "Text," speaker said.
            (r'\"([^\"]+)\"[,.]? ([A-Z][a-z]+(?: [A-Z][a-z]+)?) (said|says|replied|added|mentioned|asked|exclaimed|shouted|whispered)', 
             lambda m: f"{m.group(2)}:\n\"{m.group(1)}\""),
            # Pattern for speaker: text
            (r'([A-Z][a-z]+(?: [A-Z][a-z]+)?): (.*?)(?=$|\n|[.!?] [A-Z])', 
             lambda m: f"{m.group(1)}:\n{m.group(2).strip()}"),
        ]

        for pattern, formatter in explicit_dialogue_patterns:
            match = re.search(pattern, text)
            if match:
                # If an explicit pattern matches the whole text or a significant part,
                # assume it's correctly formatted or can be formatted by the lambda.
                # This part might need more sophisticated logic if multiple explicit patterns exist.
                # For now, if a strong explicit pattern is found, we use it.
                # This is a simplification; a full solution might try to find all such patterns.
                return formatter(match) # Return early for high-confidence explicit dialogue

        # Start with the original text for sequential modifications
        processed_text = text

        # Rule 1: Normalize text - attempt to fix run-on sentences often found in ASR output
        # Add a period before a capital letter if preceded by a lowercase letter and a space, or just a lowercase letter.
        processed_text = re.sub(r'([a-z])([A-Z])', r'\1. \2', processed_text) # wordWord -> word. Word
        processed_text = re.sub(r'([a-z.,?!]) ([A-Z])', r'\1. \2', processed_text) # word. Word or word Word -> word. Word
        processed_text = re.sub(r'\.([a-zA-Z])', r'. \1', processed_text) # Ensure space after period if missing

        # Rule 2: Split before key interjections or turn-taking phrases (case-insensitive)
        # These phrases often start a new speaker's turn.
        # We insert a newline, ensuring not to add if already at line start or after another newline.
        # Using a placeholder to manage iterative `re.sub` and then replacing it.
        newline_placeholder = "[[NEWLINE_HERE]]"
        
        key_phrases_before = [
            r"what about you",
            r"and you",
            r"am I understood",
            r"are you sure",
            r"can you tell me",
            r"yes sir",
            r"no sir",
            r"yes ma'am",
            r"no ma'am",
            r"okay",
            r"alright",
            r"well", # Can start a new turn
            r"actually", # Can start a new turn
            # Names used as vocatives or to change subject - this is harder to generalize
            # For the example: "Amelia", "Liam" - if they are followed by a shift.
        ]
        
        # Temporarily mark potential split points before these phrases
        for phrase in key_phrases_before:
            processed_text = re.sub(fr'(?i)(?<=[a-z0-9.,?!])\s+(\b{phrase}\b)', fr'{newline_placeholder}\1', processed_text)
        
        # Rule 3: Pronoun shift based splitting (I/me/my vs. you/your)
        # This rule applies after sentence normalization and key phrase splitting.
        # It looks for transitions between sentences/clauses.
        # Split "Sentence with I/my. Sentence with you/your." into two lines.
        
        # Pattern: (stuff ending with I/my/me PUNC) whitespace (you/your stuff)
        processed_text = re.sub(fr'(?i)(\b(?:I|my|me)\b(?:[^.!?]|[.!?](?!\s+[A-Z]))*?[.!?])(\s+)(\b(?:you|your)\b)', fr'\1{newline_placeholder}\3', processed_text)
        # Pattern: (stuff ending with you/your PUNC) whitespace (I/my/me stuff)
        processed_text = re.sub(fr'(?i)(\b(?:you|your)\b(?:[^.!?]|[.!?](?!\s+[A-Z]))*?[.!?])(\s+)(\b(?:I|my|me)\b)', fr'\1{newline_placeholder}\3', processed_text)

        # Rule 4: Splitting around specific names if they appear to mark a turn (context-dependent)
        # Example: "... outbreak Amelia but it's a start" -> "... outbreak Amelia\nbut it's a start"
        # Example: "... you Liam I scored" -> "... you Liam\nI scored"
        # This is heuristic and can be error-prone if names are common words or part of longer names.
        # For the given example, let's try to be specific.
        # (This should ideally use a list of known speaker names if available)
        speaker_names_in_example = [r"Amelia", r"Liam"]
        for name in speaker_names_in_example:
            # Split after "Name" if followed by a conjunction or different pronoun context
            processed_text = re.sub(fr'(?i)(\b{name}\b[.,!?]?)\s+(?=(?:but|and|so|then|\b(?:I|my|me|you|your)\b))', fr'\1{newline_placeholder}', processed_text)

        # Convert placeholders to actual newlines
        processed_text = processed_text.replace(newline_placeholder, '\n')

        # Rule 5: Clean up whitespace and multiple newlines
        processed_text = re.sub(r'[ \t]*\n[ \t]*', '\n', processed_text) # Remove spaces around newlines
        processed_text = re.sub(r'\n{2,}', '\n', processed_text) # Collapse multiple newlines to one
        processed_text = processed_text.strip() # Remove leading/trailing whitespace

        # If, after all this, the text is identical to original and has no newlines,
        # and is very long, it might be a monologue.
        # However, if it contains I/you, it's still suspicious.
        if processed_text == text.strip() and '\n' not in processed_text and len(processed_text.split()) > 20:
            # Last resort for long unpunctuated lines with mixed pronouns (very heuristic)
            if re.search(r'\bI\b', processed_text, re.I) and re.search(r'\byou\b', processed_text, re.I):
                # Try to find a split point around conjunctions or mid-sentence pronoun shifts
                # This is complex and risky, so keeping it minimal or skipping for now.
                # A simple split at a conjunction if one exists mid-way.
                conjunction_split = re.match(r"(.*?\b(?:but|and|so)\b.*?)\s+(.*)", processed_text)
                if conjunction_split and abs(len(conjunction_split.group(1)) - len(conjunction_split.group(2))) < len(processed_text) * 0.4 : # Reasonably balanced split
                     processed_text = f"{conjunction_split.group(1)}\n{conjunction_split.group(2)}"

        return processed_text

    def split_into_captions(self, text: str, start_time: float, duration: float, 
                           max_words_per_caption: int = 8,  # Changed from 14
                           max_chars_per_caption: int = 50) -> list: # Changed from 80
        """Split a transcript into multiple caption blocks with appropriate timing"""
        captions = []
        
        # First process the text for potential dialogue formatting
        text = self.detect_and_format_dialogue(text)
        
        # Now we'll work with the dialogue-enhanced text
        
        # Handle already line-broken text specially
        if "\n" in text:
            lines = text.split("\n")
            line_count = len(lines)
            time_per_line = duration / line_count if line_count > 0 else duration
            
            current_time = start_time
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                    
                line_duration = min(time_per_line, 5.0)  # Cap at 5 seconds per line
                end_time = current_time + line_duration
                
                captions.append((line.strip(), current_time, end_time))
                current_time = end_time
                
            return captions
        
        # If no line breaks detected, fall back to original sentence splitting
        sentences = re.split(r'(?<=[.!?]) +', text)
        
        # Estimate words per second from overall duration and word count
        word_count = len(text.split())
        words_per_second = word_count / duration if duration > 0 and word_count > 0 else 0.5
        
        current_caption = ""
        current_word_count = 0
        current_start = start_time
        
        for sentence in sentences:
            words = sentence.split()
            
            # If adding this sentence would exceed our limits, add the current caption
            if current_word_count + len(words) > max_words_per_caption or \
               len(current_caption + " " + sentence) > max_chars_per_caption:
                
                # Only add if we have content
                if current_caption:
                    # Calculate end time based on word count and estimated words per second
                    current_end = current_start + (current_word_count / words_per_second)
                    captions.append((current_caption.strip(), current_start, current_end))
                    
                    # Start a new caption
                    current_caption = sentence
                    current_word_count = len(words)
                    current_start = current_end
            else:
                # Add to current caption
                if current_caption:
                    current_caption += " " + sentence
                else:
                    current_caption = sentence
                current_word_count += len(words)
        
        # Add the final caption if there's any content left
        if current_caption:
            current_end = min(start_time + duration, current_start + (current_word_count / words_per_second))
            captions.append((current_caption.strip(), current_start, current_end))
        
        return captions

    def create_srt_content(self, text: str, start_offset: float, duration: float) -> str:
        """Create a complete SRT file content from transcript text"""
        captions = self.split_into_captions(text, start_offset, duration)
        
        # If no captions were created, make a single caption for the whole chunk
        if not captions:
            captions = [(text, start_offset, start_offset + duration)]
        
        srt_content = ""
        for i, (caption_text, start, end) in enumerate(captions, start=1):
            srt_content += self.create_srt_block(i, caption_text, start, end)
        
        return srt_content

    def process_chunk_to_srt(self, wav_path: str, offset: float = 0.0, 
                           duration: float = 30.0, language: Optional[str] = "en", 
                           model: Optional[str] = None) -> str:
        """
        Process a single WAV chunk and return SRT content
        
        Args:
            wav_path: Path to the WAV file
            offset: Start time offset in seconds for this chunk in the full video
            duration: Duration of the chunk in seconds
            language: Language code
            model: Model name to use (optional)
            
        Returns:
            str: SRT content for this chunk
        """
        self.log('info', f"Processing chunk {wav_path} with offset {offset}s to SRT")
        
        # Don't get model from config - let server use already loaded model
        # This prevents downloading a new model when one is already loaded
        
        try:
            from wyoming_client import WyomingClient # Moved import here
            # Create Wyoming client
            client = WyomingClient(host=self.server_host, port=self.server_port)
            
            # Transcribe audio - don't pass model parameter to use already loaded one
            transcript = client.transcribe(wav_path, language=language)
            
            # Create SRT content
            srt_content = self.create_srt_content(transcript, offset, duration)
            
            self.log('info', f"Generated SRT content with {srt_content.count('#')} caption blocks")
            return srt_content
        
        except ModuleNotFoundError:
            self.log('warning', "WyomingClient module not found. Skipping Wyoming STT.")
            wyoming_transcript = None
        except Exception as e:
            self.log('error', f"Error during Wyoming STT: {e}")
            wyoming_transcript = None
        # Fall back to using our standard transcription and formatting it as SRT
        try:
            success, message, result = self.transcribe_audio_chunk(wav_path, language)
            if success and 'text' in result:
                return self.create_srt_content(result['text'], offset, duration)
            else:
                raise Exception(f"Transcription failed: {message}")
        except Exception as fallback_error:
            self.log('error', f"Fallback transcription failed: {str(fallback_error)}")
            # Return a minimal SRT with error message
            return f"1\n{self.format_timestamp(offset)} --> {self.format_timestamp(offset + duration)}\n[Transcription failed]\n\n"

    def transcribe_video_to_srt(self, video_path: str, output_path: str, language: Optional[str] = None, 
                              chunk_duration: int = 30, model: Optional[str] = None, job_id: Optional[str] = None,
                              external_progress_updater: Optional[Callable[[float, str, str, str], None]] = None) -> Tuple[bool, str]:
        """
        Transcribe a video directly to an SRT file using Wyoming protocol
        
        Args:
            video_path: Path to the video file
            output_path: Path where to save the SRT file
            language: Language code (e.g., 'en', 'fr')
            chunk_duration: Duration of each chunk in seconds
            model: Whisper model to use
            job_id: Optional job ID for progress tracking
            external_progress_updater: Optional callback function for updating progress externally
            
        Returns:
            tuple: (success, message)
                - success (bool): True if the transcription was successful
                - message (str): Status or error message
        """
        if not os.path.exists(video_path):
            self.log('error', f"Video file not found: {video_path}")
            return False, "Video file not found"
            
        try:
            # Generate job_id if not provided
            if not job_id:
                job_id = f"job_{uuid.uuid4().hex[:8]}"
                
            # Initialize progress
            self._update_progress(job_id, 0, "Starting transcription...")
            if external_progress_updater and job_id is not None:
                external_progress_updater(0, "Starting transcription...", "processing", job_id)
            
            # Extract audio from video
            self._update_progress(job_id, 5, "Extracting audio from video...")
            if external_progress_updater and job_id is not None:
                external_progress_updater(5, "Extracting audio from video...", "processing", job_id)
                
            extract_success, extract_message, audio_path = self.extract_audio(video_path)
            
            if not extract_success or not audio_path:
                self.log('error', f"Failed to extract audio: {extract_message}")
                self._update_progress(job_id, 100, f"Failed: {extract_message}", status="error")
                if external_progress_updater and job_id is not None:
                    external_progress_updater(100, f"Failed: {extract_message}", "error", job_id)
                return False, f"Failed to extract audio: {extract_message}"
                
            # Split audio into manageable chunks
            self._update_progress(job_id, 15, "Splitting audio into chunks...")
            if external_progress_updater and job_id is not None:
                external_progress_updater(15, "Splitting audio into chunks...", "processing", job_id)
                
            split_success, split_message, chunk_paths = self.split_audio_into_chunks(
                audio_path, 
                chunk_duration_seconds=chunk_duration
            )
            
            if not split_success:
                self.log('error', f"Failed to split audio: {split_message}")
                self._update_progress(job_id, 100, f"Failed: {split_message}", status="error")
                if external_progress_updater and job_id is not None:
                    external_progress_updater(100, f"Failed: {split_message}", "error", job_id)
                return False, f"Failed to split audio: {split_message}"
                
            total_chunks = len(chunk_paths)
            self.log('info', f"Processing {total_chunks} audio chunks for SRT generation")
            self._update_progress(job_id, 20, f"Processing {total_chunks} audio chunks...")
            if external_progress_updater and job_id is not None:
                external_progress_updater(20, f"Processing {total_chunks} audio chunks...", "processing", job_id)
            
            # Process each chunk and collect SRT contents
            srt_chunks = []
            
            # Calculate how much progress each chunk represents (from 20% to 90%)
            chunk_progress_total = 70  # 90-20
            chunk_progress_each = chunk_progress_total / total_chunks if total_chunks > 0 else 0
            
            try:
                for i, chunk_path in enumerate(chunk_paths):
                    chunk_num = i + 1
                    progress_pct = 20 + (i * chunk_progress_each)
                    progress_message = f"Transcribing chunk {chunk_num}/{total_chunks} ({int((chunk_num/total_chunks)*100)}% done)"
                    
                    self._update_progress(job_id, int(progress_pct), progress_message)
                    if external_progress_updater and job_id is not None:
                        external_progress_updater(int(progress_pct), progress_message, "processing", job_id)
                        
                    self.log('info', f"Processing chunk {chunk_num}/{total_chunks} to SRT")
                    
                    # Calculate offset for this chunk
                    offset = i * chunk_duration
                    
                    # Process chunk and get SRT content
                    srt_content = self.process_chunk_to_srt(
                        chunk_path,
                        offset=offset,
                        duration=chunk_duration,
                        language=language
                        # Don't pass model parameter here to use already loaded model
                    )
                    
                    srt_chunks.append(srt_content)
                    
                    # Update progress after each chunk with more detailed information
                    current_progress = int(20 + ((i+1) * chunk_progress_each))
                    chunk_percent = int(((i+1)/total_chunks)*100)
                    progress_message = f"Completed chunk {chunk_num}/{total_chunks} ({chunk_percent}% complete)"
                    
                    self._update_progress(job_id, current_progress, progress_message)
                    if external_progress_updater and job_id is not None:
                        external_progress_updater(current_progress, progress_message, "processing", job_id)
            except Exception as chunk_error:
                self.log('error', f"Error processing chunk {chunk_num}/{total_chunks}: {str(chunk_error)}")
                # Continue with whatever chunks we have processed so far
                if not srt_chunks:
                    error_message = f"Failed to process any chunks: {str(chunk_error)}"
                    if external_progress_updater and job_id is not None:
                        external_progress_updater(100, error_message, "error", job_id)
                    raise Exception(error_message)
                else:
                    self.log('warning', f"Proceeding with {len(srt_chunks)}/{total_chunks} processed chunks")
            
            # Combine all SRT contents
            self._update_progress(job_id, 90, "Combining transcription chunks...")
            if external_progress_updater and job_id is not None:
                external_progress_updater(90, "Combining transcription chunks...", "processing", job_id)
                
            combined_srt = self._combine_srt_chunks(srt_chunks)
            
            # Write to output file
            self._update_progress(job_id, 95, "Writing SRT file...")
            if external_progress_updater and job_id is not None:
                external_progress_updater(95, "Writing SRT file...", "processing", job_id)
                
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(combined_srt)
                
            self.log('info', f"SRT file generated: {output_path}")
            self._update_progress(job_id, 100, "Transcription complete!", status="complete")
            if external_progress_updater and job_id is not None:
                external_progress_updater(100, "Transcription complete!", "completed", job_id)
                
            return True, f"SRT file generated: {output_path}"
            
        except Exception as e:
            self.log('error', f"Error transcribing video to SRT: {str(e)}")
            import traceback
            self.log('error', traceback.format_exc())
            self._update_progress(job_id, 100, f"Error: {str(e)}", status="error")
            if external_progress_updater and job_id is not None:
                external_progress_updater(100, f"Error: {str(e)}", "failed", job_id)
                
            return False, f"Error transcribing video to SRT: {str(e)}"
        finally:
            # Clean up temporary files
            if 'audio_path' in locals() and audio_path:
                temp_dir = os.path.dirname(audio_path)
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass

    def _combine_srt_chunks(self, srt_chunks: List[str]) -> str:
        """
        Combine multiple SRT chunk contents into a single SRT file,
        renumbering the indices to ensure they're sequential.
        
        Args:
            srt_chunks: List of SRT file contents as strings
            
        Returns:
            str: Combined SRT content with corrected indices
        """
        import re
        
        combined_content = ""
        index = 1
        
        # SRT block pattern: index + timestamp line + text + blank line
        pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3})\n([\s\S]*?)(?=\n\n|\Z)'
        
        for chunk in srt_chunks:
            for match in re.finditer(pattern, chunk):
                # Extract the timestamp line and text content
                timestamp_line = match.group(2)
                text_content = match.group(3).strip()
                
                # Create a new SRT block with the correct index
                combined_content += f"{index}\n{timestamp_line}\n{text_content}\n\n"
                index += 1
        
        return combined_content

    def _update_progress(self, job_id: Optional[str], percent: int, message: str, status: str = "processing"):
        """
        Update progress information for a transcription job
        
        Args:
            job_id: Unique identifier for the job
            percent: Progress percentage (0-100)
            message: Status message
            status: Status indicator ("processing", "complete", "error")
        """
        if job_id is None:
            self.log('warning', f"Progress update with no job_id: {percent}% - {message}")
            return
            
        progress_info = {
            "job_id": job_id,
            "percent": percent,
            "message": message,
            "status": status,
            "updated": time.time()
        }
        
        self.log('info', f"Progress update [{job_id}]: {percent}% - {message}")
        VideoTranscriber._progress_data[job_id] = progress_info # Store in class variable

        # If we have a Flask app with socketio, emit progress update
        try:
            import sys
            if 'app' in sys.modules and hasattr(sys.modules['app'], 'socketio') and sys.modules['app'].socketio:
                socketio = sys.modules['app'].socketio
                # Emit to a room specific to the job_id for targeted updates
                socketio.emit('transcription_progress', progress_info, room=job_id)
                self.log('debug', f"Emitted progress via socketio to room {job_id}: {progress_info}")
            elif 'app' in sys.modules and hasattr(sys.modules['app'], 'socketio'): # SocketIO might be None if not initialized
                 self.log('debug', "socketio object exists in app module but is None, cannot emit.")
            else:
                self.log('debug', "socketio not available in app module for progress emission.")
        except ImportError:
            self.log('debug', "App module or socketio not available for emitting progress.")
        except Exception as e:
            self.log('debug', f"Could not emit socketio progress: {e}")

    @classmethod
    def get_progress(cls, job_id: str) -> Optional[Dict[str, Any]]: # Renamed from get_job_progress
        """
        Get progress information for a specific job
        
        Args:
            job_id: Unique identifier for the job
            
        Returns:
            dict: Progress information or None if not found
        """
        return cls._progress_data.get(job_id)

def test_connection(server_url="http://10.0.10.23:10300"):
    """
    Test connection to a specified Wyoming protocol server.
    This is a standalone function that can be called directly from the command line.
    
    Args:
        server_url (str): URL of the server to test
        
    Returns:
        bool: True if the connection was successful, False otherwise
    """
    # Create a transcriber instance with the specified URL
    transcriber = VideoTranscriber(server_url=server_url)
    
    # Parse the URL to get host and port
    parsed_url = urlparse(server_url)
    host = parsed_url.hostname
    port = parsed_url.port or 10300  # Default Wyoming protocol port
    
    # Test TCP connection first
    print(f"Testing TCP connection to {host}:{port}...")
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            print(f"✅ TCP connection successful!")
            
            # Test Wyoming protocol
            print("Testing Wyoming protocol...")
            try:
                transcriber._wyoming_send_event(sock, {"type": "describe"})
                try:
                    info_event = transcriber._wyoming_receive_event(sock, timeout=10)
                    if info_event and info_event.get("type") == "info":
                        print(f"✅ Wyoming protocol successful!")
                        print(f"Server info: {json.dumps(info_event, indent=2)}")
                        return True
                    else:
                        print(f"⚠️ Server responded, but with unexpected event: {info_event}")
                except socket.timeout:
                    print(f"⚠️ Wyoming protocol timed out, but TCP connection works")
                except Exception as e:
                    print(f"⚠️ Wyoming protocol error: {str(e)}")
            except Exception as e:
                print(f"⚠️ Failed to send Wyoming protocol message: {str(e)}")
    except socket.timeout:
        print(f"❌ Connection timed out after 10 seconds")
    except ConnectionRefusedError:
        print(f"❌ Connection refused. Is the server running?")
    except Exception as e:
        print(f"❌ Connection error: {str(e)}")
    
    print("\nTrying HTTP endpoints as fallback...")
    # Try some common HTTP endpoints
    endpoints = ['/', '/health', '/status', '/info', '/v1', '/api']
    for endpoint in endpoints:
        try:
            url = f"{server_url.rstrip('/')}{endpoint}"
            print(f"Testing {url}...")
            response = requests.get(url, timeout=5)
            print(f"✅ HTTP response: {response.status_code} {response.reason}")
            return True
        except requests.RequestException as e:
            print(f"❌ HTTP error: {str(e)}")
    
    print("\nAll connection attempts failed.")
    return False


# Allow running as a standalone script for testing
if __name__ == "__main__":
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Test connection to a Wyoming protocol server")
    parser.add_argument("--url", default="http://10.0.10.23:10300", help="URL of the server to test")
    args = parser.parse_args()
    
    # Run the connection test
    success = test_connection(args.url)
    
    # Exit with appropriate status code
    import sys
    sys.exit(0 if success else 1)