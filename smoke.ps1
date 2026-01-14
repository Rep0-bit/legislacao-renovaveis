$ErrorActionPreference = "Stop"

pytest -q

python -m src.core.pipeline collect --days 2 --force-full-window --log-level INFO

python -m src.export_llm --tipo decreto-lei --out data\llm_decretos --no-incremental
python -m src.export_llm --tipo portaria --out data\llm_portarias --no-incremental

# valida outputs
$txt1 = Get-ChildItem .\data\llm_decretos\text\*.txt -ErrorAction Stop | Select-Object -First 1
$json1 = Get-ChildItem .\data\llm_decretos\meta\*.json -ErrorAction Stop | Select-Object -First 1

Get-Content $txt1.FullName -Encoding UTF8 -TotalCount 5
Get-Content $json1.FullName -Encoding UTF8 -TotalCount 25

"SMOKE OK ✅"

