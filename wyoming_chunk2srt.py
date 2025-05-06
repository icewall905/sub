#!/usr/bin/env python3
"""
Transcribe a single 30-second WAV chunk with Wyoming Faster-Whisper
and write it to an .srt subtitle file.

Example:
    python wyoming_chunk2srt.py --wav chunk_000.wav --offset 0 \
        --host 10.0.10.23 --port 10300 --model large-v3
"""

import argparse
import os
import sys
import textwrap
from datetime import timedelta
from pathlib import Path
import logging
import re

# Add the project's 'py' directory to path so we can import the custom WyomingClient
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "py"))

from wyoming_client import WyomingClient

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("wyoming_chunk2srt")

## ---------- SRT helpers ------------------------------------------------------

def format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int((td.total_seconds() - total_seconds) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def create_srt_block(index: int, text: str, start: float, end: float) -> str:
    """Create a single SRT caption block"""
    # Wrap text at approximately 40 characters
    wrapped_text = textwrap.fill(text, width=40)
    return f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{wrapped_text}\n\n"

def split_into_captions(text: str, start_time: float, duration: float, 
                       max_words_per_caption: int = 14,
                       max_chars_per_caption: int = 80) -> list:
    """Split a transcript into multiple caption blocks with appropriate timing"""
    captions = []
    
    # Basic sentence splitting
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

def create_srt_content(text: str, start_offset: float, duration: float) -> str:
    """Create a complete SRT file content from transcript text"""
    captions = split_into_captions(text, start_offset, duration)
    
    # If no captions were created, make a single caption for the whole chunk
    if not captions:
        captions = [(text, start_offset, start_offset + duration)]
    
    srt_content = ""
    for i, (caption_text, start, end) in enumerate(captions, start=1):
        srt_content += create_srt_block(i, caption_text, start, end)
    
    return srt_content

## ---------- Main functionality ----------------------------------------------

def process_chunk(wav_path: str, host: str, port: int, model: str, language: str,  
                 offset: float, duration: float) -> str:
    """
    Process a single WAV chunk and return SRT content
    
    Args:
        wav_path: Path to the WAV file
        host: Wyoming server hostname or IP
        port: Wyoming server port
        model: Model name to use
        language: Language code
        offset: Start time offset in seconds for this chunk in the full video
        duration: Duration of the chunk in seconds
        
    Returns:
        str: SRT content for this chunk
    """
    logger.info(f"Processing chunk {wav_path} with offset {offset}s")
    
    # Create Wyoming client
    client = WyomingClient(host=host, port=port, logger=logger)
    
    try:
        # Transcribe audio
        transcript = client.transcribe(wav_path, language=language, model=model)
        
        # Create SRT content
        srt_content = create_srt_content(transcript, offset, duration)
        
        logger.info(f"Generated SRT content with {srt_content.count('#')} caption blocks")
        return srt_content
    
    except Exception as e:
        logger.error(f"Error processing chunk: {str(e)}")
        raise

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="30-s WAV → SRT via Wyoming")
    parser.add_argument("--wav", required=True, help="Path to WAV file")
    parser.add_argument("--host", default="10.0.10.23", help="Wyoming server hostname/IP")
    parser.add_argument("--port", type=int, default=10300, help="Wyoming server port")
    parser.add_argument("--model", default="large-v3", help="Model name to use")
    parser.add_argument("--lang", default="en", help="Language code")
    parser.add_argument("--offset", type=float, default=0.0,
                        help="Start time (s) of this chunk in the episode")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Duration (s) - keep at 30 unless you changed chunk size")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    try:
        # Process the chunk
        srt_content = process_chunk(
            args.wav, args.host, args.port, args.model, args.lang,
            args.offset, args.duration
        )
        
        # Write SRT file
        out_path = Path(args.wav).with_suffix(".srt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
            
        logger.info(f"SRT file created: {out_path}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()