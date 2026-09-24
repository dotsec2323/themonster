@echo off
REM ============================================================
REM  Builds CanFallingDetector.exe using PyInstaller.
REM  Run this ON WINDOWS, from a normal Command Prompt, after you
REM  have installed the requirements (see README.md / requirements.txt).
REM
REM  Usage:
REM     cd CanFallingDetector\installer
REM     build_exe.bat
REM
REM  Output goes to: CanFallingDetector\installer\dist\CanFallingDetector\
REM ============================================================

pip install pyinstaller

pyinstaller ^
  --name CanFallingDetector ^
  --noconfirm ^
  --windowed ^
  --add-data "..\models;models" ^
  --add-data "..\config;config" ^
  --collect-all ultralytics ^
  --collect-all torch ^
  --collect-all cv2 ^
  "..\app\main.py"

echo.
echo Build complete. Find CanFallingDetector.exe in:
echo   installer\dist\CanFallingDetector\CanFallingDetector.exe
echo.
echo Copy the whole "CanFallingDetector" folder (not just the .exe) to the
echo production PC - it contains the required DLLs and the bundled model.
pause
