#!/usr/bin/env python3
import asyncio
import json
import struct
from typing import Optional, List, Dict, Any, Generator, AsyncGenerator
import logging

logger = logging.getLogger(__name__)

class WyomingClient:
    """Client for communicating with Wyoming protocol services"""
    
    def __init__(self, host: str = "localhost", port: int = 10300):
        """Initialize Wyoming client with host and port"""
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.connected = False
    
    async def connect(self) -> bool:
        """Connect to the Wyoming server"""
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            self.connected = True
            logger.info(f"Connected to Wyoming server at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Wyoming server: {e}")
            self.connected = False
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from the Wyoming server"""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")
        self.connected = False
        logger.info("Disconnected from Wyoming server")
    
    async def send_event(self, event_type: str, data: Dict[str, Any] = None) -> None:
        """Send an event to the Wyoming server"""
        if not self.connected:
            raise RuntimeError("Not connected to Wyoming server")
        
        event = {"type": event_type}
        if data:
            event.update(data)
        
        event_json = json.dumps(event).encode("utf-8")
        header = struct.pack("!I", len(event_json))
        
        self.writer.write(header + event_json)
        await self.writer.drain()
        logger.debug(f"Sent event: {event_type}")
    
    async def receive_event(self) -> Dict[str, Any]:
        """Receive an event from the Wyoming server"""
        if not self.connected:
            raise RuntimeError("Not connected to Wyoming server")
        
        try:
            header = await self.reader.readexactly(4)
            event_length = struct.unpack("!I", header)[0]
            event_json = await self.reader.readexactly(event_length)
            event = json.loads(event_json.decode("utf-8"))
            logger.debug(f"Received event: {event.get('type')}")
            return event
        except asyncio.IncompleteReadError:
            logger.warning("Connection closed by server")
            self.connected = False
            raise
        except Exception as e:
            logger.error(f"Error receiving event: {e}")
            raise
    
    async def audio_to_srt(self, audio_data: bytes, rate: int = 16000, language: str = "en") -> str:
        """Convert audio data to SRT subtitles using Wyoming ASR service"""
        if not self.connected:
            await self.connect()
        
        # Set up ASR parameters
        await self.send_event("asr-start", {
            "language": language,
            "sample_rate": rate,
            "format": "wav",
            "client": {"name": "subtitle-translator"}
        })
        
        # Send audio data
        await self.send_event("asr-audio", {"audio": audio_data})
        
        # Signal end of audio
        await self.send_event("asr-stop")
        
        full_transcript = ""
        transcript_parts = []
        start_time = 0
        
        # Process ASR results
        while True:
            event = await self.receive_event()
            event_type = event.get("type")
            
            if event_type == "asr-result":
                transcript = event.get("text", "")
                if transcript:
                    full_transcript += transcript + " "
                    
                # Get timing information if available
                if "start_time" in event:
                    start_time = float(event["start_time"])
                    end_time = float(event.get("end_time", start_time + 3.0))
                    transcript_parts.append((transcript, start_time, end_time))
                    
            elif event_type == "asr-complete":
                break
        
        return full_transcript.strip(), transcript_parts
    
    def transcribe(self, wav_path: str, language: Optional[str] = None, model: Optional[str] = None) -> str:
        """Synchronous wrapper to transcribe WAV file via audio_to_srt"""
        try:
            with open(wav_path, 'rb') as f:
                audio_data = f.read()
        except Exception as e:
            logger.error(f"Failed to read WAV file {wav_path}: {e}")
            raise
        # Run async audio_to_srt
        transcript, _ = asyncio.run(self.audio_to_srt(audio_data, rate=16000, language=language or 'en'))
        return transcript
    
    async def __aenter__(self):
        """Context manager enter"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.disconnect()

# Simple test if run directly
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    
    if len(sys.argv) < 3:
        print("Usage: python wyoming_client.py <host:port> <wav_file> [language]")
        sys.exit(1)
        
    host_port = sys.argv[1].split(":")
    host = host_port[0]
    port = int(host_port[1]) if len(host_port) > 1 else 10300
    
    wav_file = sys.argv[2]
    language = sys.argv[3] if len(sys.argv) > 3 else None
    
    client = WyomingClient(host, port)
    try:
        text = client.transcribe(wav_file, language)
        print(f"Transcription: {text}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)