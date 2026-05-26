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

    # Load NVM into the systemd execution environment so npm is available
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

    # Reroute npm cache to the writable PrivateTmp to bypass ProtectHome=read-only
    export npm_config_cache=/tmp/.npm

    npm ci
    npm run build
    cd ..
fi

echo "Starting Urantia Library Web Server..."
cd backend
. .venv/bin/activate

# Pass --root-path only when APP_ROOT_PATH names a real prefix. Both empty
# and "/" mean "no prefix" — but passing --root-path / makes uvicorn prepend
# "/" to scope.path, so request.url.path came back as "//api/..." on dev.
# Treat both as no-op so secrets.env can spell it either way.
ROOT_PATH_ARGS=()
if [ -n "$APP_ROOT_PATH" ] && [ "$APP_ROOT_PATH" != "/" ]; then
    ROOT_PATH_ARGS=(--root-path "$APP_ROOT_PATH")
fi

# Launch based on explicit environment state
if [ "$APP_ENV" = "development" ]; then
    echo "INFO: Development environment detected. Enabling hot-reload."
    exec uvicorn main:app --host 127.0.0.1 --port 8000 "${ROOT_PATH_ARGS[@]}" --no-access-log --reload
else
    echo "INFO: Production environment detected. Running optimized server."
    exec uvicorn main:app --host 127.0.0.1 --port 8000 "${ROOT_PATH_ARGS[@]}" --no-access-log
fi
