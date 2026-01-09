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
  echo [ERRO] Executável não encontrado:
  echo   %EXE%
  echo.
  echo Dica: corre build.ps1 primeiro para gerar o executável.
  echo.
  pause
  exit /b 2
)

REM --- Keywords standard (informativo) ---
REM Nota: isto é apenas informativo no launcher; as keywords reais vivem no core/profiles.
set "KW_INFO=energia renovavel; renovaveis; solar; fotovoltaico; eolico; hidrogenio; biomassa; autoconsumo; UPAC; UPP; rede eletrica; armazenamento; baterias"

REM ============================================================
REM MENU INICIAL
REM ============================================================
:MENU_INICIAL
cls
echo ============================================================
echo  Legislação Renováveis - Coletor
echo ============================================================
echo.
echo Escolha o modo:
echo   [1] Modo simples (recomendado)
echo   [2] Modo avançado (técnico)
echo   [0] Sair
echo.

set "MODO="
set /p "MODO=Opção [1]: "
if "%MODO%"=="" set "MODO=1"

if "%MODO%"=="0" exit /b 0
if "%MODO%"=="1" goto MENU_SIMPLES
if "%MODO%"=="2" goto MENU_AVANCADO

echo.
echo [ERRO] Opção inválida. Use 0, 1 ou 2.
timeout /t 2 >nul
goto MENU_INICIAL


REM ============================================================
REM MODO SIMPLES
REM ============================================================
:MENU_SIMPLES
set "PROFILE=renovaveis_todos"
set "DAYS=14"
set "OPEN_REPORTS=1"
set "LOG_LEVEL=INFO"
set "EXTRA="
set "EXTRA_ARGS="

cls
echo ============================================================
echo  Modo simples
echo ============================================================
echo.
echo Selecione o tipo de rastreio:
echo   [1] Rastreio completo de Energias Renovaveis (Recomendado: todos os tipos; filtro por palavras-chave)
echo   [2] Rastreio apenas por Portarias (filtro por palavras-chave)
echo   [3] Rastreio apenas por Portarias (sem filtro de palavras-chave)
echo   [0] Voltar
echo.
echo Palavras-chave standard (informativo — aplicadas nas opções 1 e 2):
echo   %KW_INFO%
echo.

set "SIMP="
set /p "SIMP=Opção (1-3; 0 voltar) [1]: "
if "%SIMP%"=="" set "SIMP=1"

if "%SIMP%"=="0" goto MENU_INICIAL
if "%SIMP%"=="1" set "PROFILE=renovaveis_todos"
if "%SIMP%"=="2" set "PROFILE=renovaveis_portarias"
if "%SIMP%"=="3" set "PROFILE=sem_keywords_portarias"

REM Validar escolha
if not "%SIMP%"=="1" if not "%SIMP%"=="2" if not "%SIMP%"=="3" (
  echo.
  echo [ERRO] Opção inválida. Use 0, 1, 2 ou 3.
  pause
  goto MENU_SIMPLES
)

echo.
set /p "DAYS=Período a rastrear (dias) [%DAYS%]: "
if "%DAYS%"=="" set "DAYS=14"

call :VALIDATE_DAYS "%DAYS%"
if errorlevel 1 (
  pause
  goto MENU_SIMPLES
)

echo.
set "OPEN_IN="
set /p "OPEN_IN=Deseja consultar o relatório final? (S/N) [S]: "
if "%OPEN_IN%"=="" set "OPEN_IN=S"
if /I "%OPEN_IN%"=="N" set "OPEN_REPORTS=0"
if /I "%OPEN_IN%"=="S" set "OPEN_REPORTS=1"
if /I "%OPEN_IN%"=="Y" set "OPEN_REPORTS=1"

REM Garantir --no-keywords quando o perfil for sem_keywords_portarias
if /I "%PROFILE%"=="sem_keywords_portarias" set "EXTRA=%EXTRA% --no-keywords"

goto RUN


REM ============================================================
REM MODO AVANÇADO (técnico)
REM ============================================================
:MENU_AVANCADO
set "PROFILE=renovaveis_portarias"
set "DAYS=14"
set "OPEN_REPORTS=1"
set "LOG_LEVEL=INFO"

set "DEBUG_MODE=0"
set "FORCE_FULL=0"
set "RESET_CKPT=0"
set "EXTRA_ARGS="
set "EXTRA="

cls
echo ============================================================
echo  Modo avançado (técnico)
echo ============================================================
echo.
echo Perfis:
echo   [1] renovaveis_portarias
echo   [2] sem_keywords_portarias
echo   [3] renovaveis_todos
echo   [0] Voltar
echo.

set "ADV="
set /p "ADV=Opção [1]: "
if "%ADV%"=="" set "ADV=1"

if "%ADV%"=="0" goto MENU_INICIAL
if "%ADV%"=="1" set "PROFILE=renovaveis_portarias"
if "%ADV%"=="2" set "PROFILE=sem_keywords_portarias"
if "%ADV%"=="3" set "PROFILE=renovaveis_todos"

