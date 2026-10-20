@echo off
setlocal enabledelayedexpansion
set ADB=%~dp0adb_tools\platform-tools\adb.exe
set HOSTDIR=/root/guitarist_app
title BoYun - deploy servo panel (hardened)

echo.
echo  ============================================================
echo   Deploy hardened servo_panel to board   (2026-10-12)
echo   - bind 127.0.0.1 by default
echo   - atomic csv write (.tmp -^> .bak -^> replace)
echo   - refuse drive while main program is playing
echo   - press/zero base = 90 (hardware zero), NOT the home chord angle
echo  ============================================================
echo.

if not exist "%ADB%" (
  echo [X] adb not found: %ADB%
  goto :end
)

rem ---------- [0/7] local regression tests (PC only, no hardware) ----------
echo  [0/7] local regression tests ...
set "PYTHONPATH="
set PYEXE=
for %%p in (python.exe) do if not defined PYEXE if not "%%~$PATH:p"=="" set PYEXE=%%~$PATH:p
if not defined PYEXE if exist "C:/Users/zhouqian/.workbuddy/binaries/python/versions/3.13.12/python.exe" set PYEXE=C:/Users/zhouqian/.workbuddy/binaries/python/versions/3.13.12/python.exe
if not defined PYEXE (
  echo        [W] python not found - SKIPPING local regression
  goto :after_tests
)
"%PYEXE%" "%~dp0_test_press_formula_parity.py"
set RC=!ERRORLEVEL!
if "!RC!"=="1" (
  echo        [X] parity test FAILED - front/back press formula disagree. ABORT.
  goto :end
)
if "!RC!"=="3" echo        [W] parity test SKIPPED ^(node not found^) - continuing
if "!RC!"=="0" echo        [OK] parity test passed
"%PYEXE%" "%~dp0_test_panel_hardening.py"
set RC=!ERRORLEVEL!
if not "!RC!"=="0" (
  echo        [X] hardening test FAILED - ABORT.
  goto :end
)
echo        [OK] hardening test passed
:after_tests

echo  [1/7] starting adb server ...
"%ADB%" start-server >nul 2>nul

echo  [2/7] waiting for board (USB) ...
"%ADB%" wait-for-device
for /f "delims=" %%i in ('"%ADB%" shell date +%%Y%%m%%d_%%H%%M%%S') do set TS=%%i
echo        board time: !TS!

echo  [3/7] backing up current panel file on board ...
"%ADB%" shell "cp -a %HOSTDIR%/servo_panel.py %HOSTDIR%/servo_panel.py.bak_!TS! 2>/dev/null; cp -a %HOSTDIR%/servo_panel.html %HOSTDIR%/servo_panel.html.bak_!TS! 2>/dev/null; ls -1 %HOSTDIR%/servo_panel.py.bak_!TS!"

echo  [4/7] pushing servo_panel.py + servo_panel.html ...
"%ADB%" push "%~dp0servo_panel.py"   %HOSTDIR%/servo_panel.py
"%ADB%" push "%~dp0servo_panel.html" %HOSTDIR%/servo_panel.html

echo  [5/7] syntax check on board ...
"%ADB%" shell "python3 -m py_compile %HOSTDIR%/servo_panel.py && echo PY_OK"
if errorlevel 1 (
  echo [X] py_compile FAILED on board - rolling back ...
  "%ADB%" shell "cp -a %HOSTDIR%/servo_panel.py.bak_!TS! %HOSTDIR%/servo_panel.py; cp -a %HOSTDIR%/servo_panel.html.bak_!TS! %HOSTDIR%/servo_panel.html"
  goto :end
)

echo  [6/7] restarting panel service ...
"%ADB%" shell "systemctl restart servo-panel; sleep 1.5; systemctl is-active servo-panel"
"%ADB%" forward tcp:5002 tcp:5002 >nul 2>nul

echo  [7/7] smoke test (PC side via adb forward) ...
curl -s --noproxy "*" -m 4 -o nul -w "       GET /            -> HTTP %%{http_code}\n" http://127.0.0.1:5002/
curl -s --noproxy "*" -m 4 -o nul -w "       GET /api/cfg     -> HTTP %%{http_code}\n" http://127.0.0.1:5002/api/cfg
curl -s --noproxy "*" -m 4 -w "       GET /api/move    -> %%{http_code} " http://127.0.0.1:5002/api/move?tag=PWM0^&ang=90
echo.
echo.
echo  Done. Open http://127.0.0.1:5002/ in your browser.
echo  (Panel now listens on 127.0.0.1 only; nothing on the LAN can reach it.)
echo.
echo  NOTE: the panel's zero/press base is now the hardware zero (90), NOT the
echo        home chord angle. Firmware and player both use 90 + null_offset, so
echo        the old panel was systematically off by (home_chord_angle - 90).
echo        Your home_chord_angle is 89 -^> re-check the zero calibration once
echo        (drag the zero slider until the string is level again).
echo.

:end
pause >nul
endlocal
