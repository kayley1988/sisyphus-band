@echo off
setlocal
set ADB=%~dp0adb_tools\platform-tools\adb.exe
title Guitar Servo Cal - on screen touch mode

echo.
echo  ===============================================
echo  BoYun servo calibration - on the guitar screen
echo  ===============================================
echo.
echo  This will STOP the main program, take over the
echo  240x240 LCD and the touch pads, and RESTORE the
echo  main program automatically when you exit.
echo.
echo  Touch pads 1-6 = pick string 1-6
echo  UP / DOWN      = adjust value
echo  SET tap        = next adjust point / enter
echo  SET hold 1s    = SAVE to csv
echo.
"%ADB%" wait-for-device
"%ADB%" shell sh /root/guitarist_app/servo_cal.sh
echo.
echo  Calibration exited. Main program restored.
pause >nul
endlocal
