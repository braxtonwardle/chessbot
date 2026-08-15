@echo off
REM Keeps ChessBot running: if node.exe exits for any reason (crash,
REM the "Connection Failure" reconnect loop giving up, etc.), this
REM waits 5 seconds and starts it again instead of leaving the bot
REM offline until someone notices.
REM
REM To run this automatically on startup/logon:
REM   1. Press Win+R, type: shell:startup
REM   2. Create a shortcut to this file inside that folder
REM   That's Windows' per-user startup folder -- anything shortcut'd
REM   in there launches automatically when you log in.

cd /d "%~dp0"

:loop
echo Starting ChessBot...
node chessbot.mjs
echo ChessBot exited. Restarting in 5 seconds...
timeout /t 5 /nobreak
goto loop
