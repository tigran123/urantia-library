from fastapi import FastAPI, HTTPException, Request, Depends, Cookie, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse, FileResponse, Response
import os
import re
import subprocess
import io
import hashlib
from PIL import Image
from typing import List, Dict, Any
from pathlib import Path
import json
import logging
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
import models
import schemas
from database import engine, get_db
from security import get_password_hash, verify_password, create_access_token, SECRET_KEY, ALGORITHM
import email_utils
import jwt

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def custom_logging_middleware(request: Request, call_next):
    # Determine user email from cookie
    user_email = "Anonymous"
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_email = payload.get("sub", "Anonymous")
        except:
            pass

    # Client IP without port
    client_host = request.client.host if request.client else "unknown"

    response = await call_next(request)

    # Format current date/time
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format and print the log
    url_with_query = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
    print(f"{now} {client_host} - {user_email} - \"{request.method} {url_with_query} HTTP/{request.scope.get('http_version', '1.1')}\" {response.status_code}")

    return response

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

async def get_current_user(access_token: str = Cookie(None), db: Session = Depends(get_db)):
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user

@app.post("/api/login")
async def login(login_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="User is not active")

    access_token = create_access_token(data={"sub": user.email})
    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7*24*60*60
    )
    return response

@app.post("/api/logout")
async def logout():
    response = JSONResponse(content={"message": "Logout successful"})
    response.delete_cookie(key="access_token")
    return response

@app.get("/api/browse")
async def browse(path: str = "", current_user: models.User = Depends(get_current_user)):
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
        if not os.path.exists(entry_path):
            continue
        is_dir = os.path.isdir(entry_path)

        # Check cover
        cover_url = None
        cover_path = os.path.join(target_dir, ".covers", f"{entry}.jpg")
        if os.path.exists(cover_path):
            rel_cover = os.path.relpath(cover_path, BOOKS_DIR)
            cover_url = f"/api/files/{rel_cover.replace(chr(92), '/')}"

        try:
            size = os.path.getsize(entry_path) if not is_dir else 0
            mtime = datetime.fromtimestamp(os.path.getmtime(entry_path)).isoformat()
        except OSError:
            size = 0
            mtime = None

        items.append({
            "name": entry,
            "is_dir": is_dir,
            "description": descriptions.get(entry, ""),
            "cover_url": cover_url,
            "size": size,
            "mtime": mtime,
            "path": os.path.relpath(entry_path, BOOKS_DIR).replace("\\", "/")
        })

    # Sort: folders first, then files
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    return {"path": path, "items": items}

@app.get("/api/search")
async def search(q: str = "", current_user: models.User = Depends(get_current_user)):
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
            if not os.path.exists(entry_path):
                continue
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

@app.post("/api/register", response_model=schemas.Message)
async def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Check if a pending request already exists
    db_request = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.email == user.email).first()
    if db_request:
        raise HTTPException(status_code=400, detail="Registration request already pending")

    db_req = models.RegistrationRequest(
        email=user.email,
        source=user.source,
        purpose=user.purpose,
        status="pending"
    )
    db.add(db_req)
    db.commit()
    db.refresh(db_req)

    # Notify admin
    email_utils.send_admin_notification(
        user_email=db_req.email,
        token=db_req.token,
        source=db_req.source,
        purpose=db_req.purpose
    )

    return {"message": "Registration request queued for approval."}

@app.get("/api/admin/approve")
async def approve_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    if db_req.status == "approved":
        return JSONResponse(status_code=200, content={"message": "User already approved, waiting for password setup."})

    db_req.status = "approved"
    db.commit()

    # Notify user to set password
    email_utils.send_user_approval(db_req.email, db_req.token)

    return JSONResponse(status_code=200, content={"message": "User approved successfully. Email sent to set password."})

@app.post("/api/set-password", response_model=schemas.Message)
async def set_password(data: schemas.UserSetPassword, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == data.token).first()
    if not db_req or db_req.status != "approved":
        raise HTTPException(status_code=400, detail="Invalid or unapproved token.")

    # Create active user
    hashed_password = get_password_hash(data.password)
    new_user = models.User(
        email=db_req.email,
        hashed_password=hashed_password,
        is_active=True
    )
    db.add(new_user)
    db.delete(db_req)
    db.commit()

    return {"message": "Password set successfully. You can now log in."}

@app.get("/api/admin/reject")
async def reject_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    user_email = db_req.email
    db.delete(db_req)
    db.commit()

    # Notify user
    email_utils.send_user_rejection(user_email)

    return JSONResponse(status_code=200, content={"message": "User rejected successfully."})

@app.get("/api/files/{path:path}")
async def get_file(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(file_path).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)

def sanitize_djvu_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR and exists."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not target_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    return target_path

@app.get("/api/djvu-metadata")
async def djvu_metadata(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_djvu_path(path)
    try:
        result = subprocess.run(["djvused", "-e", "n", file_path], capture_output=True, text=True, check=True)
        total_pages = int(result.stdout.strip())
        return {"total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/djvu-page")
async def djvu_page(path: str, page: int, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_djvu_path(path)
    if page < 1:
        raise HTTPException(status_code=400, detail="Invalid page number")

    # Setup cache directory
    cache_dir = os.path.join(BOOKS_DIR, ".cache", "djvu")
    os.makedirs(cache_dir, exist_ok=True)

    # Create unique filename based on the absolute file path and page number
    file_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
    cache_filename = f"{file_hash}_p{page}.webp"
    cache_filepath = os.path.join(cache_dir, cache_filename)

    headers = {"Cache-Control": "public, max-age=86400"}

    # Return cached file if it exists
    if os.path.exists(cache_filepath):
        return FileResponse(cache_filepath, headers=headers, media_type="image/webp")

    try:
        result = subprocess.run(
            ["ddjvu", "-format=pnm", f"-page={page}", file_path],
            capture_output=True,
            check=True
        )

        image_data = io.BytesIO(result.stdout)
        img = Image.open(image_data)

        output_buffer = io.BytesIO()
        img.save(output_buffer, format="WEBP", quality=90, method=6)

        # Save to disk cache
        with open(cache_filepath, "wb") as f:
            f.write(output_buffer.getvalue())

        return FileResponse(cache_filepath, headers=headers, media_type="image/webp")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="Failed to extract page")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# Frontend static files will be mounted here later
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
