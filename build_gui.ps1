$ErrorActionPreference = "Stop"

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\build, .\dist

pyinstaller --noconfirm --clean .\legislacao-renovaveis-user.spec

