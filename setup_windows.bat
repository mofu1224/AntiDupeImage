@echo off
setlocal
call "%~dp0AntiDupeImage.bat" --setup-only %*
exit /b %errorlevel%
