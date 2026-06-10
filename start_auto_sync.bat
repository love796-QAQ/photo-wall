@echo off
echo Starting Photo Auto-Sync...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_sync_photos.ps1"
echo.
echo Script has stopped.
pause
