 echo off
echo 正在启动员工管理系统...
echo 请使用浏览器访问: http://192.168.0.25:5000

:: 检查是否已安装所需的包
python -m pip install flask flask-sqlalchemy flask-migrate pandas psutil werkzeug

:: 启动应用
python app.py

pause