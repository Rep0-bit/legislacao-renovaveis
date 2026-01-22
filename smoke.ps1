# smoke.ps1
# Smoke test end-to-end para legislacao-renovaveis
# Objetivo:
#  - garantir que o pipeline corre
#  - garantir que o export LLM corre
#  - validar que pelo menos um TXT é legível (se existir)
#  - NUNCA falhar só porque não houve resultados numa categoria

$ErrorActionPreference = "Stop"

Write-Host "== SMOKE TEST: legislacao-renovaveis =="

# -------------------------------------------------------------------
# 1) Testes unitários (via python -m pytest para evitar problemas de PATH)
# -------------------------------------------------------------------
Write-Host "`n[1/4] A correr testes unitários..."
python -m pytest -q

# -------------------------------------------------------------------
# 2) Coleta (janela curta, forçada)
# -------------------------------------------------------------------
Write-Host "`n[2/4] A correr coleta RSS..."
python -m src.collectors.coletor_dr_serie1_rss `
    --days 2 `
    --force-full-window `
    --reset-checkpoint `
    --log-level INFO

# -------------------------------------------------------------------
# 3) Export LLM (presets principais)
# -------------------------------------------------------------------
Write-Host "`n[3/4] A exportar para LLM..."

python -m src.export_llm --preset decretos
python -m src.export_llm --preset portarias

# -------------------------------------------------------------------
# 4) Validação mínima dos outputs
# -------------------------------------------------------------------
Write-Host "`n[4/4] A validar outputs..."

$txtFiles = Get-ChildItem .\data\llm_*\text\*.txt -ErrorAction SilentlyContinue

if ($txtFiles -and $txtFiles.Count -gt 0) {
    $txt1 = $txtFiles | Select-Object -First 1
    Write-Host "`n--- Amostra do TXT exportado ($($txt1.FullName)) ---"
    Get-Content $txt1.FullName -Encoding UTF8 -TotalCount 5
}
else {
    Write-Host "Nenhum ficheiro TXT gerado nesta execução (0 resultados filtrados) — OK"
}

Write-Host "`nSMOKE OK ✅"
