#!/bin/bash
# Start all B2B Exception Engine services locally

echo "Starting B2B Exception Engine..."

# Navigate to project dir
cd "$(dirname "$0")"

# Activate virtual environment
source .venv/bin/activate

# 1. Start FastAPI Backend (Port 8000)
echo "Starting FastAPI server..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
PID_API=$!

# 2. Start Celery Worker
echo "Starting Celery worker..."
PYTHONPATH=. celery -A backend.app.tasks.celery_app:celery_app worker --loglevel=info --concurrency=2 &
PID_CELERY=$!

# 3. Start Redis Stream Consumer
echo "Starting Stream Consumer..."
python -m backend.stream_consumer &
PID_CONSUMER=$!

# 4. Start React Frontend (Port 5173)
echo "Starting React Frontend..."
cd frontend && npm run dev &
PID_FRONTEND=$!

echo "========================================================="
echo "All services started!"
echo "API: http://localhost:8000"
echo "Dashboard: http://localhost:5173"
echo "Press Ctrl+C to stop all services."
echo "========================================================="

# Trap Ctrl+C and kill all background processes
trap "echo 'Stopping all services...'; kill $PID_API $PID_CELERY $PID_CONSUMER $PID_FRONTEND; exit" INT TERM

# Keep script running
wait
