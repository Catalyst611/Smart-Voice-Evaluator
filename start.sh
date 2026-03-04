#!/bin/bash

echo "========================================"
echo "🚀 正在初始化 [智能语音评测系统 v2.0]"
echo "========================================"

# 1. 强制清理历史残留进程，释放 5000 端口
echo "🧹 1/4 正在清理旧进程与释放端口..."
pkill -9 -f "gunicorn"
pkill -9 -f "python3 app.py"
pkill -9 -f "cpolar"
sleep 2

# 2. 确保进入正确的项目目录
echo "📂 2/4 正在进入项目目录 /root/v2_ai_system..."
cd /root/v2_ai_system

# 3. 启动 Gunicorn 工业级服务器 (4个并发进程)
echo "⚙️ 3/4 正在后台启动 Gunicorn 核心服务..."
nohup gunicorn -w 4 -b 0.0.0.0:5000 app:app > output.log 2>&1 &
sleep 2

echo "========================================"
echo "✅ 系统已成功在后台稳定运行！"
echo "👉 1. 本地日志已输出至 output.log"
echo "👉 2. 请前往 Cpolar 官网 (dashboard.cpolar.com) 查看并复制最新的 HTTPS 网址。"
echo "========================================"
