@echo off
echo Starting Photo Auto-Sync with location lookup...
echo New GPS coordinates may be sent to OpenStreetMap Nominatim.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_sync_photos.ps1" -ResolveLocations
echo.
echo Script has stopped.
pause
