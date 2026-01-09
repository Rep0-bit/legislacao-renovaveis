# scripts/run_coletor.ps1
# Launcher "de botão" para o coletor DR Série I.
#
# - Ativa o venv local (.venv)
# - Permite escolher um profile (default: renovaveis_todos)
# - Encaminha logs para a consola
[CmdletBinding()]
param(
  [string]$Profile = "renovaveis_todos",
  [int]$Days = 1,
  [switch]$DryRun,
  [switch]$ForceFullWindow,
  [string]$LogLevel = "",
  [string]$Types = "",
  [string]$ExcludeTypes = "",
  [switch]$StrictTypes
)

# Forçar encoding UTF-8 na consola (evita caracteres estranhos em emojis)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8


$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  throw "Não encontrei o Python do venv em: $venvPy. Cria o venv (.venv) primeiro."
}

$args = @("-m", "src.collectors.coletor_dr_serie1_rss", "--profile", $Profile, "--days", $Days.ToString())
if ($DryRun) { $args += "--dry-run" }
if ($ForceFullWindow) { $args += "--force-full-window" }
if ($LogLevel -and $LogLevel.Trim() -ne "") { $args += @("--log-level", $LogLevel) }
if ($Types -and $Types.Trim() -ne "") { $args += @("--types", $Types) }
if ($ExcludeTypes -and $ExcludeTypes.Trim() -ne "") { $args += @("--exclude-types", $ExcludeTypes) }
if ($StrictTypes) { $args += "--strict-types" }

Write-Host "->  Repo: $repoRoot"
Write-Host "->  Python: $venvPy"
Write-Host "->  Args: $($args -join ' ')"
& $venvPy @args
$code = $LASTEXITCODE
Write-Host "`nExitCode=$code"
exit $code
