@echo off
REM dg - Command Prompt wrapper. Delegates to dg.ps1 so the logic lives in one place.
REM
REM   dg init
REM   dg query "send_file" --mode why

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dg.ps1" %*
exit /b %ERRORLEVEL%
