import os
import uuid
import asyncio
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
        
        return {
            "status": "healthy",
            "downloads_dir": str(DOWNLOADS_DIR.absolute()),
            "temp_dir": str(TEMP_DIR.absolute()),
            "downloads_writable": downloads_writable,
            "temp_writable": temp_writable,
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
        
        return {"message": "Cookies uploaded successfully"}
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
            "youtube.com/v/"
        ]
        
        if not any(pattern in request.url for pattern in youtube_patterns):
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")
        
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
        raise HTTPException(status_code=404, detail="File not found")
    
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

async def download_video_task(task_id: str, url: str, rename: Optional[str] = None):
    """Background task to download and convert video"""
    downloader = VideoDownloader()
    
    try:
        # Update status: extracting
        tasks[task_id]["progress"] = "extracting"
        tasks[task_id]["message"] = "Extracting video information..."
        
        # Extract video info
        video_info = await downloader.extract_info(url)
        
        if not video_info:
            raise Exception("Could not extract video information")
        
        tasks[task_id]["videoInfo"] = {
            "title": video_info.get("title", "Unknown"),
            "thumbnail": video_info.get("thumbnail", ""),
            "duration": format_duration(video_info.get("duration", 0))
        }
        
        # Update status: downloading
        tasks[task_id]["progress"] = "downloading"
        tasks[task_id]["message"] = "Downloading video..."
        
        # Download video
        temp_file = await downloader.download_video(url, task_id)
        
        if not temp_file or not temp_file.exists():
            raise Exception("Download failed - no file created")
        
        # Update status: converting
        tasks[task_id]["progress"] = "converting"
        tasks[task_id]["message"] = "Converting to HEVC..."
        
        # Convert to HEVC
        output_file = DOWNLOADS_DIR / f"{task_id}.mkv"
        await downloader.convert_to_hevc(temp_file, output_file)
        
        if not output_file.exists():
            raise Exception("Conversion failed - no output file created")
        
        # Clean up temp file
        if temp_file.exists():
            temp_file.unlink()
        
        # Update status: ready
        tasks[task_id]["status"] = "ready"
        tasks[task_id]["progress"] = "ready"
        tasks[task_id]["message"] = "Video ready for download!"
        tasks[task_id]["filename"] = f"{task_id}.mkv"
        
    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["message"] = f"Error: {str(e)}"
        print(f"Download error for task {task_id}: {str(e)}")
        
        # Clean up any temp files on error
        try:
            for temp_file in TEMP_DIR.glob(f"{task_id}_temp.*"):
                temp_file.unlink()
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)