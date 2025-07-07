import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
import os
import random
import time
import json

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

        if self.cookies_file.exists():
            opts['cookiefile'] = str(self.cookies_file)

        return opts

    async def extract_info(self, url: str) -> Optional[Dict[str, Any]]:
        ydl_opts = self.get_ydl_opts(download=False)

        def _extract():
            try:
                time.sleep(random.uniform(1, 3))
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info.get("age_limit", 0) >= 18:
                        raise Exception("Age-restricted video — upload cookies.txt")
                    if info.get("webpage_url_basename", "") == "verify_eligibility":
                        raise Exception("Login wall detected — upload cookies.txt")
                    return info
            except yt_dlp.utils.DownloadError as e:
                raise Exception(f"yt-dlp extract error: {e}")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _extract)

    async def download_video(self, url: str, task_id: str) -> Optional[Path]:
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)
        output_template = str(temp_dir / f"{task_id}_temp.%(ext)s")

        ydl_opts = self.get_ydl_opts(download=True)
        ydl_opts.update({
            'format': 'bv*[ext=mp4][height<=720]+ba[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': output_template,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'ignoreerrors': False,
            'retries': 3,
            'fragment_retries': 5,
            'file_access_retries': 3,
            'socket_timeout': 30,
            'merge_output_format': 'mp4',
        })

        def _download():
            try:
                time.sleep(random.uniform(2, 5))
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                    return True
            except yt_dlp.utils.DownloadError as e:
                raise Exception(f"yt-dlp download error: {e}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _download)

        downloaded_files = list(temp_dir.glob(f"{task_id}_temp.*"))
        if not downloaded_files:
            raise Exception("Download completed but no file was created")

        downloaded_file = max(downloaded_files, key=lambda f: f.stat().st_size)
        if downloaded_file.stat().st_size == 0:
            raise Exception("Downloaded file is empty")

        if not await self.validate_video_file(downloaded_file):
            raise Exception("Downloaded file is not a valid video file")

        return downloaded_file

    async def validate_video_file(self, file_path: Path) -> bool:
        def _validate():
            try:
                result = subprocess.run([
                    'ffprobe', '-v', 'quiet', '-print_format', 'json',
                    '-show_format', '-show_streams', str(file_path)
                ], capture_output=True, text=True, timeout=30)

                if result.returncode != 0:
                    return False

                probe_data = json.loads(result.stdout)
                video_streams = [s for s in probe_data.get('streams', []) if s.get('codec_type') == 'video']
                return bool(video_streams)
            except:
                return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _validate)

    async def convert_to_hevc(self, input_file: Path, output_file: Path):
        def _convert():
            try:
                probe_result = subprocess.run([
                    'ffprobe', '-v', 'quiet', '-print_format', 'json', 
                    '-show_format', '-show_streams', str(input_file)
                ], capture_output=True, text=True, timeout=30)

                probe_data = json.loads(probe_result.stdout)
                video_streams = [s for s in probe_data.get('streams', []) if s.get('codec_type') == 'video']

                hevc_cmd = [
                    'ffmpeg', '-i', str(input_file),
                    '-c:v', 'libx265',
                    '-c:a', 'aac',
                    '-b:a', '96k',
                    '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',
                    '-preset', 'medium',
                    '-crf', '23',
                    '-movflags', 'faststart',
                    '-avoid_negative_ts', 'make_zero',
                    '-fflags', '+genpts',
                    '-y', str(output_file)
                ]
                result = subprocess.run(hevc_cmd, capture_output=True, text=True, timeout=1800)
                if result.returncode == 0 and output_file.exists() and output_file.stat().st_size > 0:
                    return True

                h264_cmd = [
                    'ffmpeg', '-i', str(input_file),
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-b:a', '96k',
                    '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',
                    '-preset', 'medium',
                    '-crf', '23',
                    '-movflags', 'faststart',
                    '-avoid_negative_ts', 'make_zero',
                    '-fflags', '+genpts',
                    '-y', str(output_file)
                ]
                result = subprocess.run(h264_cmd, capture_output=True, text=True, timeout=1800)
                if result.returncode == 0 and output_file.exists() and output_file.stat().st_size > 0:
                    return True

                copy_cmd = [
                    'ffmpeg', '-i', str(input_file),
                    '-c', 'copy',
                    '-movflags', 'faststart',
                    '-avoid_negative_ts', 'make_zero',
                    '-y', str(output_file)
                ]
                result = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0 and output_file.exists() and output_file.stat().st_size > 0:
                    return True

                raise Exception(f"Video conversion failed: {result.stderr}")

            except subprocess.TimeoutExpired:
                raise Exception("Video conversion timed out")
            except Exception as e:
                raise Exception(f"Video conversion failed: {str(e)}")

        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, _convert)

        if not success:
            raise Exception("Video conversion failed")

        if not await self.validate_video_file(output_file):
            raise Exception("Converted file is not valid")
