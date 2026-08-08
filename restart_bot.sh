#!/bin/bash
echo "Restarting the IntradayBot service using systemd..."
sudo systemctl restart intradaybot.service

echo "=== BOT STATUS ==="
sudo systemctl status intradaybot.service --no-pager | head -n 10

echo "=== RECENT LOGS ==="
tail -n 20 /home/ubuntu/intradayBot/bot.log