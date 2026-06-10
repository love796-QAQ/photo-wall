@echo off
echo Starting Photo Wall application...
echo Main:  http://localhost:8765
echo Admin: http://localhost:8765/admin.html
echo.
python "%~dp0server.py"
pause
