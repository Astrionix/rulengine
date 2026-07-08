#!/bin/bash
echo "Stopping existing bot processes..."

# Kill anything on port 8000
fuser -k 8000/tcp 2>/dev/null || true
sleep 1

# Kill any remaining uvicorn/python bot processes
pkill -9 -f "uvicorn main:app" 2>/dev/null || true
sleep 3

# Confirm port is free
if fuser 8000/tcp 2>/dev/null; then
    echo "ERROR: Port 8000 still in use. Please kill manually."
    exit 1
fi

cd /home/ubuntu/intradayBot
echo "Starting bot with nohup..."

# Write logs to project directory (not home dir)
nohup /home/ubuntu/intradayBot/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 > /home/ubuntu/intradayBot/bot.log 2>&1 &

sleep 5
echo "=== BOT STATUS ==="
ps aux | grep uvicorn | grep -v grep
echo "=== RECENT LOGS ==="
tail -n 20 /home/ubuntu/intradayBot/bot.log
