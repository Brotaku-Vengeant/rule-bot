@echo off
rem Double-click to start the bot. Closing this window stops the bot.
cd /d "%~dp0"
title Amtgard Rule Bot
.venv\Scripts\python.exe -m bot.main
rem Only hold the window open if the bot exited with an error, so a normal
rem stop (or an external kill) doesn't leave an empty console behind.
if errorlevel 1 (
    echo.
    echo Bot exited with an error - see the messages above.
    pause
)
