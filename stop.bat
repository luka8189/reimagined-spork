@echo off
echo 正在停止员工管理系统服务...
taskkill /F /IM python.exe /T
echo 服务已停止
pause 