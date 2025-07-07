import re
from pathlib import Path

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file system usage"""
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')
    # Limit length
    if len(filename) > 200:
        filename = filename[:200]
    return filename

def format_duration(seconds: int) -> str:
    """Format duration in seconds to HH:MM:SS or MM:SS"""
    if not seconds:
        return "Unknown"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"

def ensure_mkv_extension(filename: str) -> str:
    """Ensure filename has .mkv extension"""
    if not filename.lower().endswith('.mkv'):
        # Remove existing extension and add .mkv
        filename = Path(filename).stem + '.mkv'
    return filename