import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
import ffmpeg
import os

class VideoDownloader:
    def __init__(self):
        self.cookies_file = Path("cookies.txt")
    
    async def extract_info(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract video information using yt-dlp"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
        }
        
        # Add cookies if available
        if self.cookies_file.exists():
            ydl_opts['cookiefile'] = str(self.cookies_file)
        
        def _extract():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except Exception as e:
                print(f"yt-dlp extract error: {e}")
                return None
        
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, _extract)
            return info
        except Exception as e:
            print(f"Extract info error: {e}")
            return None
    
    async def download_video(self, url: str, task_id: str) -> Optional[Path]:
        """Download video using yt-dlp"""
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)
        
        output_template = str(temp_dir / f"{task_id}_temp.%(ext)s")
        
        ydl_opts = {
            'format': 'best[height<=720]/best[height<=480]/best',
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'ignoreerrors': False,
        }
        
        # Add cookies if available
        if self.cookies_file.exists():
            ydl_opts['cookiefile'] = str(self.cookies_file)
        
        def _download():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                    return True
            except Exception as e:
                print(f"yt-dlp download error: {e}")
                return False
        
        # Run in thread pool
        loop = asyncio.get_event_loop()
        try:
            success = await loop.run_in_executor(None, _download)
            if not success:
                return None
            
            # Find the downloaded file
            for file in temp_dir.glob(f"{task_id}_temp.*"):
                if file.is_file() and file.stat().st_size > 0:
                    return file
            
            return None
        except Exception as e:
            print(f"Download error: {e}")
            return None
    
    async def convert_to_hevc(self, input_file: Path, output_file: Path):
        """Convert video to HEVC using ffmpeg"""
        def _convert():
            try:
                # Check if ffmpeg is available
                subprocess.run(['ffmpeg', '-version'], 
                             capture_output=True, check=True)
                
                (
                    ffmpeg
                    .input(str(input_file))
                    .output(
                        str(output_file),
                        vcodec='libx265',
                        acodec='aac',
                        audio_bitrate='96k',
                        vf='scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',
                        preset='medium',
                        crf=23,
                        movflags='faststart'
                    )
                    .overwrite_output()
                    .run(quiet=True, capture_output=True)
                )
                return True
            except subprocess.CalledProcessError as e:
                print(f"FFmpeg not found: {e}")
                return False
            except Exception as e:
                print(f"FFmpeg conversion error: {e}")
                return False
        
        # Run in thread pool
        loop = asyncio.get_event_loop()
        try:
            success = await loop.run_in_executor(None, _convert)
            if not success:
                raise Exception("Video conversion failed")
        except Exception as e:
            print(f"Conversion error: {e}")
            raise