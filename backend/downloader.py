import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
import ffmpeg
import os
import random
import time

class VideoDownloader:
    def __init__(self):
        self.cookies_file = Path("cookies.txt")
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0'
        ]
    
    def get_ydl_opts(self, download=True):
        """Get yt-dlp options with anti-detection measures"""
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'user_agent': random.choice(self.user_agents),
            'referer': 'https://www.youtube.com/',
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
            'sleep_interval': 1,
            'max_sleep_interval': 5,
            'sleep_interval_subtitles': 1,
        }
        
        if not download:
            opts['skip_download'] = True
        
        # Add cookies if available
        if self.cookies_file.exists():
            opts['cookiefile'] = str(self.cookies_file)
        
        return opts
    
    async def extract_info(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract video information using yt-dlp with anti-detection"""
        ydl_opts = self.get_ydl_opts(download=False)
        
        def _extract():
            try:
                # Add random delay to avoid rate limiting
                time.sleep(random.uniform(1, 3))
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                print(f"yt-dlp extract error: {error_msg}")
                
                # Check for specific YouTube blocking patterns
                if any(phrase in error_msg.lower() for phrase in [
                    'sign in to confirm', 'not a bot', 'private video', 
                    'video unavailable', 'removed by the user'
                ]):
                    raise Exception(f"Video access blocked: {error_msg}")
                elif 'http error 403' in error_msg.lower():
                    raise Exception("Access forbidden - video may be region-locked or require authentication")
                elif 'http error 404' in error_msg.lower():
                    raise Exception("Video not found - it may have been deleted or made private")
                else:
                    raise Exception(f"Failed to extract video info: {error_msg}")
            except Exception as e:
                print(f"Unexpected extract error: {e}")
                raise Exception(f"Could not extract video information: {str(e)}")
        
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, _extract)
            return info
        except Exception as e:
            print(f"Extract info error: {e}")
            raise
    
    async def download_video(self, url: str, task_id: str) -> Optional[Path]:
        """Download video using yt-dlp with enhanced error handling"""
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)
        
        output_template = str(temp_dir / f"{task_id}_temp.%(ext)s")
        
        ydl_opts = self.get_ydl_opts(download=True)
        ydl_opts.update({
            'format': 'best[height<=720][ext=mp4]/best[height<=720]/best[height<=480]/best',
            'outtmpl': output_template,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'ignoreerrors': False,
            'retries': 3,
            'fragment_retries': 3,
            'file_access_retries': 3,
        })
        
        def _download():
            try:
                # Add random delay
                time.sleep(random.uniform(2, 5))
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                    return True
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                print(f"yt-dlp download error: {error_msg}")
                
                # Provide specific error messages
                if any(phrase in error_msg.lower() for phrase in [
                    'sign in to confirm', 'not a bot'
                ]):
                    raise Exception("YouTube is blocking automated access. Please try uploading cookies.txt file or try again later.")
                elif 'http error 403' in error_msg.lower():
                    raise Exception("Access forbidden. Video may be region-locked, private, or require authentication. Try uploading cookies.txt.")
                elif 'http error 404' in error_msg.lower():
                    raise Exception("Video not found. It may have been deleted, made private, or the URL is incorrect.")
                elif 'private video' in error_msg.lower():
                    raise Exception("This is a private video. You need to upload cookies.txt from a logged-in session.")
                elif 'video unavailable' in error_msg.lower():
                    raise Exception("Video is unavailable. It may be region-locked or removed.")
                else:
                    raise Exception(f"Download failed: {error_msg}")
            except Exception as e:
                print(f"Unexpected download error: {e}")
                raise Exception(f"Download failed: {str(e)}")
        
        # Run in thread pool
        loop = asyncio.get_event_loop()
        try:
            success = await loop.run_in_executor(None, _download)
            if not success:
                raise Exception("Download failed - unknown error")
            
            # Find the downloaded file
            downloaded_files = list(temp_dir.glob(f"{task_id}_temp.*"))
            if not downloaded_files:
                raise Exception("Download completed but no file was created")
            
            # Get the largest file (in case multiple formats were downloaded)
            downloaded_file = max(downloaded_files, key=lambda f: f.stat().st_size)
            
            if downloaded_file.stat().st_size == 0:
                raise Exception("Downloaded file is empty")
            
            return downloaded_file
            
        except Exception as e:
            print(f"Download error: {e}")
            # Clean up any partial downloads
            for temp_file in temp_dir.glob(f"{task_id}_temp.*"):
                try:
                    temp_file.unlink()
                except:
                    pass
            raise
    
    async def convert_to_hevc(self, input_file: Path, output_file: Path):
        """Convert video to HEVC using ffmpeg with better error handling"""
        def _convert():
            try:
                # Check if ffmpeg is available
                result = subprocess.run(['ffmpeg', '-version'], 
                                      capture_output=True, text=True)
                if result.returncode != 0:
                    raise Exception("FFmpeg is not available")
                
                # Get input file info first
                probe_result = subprocess.run([
                    'ffprobe', '-v', 'quiet', '-print_format', 'json', 
                    '-show_format', '-show_streams', str(input_file)
                ], capture_output=True, text=True)
                
                if probe_result.returncode != 0:
                    raise Exception("Could not analyze input video file")
                
                # Run conversion
                cmd = [
                    'ffmpeg', '-i', str(input_file),
                    '-c:v', 'libx265',
                    '-c:a', 'aac',
                    '-b:a', '96k',
                    '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',
                    '-preset', 'medium',
                    '-crf', '23',
                    '-movflags', 'faststart',
                    '-y',  # Overwrite output file
                    str(output_file)
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 min timeout
                
                if result.returncode != 0:
                    error_output = result.stderr
                    if 'libx265' in error_output:
                        raise Exception("HEVC encoder (libx265) not available. Using fallback H.264 encoding.")
                    else:
                        raise Exception(f"Video conversion failed: {error_output}")
                
                # Verify output file was created and has content
                if not output_file.exists() or output_file.stat().st_size == 0:
                    raise Exception("Conversion completed but output file is missing or empty")
                
                return True
                
            except subprocess.TimeoutExpired:
                raise Exception("Video conversion timed out (file too large)")
            except subprocess.CalledProcessError as e:
                raise Exception(f"FFmpeg error: {e}")
            except Exception as e:
                print(f"FFmpeg conversion error: {e}")
                
                # Fallback to H.264 if HEVC fails
                if 'libx265' in str(e):
                    try:
                        print("Falling back to H.264 encoding...")
                        cmd_fallback = [
                            'ffmpeg', '-i', str(input_file),
                            '-c:v', 'libx264',
                            '-c:a', 'aac',
                            '-b:a', '96k',
                            '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',
                            '-preset', 'medium',
                            '-crf', '23',
                            '-movflags', 'faststart',
                            '-y',
                            str(output_file)
                        ]
                        
                        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=1800)
                        
                        if result.returncode == 0 and output_file.exists() and output_file.stat().st_size > 0:
                            return True
                    except:
                        pass
                
                raise Exception(f"Video conversion failed: {str(e)}")
        
        # Run in thread pool
        loop = asyncio.get_event_loop()
        try:
            success = await loop.run_in_executor(None, _convert)
            if not success:
                raise Exception("Video conversion failed")
        except Exception as e:
            print(f"Conversion error: {e}")
            raise
