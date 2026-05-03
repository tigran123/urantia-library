#!/bin/bash
set -e

# Navigate to the directory where this script is located
cd "$(dirname "$0")"

# Ensure backend dependencies are installed
if [ ! -d "backend/.venv" ]; then
    echo "Setting up Python virtual environment..."
    cd backend
    uv venv
    uv pip install fastapi uvicorn aiofiles
    cd ..
fi

# Ensure frontend is built
if [ ! -d "frontend/dist" ]; then
    echo "Building frontend..."
    cd frontend
    npm install
    npm run build
    cd ..
fi

echo "Starting Urantia Library Web Server..."
cd backend
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --root-path /library
