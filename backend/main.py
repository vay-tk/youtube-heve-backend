import os
import sys
import uuid
import asyncio
import subprocess
import uvicorn
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import aiofiles
from dotenv import load_dotenv
from downloader import VideoDownloader
from utils import sanitize_filename, format_duration

load_dotenv()

app = FastAPI(
    title="YouTube HEVC Downloader API", 
    version="1.0.0",
    description="Convert YouTube videos to 720p HEVC format"
)

# CORS configuration - Allow all origins for now, restrict in production
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if FRONTEND_ORIGIN == "*" else [FRONTEND_ORIGIN, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Ensure directories exist
DOWNLOADS_DIR = Path("downloads")
TEMP_DIR = Path("temp")
DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# Task storage (in production, use Redis or database)
tasks = {}

class DownloadRequest(BaseModel):
    url: str
    rename: Optional[str] = None

class DownloadResponse(BaseModel):
    taskId: str
    status: str
    message: str

class StatusResponse(BaseModel):
    status: str
    filename: Optional[str] = None
    message: Optional[str] = None
    videoInfo: Optional[dict] = None
    progress: Optional[str] = None

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "message": "YouTube HEVC Downloader API is running", 
        "version": "1.0.0",
        "status": "healthy"
    }

@app.get("/health")
async def health_check():
    """Detailed health check"""
    try:
        # Test directories
        downloads_writable = os.access(DOWNLOADS_DIR, os.W_OK)
        temp_writable = os.access(TEMP_DIR, os.W_OK)
        
        # Test ffmpeg
        import subprocess
        try:
            ffmpeg_result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=10)
            ffmpeg_available = ffmpeg_result.returncode == 0
        except:
            ffmpeg_available = False
        
        # Test ffprobe
        try:
            ffprobe_result = subprocess.run(['ffprobe', '-version'], capture_output=True, timeout=10)
            ffprobe_available = ffprobe_result.returncode == 0
        except:
            ffprobe_available = False
        
        return {
            "status": "healthy",
            "downloads_dir": str(DOWNLOADS_DIR.absolute()),
            "temp_dir": str(TEMP_DIR.absolute()),
            "downloads_writable": downloads_writable,
            "temp_writable": temp_writable,
            "ffmpeg_available": ffmpeg_available,
            "ffprobe_available": ffprobe_available,
            "active_tasks": len(tasks),
            "python_version": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@app.post("/api/upload-cookies")
async def upload_cookies(cookies: UploadFile = File(...)):
    """Upload cookies.txt file for private/age-restricted videos"""
    try:
        if not cookies.filename or not cookies.filename.endswith('.txt'):
            raise HTTPException(status_code=400, detail="Only .txt files are allowed")
        
        if cookies.size and cookies.size > 1024 * 1024:  # 1MB limit
            raise HTTPException(status_code=400, detail="File too large (max 1MB)")
        
        cookies_path = Path("cookies.txt")
        async with aiofiles.open(cookies_path, 'wb') as f:
            content = await cookies.read()
            await f.write(content)
        
        # Validate the uploaded cookies
        downloader = VideoDownloader()
        validation_result = downloader.validate_cookies_file()
        
        if validation_result["valid"]:
            return {
                "message": "Cookies uploaded and validated successfully! This will help access private or age-restricted videos.",
                "validation": validation_result
            }
        else:
            return {
                "message": "Cookies uploaded but validation failed. The file may not work properly.",
                "validation": validation_result,
                "help": [
                    "To export proper cookies.txt:",
                    "1. Install a browser extension like 'Get cookies.txt LOCALLY'",
                    "2. Go to youtube.com and make sure you're logged in",
                    "3. Use the extension to export cookies for youtube.com",
                    "4. Upload the resulting cookies.txt file"
                ]
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload cookies: {str(e)}")

@app.post("/api/download", response_model=DownloadResponse)
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Start video download process"""
    try:
        # Validate URL
        if not request.url or not request.url.strip():
            raise HTTPException(status_code=400, detail="URL is required")
        
        # Basic YouTube URL validation
        youtube_patterns = [
            "youtube.com/watch",
            "youtu.be/",
            "youtube.com/embed/",
            "youtube.com/v/",
            "m.youtube.com/watch"
        ]
        
        if not any(pattern in request.url for pattern in youtube_patterns):
            raise HTTPException(status_code=400, detail="Please provide a valid YouTube URL")
        
        task_id = str(uuid.uuid4())[:12]
        
        # Initialize task status
        tasks[task_id] = {
            "status": "processing",
            "progress": "starting",
            "message": "Initializing download...",
            "videoInfo": None,
            "filename": None,
            "url": request.url,
            "rename": request.rename
        }
        
        # Start background download task
        background_tasks.add_task(download_video_task, task_id, request.url, request.rename)
        
        return DownloadResponse(
            taskId=task_id,
            status="processing",
            message="Download started successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start download: {str(e)}")

@app.get("/api/status/{task_id}", response_model=StatusResponse)
async def get_status(task_id: str):
    """Get download status for a task"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    return StatusResponse(
        status=task["status"],
        filename=task.get("filename"),
        message=task.get("message"),
        videoInfo=task.get("videoInfo"),
        progress=task.get("progress")
    )

@app.get("/files/{task_id}.mkv")
async def download_file(task_id: str):
    """Download the converted video file"""
    file_path = DOWNLOADS_DIR / f"{task_id}.mkv"
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found or has been cleaned up")
    
    return FileResponse(
        path=file_path,
        media_type="video/x-matroska",
        filename=f"{task_id}.mkv",
        headers={"Content-Disposition": f"attachment; filename={task_id}.mkv"}
    )

@app.delete("/api/cleanup/{task_id}")
async def cleanup_task(task_id: str):
    """Clean up task and associated files"""
    try:
        # Remove from tasks
        if task_id in tasks:
            del tasks[task_id]
        
        # Remove files
        file_path = DOWNLOADS_DIR / f"{task_id}.mkv"
        if file_path.exists():
            file_path.unlink()
        
        # Remove temp files
        for temp_file in TEMP_DIR.glob(f"{task_id}_temp.*"):
            temp_file.unlink()
        
        return {"message": "Task cleaned up successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")

@app.get("/api/browser-cookies")
async def get_browser_cookies_info():
    """Get information about available browser cookies"""
    try:
        downloader = VideoDownloader()
        
        return {
            "detected_browsers": downloader.detected_browsers,
            "recommendations": [
                "Browser cookie extraction works best with:",
                "1. Chrome/Chromium (most reliable)",
                "2. Firefox (good compatibility)",
                "3. Edge (Windows users)",
                "",
                "To enable browser cookie extraction:",
                "1. Make sure you're logged into YouTube in your browser",
                "2. Close all browser instances before starting downloads",
                "3. The API will automatically extract cookies from your browser",
                "",
                "If browser extraction fails, upload cookies.txt as a fallback."
            ],
            "browser_status": {
                browser: "Available" for browser in downloader.detected_browsers
            } if downloader.detected_browsers else {"none": "No browsers detected"}
        }
    except Exception as e:
        return {
            "error": f"Failed to get browser info: {str(e)}",
            "detected_browsers": [],
            "recommendations": [
                "Browser detection failed - please upload cookies.txt manually"
            ]
        }

@app.get("/api/troubleshoot")
async def get_troubleshoot_info():
    """Get troubleshooting information for debugging YouTube access issues"""
    try:
        import yt_dlp
        from datetime import datetime
        
        # Check yt-dlp version
        yt_dlp_version = yt_dlp.__version__
        
        # Check browser cookies
        downloader = VideoDownloader()
        browser_info = {
            "detected_browsers": downloader.detected_browsers,
            "available": len(downloader.detected_browsers) > 0
        }
        
        # Check and validate cookies file
        cookies_validation = downloader.validate_cookies_file()
        
        # Check ffmpeg availability
        try:
            ffmpeg_result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=10)
            ffmpeg_available = ffmpeg_result.returncode == 0
            ffmpeg_version = ffmpeg_result.stdout.decode().split('\n')[0] if ffmpeg_available else "Not available"
        except:
            ffmpeg_available = False
            ffmpeg_version = "Not available"
        
        # Get current task statistics
        task_stats = {
            "total_tasks": len(tasks),
            "processing_tasks": len([t for t in tasks.values() if t.get("status") == "processing"]),
            "ready_tasks": len([t for t in tasks.values() if t.get("status") == "ready"]),
            "error_tasks": len([t for t in tasks.values() if t.get("status") == "error"]),
        }
        
        # Recent errors
        recent_errors = []
        for task_id, task in tasks.items():
            if task.get("status") == "error":
                recent_errors.append({
                    "task_id": task_id,
                    "message": task.get("message", "Unknown error"),
                    "url": task.get("url", "Unknown")
                })
        
        return {
            "timestamp": datetime.now().isoformat(),
            "yt_dlp_version": yt_dlp_version,
            "browser_cookies": browser_info,
            "file_cookies": cookies_validation,
            "ffmpeg": {
                "available": ffmpeg_available,
                "version": ffmpeg_version
            },
            "task_statistics": task_stats,
            "recent_errors": recent_errors[-5:],  # Last 5 errors
            "recommendations": [
                "🆕 IMPROVED ANTI-BOT BYPASS:",
                "• This version now supports automatic browser cookie extraction",
                "• Browser cookies are tried first, then uploaded cookies.txt",
                "• Supported browsers: Chrome, Firefox, Edge, Safari, Opera",
                "",
                "If you're getting 'Sign in to confirm you're not a bot' errors:",
                "1. Make sure you're logged into YouTube in your browser",
                "2. Close all browser instances before downloading",
                "3. The API will automatically extract cookies from your browser",
                "4. As a fallback, upload cookies.txt from your browser",
                "",
                "If downloads keep failing:",
                "1. Check /api/browser-cookies to see detected browsers",
                "2. Wait 10-15 minutes between failed attempts",
                "3. Try videos from different channels",
                "4. Check if the video is public and accessible",
                "5. Consider using a VPN if region-locked"
            ]
        }
    except Exception as e:
        return {
            "error": f"Failed to get troubleshoot info: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }

async def download_video_task(task_id: str, url: str, rename: Optional[str] = None):
    """Background task to download and convert video with enhanced error handling"""
    downloader = VideoDownloader()
    
    try:
        # Update status: extracting
        tasks[task_id]["progress"] = "extracting"
        tasks[task_id]["message"] = "Extracting video information (trying browser cookies first)..."
        
        # Extract video info with fallback strategies
        try:
            # First try standard extraction
            try:
                video_info = await downloader.extract_info(url)
            except Exception as e:
                if "blocked" in str(e).lower() or "bot" in str(e).lower():
                    # Try fallback strategies
                    tasks[task_id]["message"] = "Standard extraction failed, trying alternative methods..."
                    video_info = await downloader.extract_info_with_fallback(url)
                else:
                    raise e
                    
        except Exception as e:
            error_msg = str(e)
            if "video access blocked" in error_msg.lower() or "bot" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ YouTube is blocking access to this video.\n\n💡 Solutions:\n• Upload a valid cookies.txt file\n• Try again in a few minutes\n• The video may be region-locked or age-restricted"
            elif "access forbidden" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ Access forbidden.\n\n💡 This video may be:\n• Region-locked\n• Private or unlisted\n• Require authentication\n\nTry uploading cookies.txt from a logged-in session."
            elif "video not found" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ Video not found.\n\n💡 Please check:\n• The URL is correct\n• The video hasn't been deleted\n• The video isn't private"
            elif "max retries exceeded" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ Multiple attempts failed.\n\n💡 YouTube is actively blocking requests. Please:\n• Wait 10-15 minutes before trying again\n• Upload fresh cookies.txt\n• Try a different video"
            else:
                tasks[task_id]["message"] = f"❌ Extraction failed: {error_msg}"
            
            tasks[task_id]["status"] = "error"
            raise
        
        if not video_info:
            raise Exception("Could not extract video information")
        
        tasks[task_id]["videoInfo"] = {
            "title": video_info.get("title", "Unknown"),
            "thumbnail": video_info.get("thumbnail", ""),
            "duration": format_duration(video_info.get("duration", 0))
        }
        
        # Update status: downloading
        tasks[task_id]["progress"] = "downloading"
        tasks[task_id]["message"] = f"Downloading: {video_info.get('title', 'Unknown')}"
        
        # Download video
        try:
            temp_file = await downloader.download_video(url, task_id)
        except Exception as e:
            error_msg = str(e)
            if "youtube is blocking" in error_msg.lower() or "bot" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ YouTube is blocking the download.\n\n💡 Solutions:\n• Upload a valid cookies.txt file\n• Wait 10-15 minutes before retrying\n• Try a different video"
            elif "access forbidden" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ Download forbidden.\n\n💡 This video may be:\n• Region-locked\n• Private or require authentication\n• Age-restricted\n\nTry uploading cookies.txt from a logged-in session."
            elif "video not found" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ Video not found during download.\n\n💡 The video may have been:\n• Deleted or made private\n• Moved to a different URL"
            elif "private video" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ This is a private video.\n\n💡 You need to upload cookies.txt from a browser session where you're logged in and have access to this video."
            elif "max retries exceeded" in error_msg.lower():
                tasks[task_id]["message"] = f"❌ Download failed after multiple attempts.\n\n💡 YouTube is actively blocking requests. Please:\n• Wait 15-30 minutes before trying again\n• Upload fresh cookies.txt\n• Check if the video is still available"
            else:
                tasks[task_id]["message"] = f"❌ Download failed: {error_msg}"
            
            tasks[task_id]["status"] = "error"
            raise
        
        if not temp_file or not temp_file.exists():
            raise Exception("Download failed - no file created")
        
        # Update status: converting
        tasks[task_id]["progress"] = "converting"
        tasks[task_id]["message"] = "Converting video to optimized format..."
        
        # Convert to HEVC/H.264
        output_file = DOWNLOADS_DIR / f"{task_id}.mkv"
        try:
            await downloader.convert_to_hevc(temp_file, output_file)
        except Exception as e:
            error_msg = str(e)
            if "hevc encoder" in error_msg.lower():
                tasks[task_id]["message"] = "⚠️ HEVC not available, using H.264 instead..."
                # The downloader will handle fallback automatically
            else:
                tasks[task_id]["message"] = f"❌ Conversion failed: {error_msg}"
                tasks[task_id]["status"] = "error"
                raise
        
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise Exception("Conversion failed - no output file created")
        
        # Clean up temp file
        if temp_file.exists():
            temp_file.unlink()
        
        # Update status: ready
        tasks[task_id]["status"] = "ready"
        tasks[task_id]["progress"] = "ready"
        tasks[task_id]["message"] = "✅ Video ready for download!"
        tasks[task_id]["filename"] = f"{task_id}.mkv"
        
        print(f"Download completed successfully for task {task_id}")
        
    except Exception as e:
        if tasks[task_id]["status"] != "error":
            tasks[task_id]["status"] = "error"
            if not tasks[task_id].get("message", "").startswith("❌"):
                tasks[task_id]["message"] = f"❌ Error: {str(e)}"
        
        print(f"Download error for task {task_id}: {str(e)}")
        
        # Clean up any temp files on error
        try:
            for temp_file in TEMP_DIR.glob(f"{task_id}_temp.*"):
                temp_file.unlink()
        except:
            pass

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
