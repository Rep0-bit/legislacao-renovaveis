$ErrorActionPreference = "Stop"

# Limpar builds anteriores
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\build, .\dist

# Build (onedir)
pyinstaller --noconfirm --clean --onedir `
  --name legislacao-renovaveis `
  --add-data "src\config\profiles.json;src\config" `
  .\main_coletor.py
