from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os
import re
from typing import List, Dict, Any
from pathlib import Path
import json
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def strip_charset_for_websites(request: Request, call_next):
    response = await call_next(request)
    if "/Websites/" in request.url.path:
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and "charset=utf-8" in content_type.lower():
            # Remove the forced utf-8 charset so the browser relies on the <meta> tag inside the file
            # By deleting it and recreating, we ensure Starlette's MutableHeaders handles it properly
            del response.headers["content-type"]
            response.headers["content-type"] = "text/html"
    return response

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")

# Regex to parse .htaccess AddDescription
DESCRIPTION_REGEX = re.compile(r'^AddDescription\s+"(.*?)"\s+(.*)$')

def get_htaccess_descriptions(dir_path: str) -> Dict[str, str]:
    descriptions = {}
    htaccess_path = os.path.join(dir_path, ".htaccess")
    if os.path.exists(htaccess_path):
        try:
            with open(htaccess_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    match = DESCRIPTION_REGEX.match(line.strip())
                    if match:
                        desc, filename = match.groups()
                        # Some filenames might have quotes around them, or just be literal.
                        filename = filename.strip('"')
                        descriptions[filename] = desc
        except Exception as e:
            print(f"Error reading {htaccess_path}: {e}")
    return descriptions

@app.get("/api/browse")
async def browse(path: str = ""):
    target_dir = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    descriptions = get_htaccess_descriptions(target_dir)
    items = []
    
    try:
        entries = sorted(os.listdir(target_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    for entry in entries:
        if entry in [".htaccess", "000-browse.php", "header.html", "footer.html", "exclude.txt", "md5sums.txt", "tree-index.html", ".covers", "webapp"]:
            continue
        if entry.startswith(".authors") or entry == "urantia-library":
            if not path: # Top level exclusions
                continue

        entry_path = os.path.join(target_dir, entry)
        is_dir = os.path.isdir(entry_path)
        
        # Check cover
        cover_url = None
        cover_path = os.path.join(target_dir, ".covers", f"{entry}.jpg")
        if os.path.exists(cover_path):
            rel_cover = os.path.relpath(cover_path, BOOKS_DIR)
            cover_url = f"/api/files/{rel_cover.replace(chr(92), '/')}"
            
        try:
            size = os.path.getsize(entry_path) if not is_dir else 0
        except OSError:
            size = 0
            
        items.append({
            "name": entry,
            "is_dir": is_dir,
            "description": descriptions.get(entry, ""),
            "cover_url": cover_url,
            "size": size,
            "path": os.path.relpath(entry_path, BOOKS_DIR).replace("\\", "/")
        })
        
    # Sort: folders first, then files
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    
    return {"path": path, "items": items}

@app.get("/api/search")
async def search(q: str = ""):
    if not q:
        return {"matches": []}
        
    matches = []
    query_lower = q.lower()
    
    # Simple recursive search
    for root, dirs, files in os.walk(BOOKS_DIR):
        # Exclude hidden and special dirs
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['urantia-library', 'Incoming', 'Html-Docs']]
        
        rel_root = os.path.relpath(root, BOOKS_DIR)
        if rel_root == ".":
            rel_root = ""
            
        descriptions = get_htaccess_descriptions(root)
        
        for entry in dirs + files:
            if entry in [".htaccess", "000-browse.php", "header.html", "exclude.txt"]:
                continue
                
            entry_path = os.path.join(root, entry)
            rel_path = os.path.relpath(entry_path, BOOKS_DIR).replace("\\", "/")
            desc = descriptions.get(entry, "")
            
            if query_lower in entry.lower() or query_lower in desc.lower():
                is_dir = os.path.isdir(entry_path)
                cover_path = os.path.join(root, ".covers", f"{entry}.jpg")
                cover_url = None
                if os.path.exists(cover_path):
                    cover_url = f"/api/files/{os.path.relpath(cover_path, BOOKS_DIR).replace(chr(92), '/')}"
                    
                matches.append({
                    "name": entry,
                    "is_dir": is_dir,
                    "description": desc,
                    "path": rel_path,
                    "parent_dir": rel_root.replace("\\", "/"),
                    "cover_url": cover_url
                })
                
                if len(matches) > 100: # Limit results
                    break
        if len(matches) > 100:
            break
            
    return {"matches": matches}

# Mount static files
app.mount("/api/files", StaticFiles(directory=BOOKS_DIR, follow_symlink=True, html=True), name="books")

# Frontend static files will be mounted here later
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
