@echo off
setlocal
set ADB=%~dp0adb_tools\platform-tools\adb.exe
title Guitar Servo Panel

echo.
echo  ===============================================
echo   BoYun Guitar Servo Panel  -  one click launcher
echo  ===============================================
echo.
echo  [1/4] starting adb server ...
"%ADB%" start-server >nul 2>nul

echo  [2/4] waiting for board (USB) ...
"%ADB%" wait-for-device

echo  [3/4] forwarding tcp:5002 -^> board:5002 ...
"%ADB%" forward tcp:5002 tcp:5002 >nul

echo  [4/4] making sure panel service is running on board ...
"%ADB%" shell sh /root/guitarist_app/start_panel.sh 5002

echo.
echo  opening  http://127.0.0.1:5002/
start "" "http://127.0.0.1:5002/"

echo.
echo  Keep this window open while you calibrate.
echo  Press any key to close.
pause >nul
endlocal
