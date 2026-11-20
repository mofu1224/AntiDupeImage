@echo off
setlocal
call "%~dp0AntiDupeImage.bat" %*
exit /b %errorlevel%
