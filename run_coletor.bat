@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM --- Consola UTF-8 (melhor para acentos) ---
chcp 65001 >nul
set "PYTHONUTF8=1"

REM --- Localização do EXE (onedir) ---
set "BASE=%~dp0"
set "EXE=%BASE%legislacao-renovaveis.exe"
if not exist "%EXE%" set "EXE=%BASE%dist\legislacao-renovaveis\legislacao-renovaveis.exe"

if not exist "%EXE%" (
  echo [ERRO] Executavel nao encontrado:
  echo   %EXE%
  echo.
  echo Dica: corre build.ps1 primeiro para gerar o executavel.
  echo.
  pause
  exit /b 2
)

REM --- Defaults ---
set "PROFILE=renovaveis_portarias"
set "DAYS=14"
set "LOG_LEVEL=INFO"
set "OPEN_REPORTS=1"

set "DEBUG_MODE=0"
set "FORCE_FULL=0"
set "RESET_CKPT=0"
set "EXTRA_ARGS="

cls
echo ============================================================
echo  Legislacao Renovaveis - Coletor (Launcher)
echo ============================================================
echo.
echo Escolhe o perfil:
echo   [1] renovaveis_portarias        (keywords ON, types=portaria)
echo   [2] sem_keywords_portarias      (keywords OFF, types=portaria)
echo   [3] renovaveis_todos            (keywords ON, types=ALL)
echo.

set /p "CHOICE=Opcao (1-3) [1]: "
if "%CHOICE%"=="" set "CHOICE=1"

if "%CHOICE%"=="1" set "PROFILE=renovaveis_portarias"
if "%CHOICE%"=="2" set "PROFILE=sem_keywords_portarias"
if "%CHOICE%"=="3" set "PROFILE=renovaveis_todos"

echo.
set /p "DAYS=Days (janela em dias) [%DAYS%]: "
if "%DAYS%"=="" set "DAYS=14"

echo.
set /p "DEBUG_IN=Modo debug? (S/N) [N]: "
if "%DEBUG_IN%"=="" set "DEBUG_IN=N"
if /I "%DEBUG_IN%"=="S" set "DEBUG_MODE=1"
if /I "%DEBUG_IN%"=="Y" set "DEBUG_MODE=1"

echo.
set /p "FORCE_IN=Force full window (reprocessar janela)? (S/N) [N]: "
if "%FORCE_IN%"=="" set "FORCE_IN=N"
if /I "%FORCE_IN%"=="S" set "FORCE_FULL=1"
if /I "%FORCE_IN%"=="Y" set "FORCE_FULL=1"

echo.
set /p "RESET_IN=Reset checkpoint? (S/N) [N]: "
if "%RESET_IN%"=="" set "RESET_IN=N"
if /I "%RESET_IN%"=="S" set "RESET_CKPT=1"
if /I "%RESET_IN%"=="Y" set "RESET_CKPT=1"

REM --- Confirmação forte para reset ---
if "%RESET_CKPT%"=="1" (
  echo.
  echo [AVISO] Vais fazer RESET do checkpoint incremental.
  echo Isto pode fazer com que itens antigos voltem a ser processados.
  echo.
  set "CONFIRM="
  set /p "CONFIRM=Para confirmar, escreve RESET (ou Enter para cancelar): "
  if /I not "%CONFIRM%"=="RESET" (
    echo.
    echo [INFO] Reset cancelado.
    set "RESET_CKPT=0"
  )
)

echo.
set /p "OPEN_IN=Abrir pasta de reports no fim? (S/N) [S]: "
if "%OPEN_IN%"=="" set "OPEN_IN=S"
if /I "%OPEN_IN%"=="S" set "OPEN_REPORTS=1"
if /I "%OPEN_IN%"=="Y" set "OPEN_REPORTS=1"
if /I "%OPEN_IN%"=="N" set "OPEN_REPORTS=0"

echo.
set /p "EXTRA_ARGS=Args extra (opcional, ex: --types portaria,resolucao) []: "

REM --- Flags derivadas ---
set "EXTRA="

REM Profile sem keywords -> garantir flag mesmo que defaults mudem
if /I "%PROFILE%"=="sem_keywords_portarias" set "EXTRA=%EXTRA% --no-keywords"

if "%DEBUG_MODE%"=="1" (
  set "EXTRA=%EXTRA% --debug"
  set "LOG_LEVEL=DEBUG"
)

if "%FORCE_FULL%"=="1" set "EXTRA=%EXTRA% --force-full-window"
if "%RESET_CKPT%"=="1" set "EXTRA=%EXTRA% --reset-checkpoint"

cls
echo ============================================================
echo  A correr...
echo ============================================================
echo EXE:       %EXE%
echo PROFILE:   %PROFILE%
echo DAYS:      %DAYS%
echo LOG_LEVEL: %LOG_LEVEL%
echo.
echo Comando:
echo   "%EXE%" --profile "%PROFILE%" --days %DAYS% --log-level %LOG_LEVEL%%EXTRA% %EXTRA_ARGS%
echo.

"%EXE%" --profile "%PROFILE%" --days %DAYS% --log-level %LOG_LEVEL% %EXTRA% %EXTRA_ARGS%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERRO] O coletor terminou com codigo %RC%.
  echo.
  pause
  exit /b %RC%
)

echo [OK] Terminado com sucesso.

REM Abrir reports
if "%OPEN_REPORTS%"=="1" (
  set "REPORTS=%LOCALAPPDATA%\LegislacaoRenovaveis\data\index\reports"
  if exist "%REPORTS%" (
    echo.
    echo A abrir pasta de relatorios:
    echo   %REPORTS%
    start "" "%REPORTS%"
  ) else (
    echo.
    echo [AVISO] Pasta de reports nao encontrada:
    echo   %REPORTS%
  )
)

echo.
pause
exit /b 0
