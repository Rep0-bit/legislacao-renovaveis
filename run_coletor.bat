@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM Legislacao Renovaveis - Launcher (coletor)
REM - corre o executável do PyInstaller (onedir)
REM - cria/usa %LOCALAPPDATA%\LegislacaoRenovaveis\...
REM ============================================================

REM Caminho do exe (assume build onedir)
set "EXE=%~dp0dist\legislacao-renovaveis\legislacao-renovaveis.exe"

REM Defaults (podes ajustar)
set "DAYS=14"
set "LOG_LEVEL=INFO"

REM Se o exe não existir, mostra ajuda
if not exist "%EXE%" (
  echo [ERRO] Executavel nao encontrado:
  echo   %EXE%
  echo.
  echo Dica: corre build.ps1 primeiro para gerar o executavel.
  echo.
  pause
  exit /b 2
)

echo [OK] A correr coletor...
echo.

"%EXE%" --profile renovaveis_portarias --log-level %LOG_LEVEL%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERRO] O coletor terminou com codigo %RC%.
  echo.
  pause
  exit /b %RC%
)

echo [OK] Terminado com sucesso.
echo.

REM Abre a pasta dos reports (se existir)
set "REPORTS=%LOCALAPPDATA%\LegislacaoRenovaveis\data\index\reports"
if exist "%REPORTS%" (
  echo A abrir pasta de relatorios:
  echo   %REPORTS%
  start "" "%REPORTS%"
)

echo.
pause
exit /b 0
