\
@echo off
chcp 65001 >nul
REM scripts\run_coletor.bat
REM Launcher "de botão" (CMD) para o coletor.
REM Uso:
REM   scripts\run_coletor.bat renovaveis_todos 1 --dry-run --force-full-window
setlocal enabledelayedexpansion

set REPO=%~dp0..
cd /d "%REPO%"

set PY=%REPO%\.venv\Scripts\python.exe
if not exist "%PY%" (
  echo Nao encontrei o venv em "%PY%". Cria o venv (.venv) primeiro.
  exit /b 1
)

set PROFILE=%1
if "%PROFILE%"=="" set PROFILE=renovaveis_todos

set DAYS=%2
if "%DAYS%"=="" set DAYS=1

shift
shift

echo Repo: %REPO%
echo Python: %PY%
echo Profile: %PROFILE%
echo Days: %DAYS%
echo Extra args: %*

"%PY%" -m src.collectors.coletor_dr_serie1_rss --profile "%PROFILE%" --days %DAYS% %*
exit /b %ERRORLEVEL%
