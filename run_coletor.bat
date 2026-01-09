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
set /p "OPEN_REPORTS=Abrir pasta de reports no fim? (S/N) [S]: "
if /I "%OPEN_REPORTS%"=="" set "OPEN_REPORTS=S"

REM Normalizar OPEN_REPORTS para 1/0
if /I "%OPEN_REPORTS%"=="S" set "OPEN_REPORTS=1"
if /I "%OPEN_REPORTS%"=="Y" set "OPEN_REPORTS=1"
if /I "%OPEN_REPORTS%"=="N" set "OPEN_REPORTS=0"

REM Se for sem_keywords_portarias, adicionar --no-keywords
set "EXTRA="
if /I "%PROFILE%"=="sem_keywords_portarias" set "EXTRA=--no-keywords"

cls
echo ============================================================
echo  A correr...
echo ============================================================
echo EXE:     %EXE%
echo PROFILE: %PROFILE%
echo DAYS:    %DAYS%
echo.
echo Comando:
echo   "%EXE%" --profile "%PROFILE%" --days %DAYS% --log-level %LOG_LEVEL% %EXTRA%
echo.

"%EXE%" --profile "%PROFILE%" --days %DAYS% --log-level %LOG_LEVEL% %EXTRA%
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
