#!/bin/bash
set -e

# Navigate to the directory where this script is located
cd "$(dirname "$0")"

# Ensure backend dependencies are installed
if [ ! -d "backend/.venv" ]; then
    echo "Setting up Python virtual environment..."
    cd backend
    uv venv
    uv pip sync requirements.txt
    cd ..
fi

# Ensure frontend is built
if [ ! -d "frontend/dist" ]; then
    echo "Building frontend..."
    cd frontend
    npm ci
    npm run build
    cd ..
fi

echo "Starting Urantia Library Web Server..."
cd backend
. .venv/bin/activate
exec uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log --reload