REM Validar escolha
if not "%ADV%"=="1" if not "%ADV%"=="2" if not "%ADV%"=="3" (
  echo.
  echo [ERRO] Opção inválida. Use 0, 1, 2 ou 3.
  pause
  goto MENU_AVANCADO
)

echo.
set /p "DAYS=Days (janela em dias) [%DAYS%]: "
if "%DAYS%"=="" set "DAYS=14"

call :VALIDATE_DAYS "%DAYS%"
if errorlevel 1 (
  pause
  goto MENU_AVANCADO
)

echo.
set "DEBUG_IN="
set /p "DEBUG_IN=Modo debug? (S/N) [N]: "
if "%DEBUG_IN%"=="" set "DEBUG_IN=N"
if /I "%DEBUG_IN%"=="S" set "DEBUG_MODE=1"
if /I "%DEBUG_IN%"=="Y" set "DEBUG_MODE=1"

echo.
set "FORCE_IN="
set /p "FORCE_IN=Force full window (reprocessar janela)? (S/N) [N]: "
if "%FORCE_IN%"=="" set "FORCE_IN=N"
if /I "%FORCE_IN%"=="S" set "FORCE_FULL=1"
if /I "%FORCE_IN%"=="Y" set "FORCE_FULL=1"

echo.
set "RESET_IN="
set /p "RESET_IN=Reset checkpoint? (S/N) [N]: "
if "%RESET_IN%"=="" set "RESET_IN=N"
if /I "%RESET_IN%"=="S" set "RESET_CKPT=1"
if /I "%RESET_IN%"=="Y" set "RESET_CKPT=1"

REM Confirmação forte para reset
if "%RESET_CKPT%"=="1" (
  echo.
  echo [AVISO] Vai fazer RESET do checkpoint incremental.
  echo Isto pode fazer com que itens antigos voltem a ser processados.
  echo.
  set "CONFIRM="
  set /p "CONFIRM=Para confirmar, escreva RESET (Enter cancela): "
  if /I not "%CONFIRM%"=="RESET" (
    echo.
    echo [INFO] Reset cancelado.
    set "RESET_CKPT=0"
  )
)

echo.
set "OPEN_IN="
set /p "OPEN_IN=Abrir pasta de reports no fim? (S/N) [S]: "
if "%OPEN_IN%"=="" set "OPEN_IN=S"
if /I "%OPEN_IN%"=="N" set "OPEN_REPORTS=0"
if /I "%OPEN_IN%"=="S" set "OPEN_REPORTS=1"
if /I "%OPEN_IN%"=="Y" set "OPEN_REPORTS=1"

echo.
set /p "EXTRA_ARGS=Args extra (opcional) []: "

REM flags derivadas
if /I "%PROFILE%"=="sem_keywords_portarias" set "EXTRA=%EXTRA% --no-keywords"
if "%DEBUG_MODE%"=="1" (
  set "EXTRA=%EXTRA% --debug"
  set "LOG_LEVEL=DEBUG"
)
if "%FORCE_FULL%"=="1" set "EXTRA=%EXTRA% --force-full-window"
if "%RESET_CKPT%"=="1" set "EXTRA=%EXTRA% --reset-checkpoint"

goto RUN


REM ============================================================
REM RUN
REM ============================================================
:RUN
cls
echo ============================================================
echo  A executar...
echo ============================================================
echo Executável: %EXE%
echo Perfil:     %PROFILE%
echo Período:    %DAYS% dias
echo.
echo Comando:
echo   "%EXE%" --profile "%PROFILE%" --days %DAYS% --log-level %LOG_LEVEL% %EXTRA% %EXTRA_ARGS%
echo.

"%EXE%" --profile "%PROFILE%" --days %DAYS% --log-level %LOG_LEVEL% %EXTRA% %EXTRA_ARGS%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERRO] Terminou com código %RC%.
  echo.
  pause
  exit /b %RC%
)

echo [OK] Terminado com sucesso.

if "%OPEN_REPORTS%"=="1" (
  set "REPORTS=%LOCALAPPDATA%\LegislacaoRenovaveis\data\index\reports"
  if exist "%REPORTS%" (
    echo.
    echo A abrir pasta de relatórios:
    echo   %REPORTS%
    start "" "%REPORTS%"
  ) else (
    echo.
    echo [AVISO] Pasta de relatórios não encontrada:
    echo   %REPORTS%
  )
)

echo.
pause
exit /b 0


REM ============================================================
REM FUNÇÕES AUXILIARES
REM ============================================================
:VALIDATE_DAYS
REM Valida se %1 é inteiro 1..3650
set "D=%~1"
echo %D%| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo.
  echo [ERRO] O período tem de ser um número inteiro.
  exit /b 1
)
if %D% LSS 1 (
  echo.
  echo [ERRO] O período mínimo é 1 dia.
  exit /b 1
)
if %D% GTR 3650 (
  echo.
  echo [ERRO] O período máximo recomendado é 3650 dias.
  exit /b 1
)
exit /b 0
