#!/bin/bash
set -e
sudo systemctl enable mysql
sudo systemctl start mysql
echo DB_STARTED
sudo mysql -e "CREATE DATABASE IF NOT EXISTS trade_history CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
echo DB_CREATED
sudo mysql -e "CREATE USER IF NOT EXISTS 'app_user'@'localhost' IDENTIFIED BY '#vMYSQLPASS2026!!';"
echo USER_CREATED
sudo mysql -e "GRANT ALL PRIVILEGES ON trade_history.* TO 'app_user'@'localhost'; FLUSH PRIVILEGES;"
echo PRIVILEGES_GRANTED
sudo mysql trade_history < /home/ubuntu/intradayBot/trade_history.sql
echo DUMP_RESTORED
mysql -u app_user -p'#vMYSQLPASS2026!!' trade_history -e 'SHOW TABLES;'
echo TABLES_VERIFIED