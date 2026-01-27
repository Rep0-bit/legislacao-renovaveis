# Patch — GUI User Final (Janela 7/30/90 + Mensagens amigáveis + ONEFILE)

Este patch atualiza a GUI "user final" e prepara o build para gerar **um único ficheiro EXE (onefile)**.

## Ficheiros incluídos (para copiar/substituir no teu repositório)
- `src/gui_user/app.py`
  - Adiciona dropdown para janela temporal (7/30/90 dias)
  - Botões:
    - **Atualizar base** (incremental)
    - **Reprocessar janela** (ignora checkpoint: `force_full_window=True`)
    - **Gerar pacote para LLM**
  - Mensagem amigável quando não há novidades (não é erro)
- `legislacao-renovaveis-user.spec`
  - Passa a apontar para `src/gui_user/__main__.py`
  - Configurado para **ONEFILE**
  - Nome do executável: `Megajoule_Legislacao.exe`
  - Ícone: `assets\megajoule.ico`
- `build_user_gui.ps1`
  - Gera o EXE a partir do `legislacao-renovaveis-user.spec`

## Como aplicar (Windows / Visual Studio)
1) Extrai este ZIP para uma pasta temporária.
2) No teu repositório, **substitui** os ficheiros pelos do patch, mantendo os caminhos.

## Testar em modo DEV (antes de build)
Com venv ativo:
```powershell
python -m src.gui_user
```
Confirma:
- abre a janela
- dropdown 7/30/90 funciona
- "Atualizar base" e "Reprocessar" correm e escrevem no log
- "Gerar pacote para LLM" cria `llm_renovaveis` em `get_data_dir()`

## Build ONEFILE (EXE único)
```powershell
powershell -ExecutionPolicy Bypass -File .\build_user_gui.ps1
```

Resultado esperado:
- `dist\Megajoule_Legislacao.exe`

Nota: o onefile pode demorar um pouco mais a abrir (extração temporária).

## Commit recomendado
Depois de validares DEV + EXE:
```powershell
git add src/gui_user/app.py legislacao-renovaveis-user.spec
git commit -m "GUI(user): janela 7/30/90 + mensagens amigáveis + build onefile"
```

Se `legislacao-renovaveis-user.spec` estiver ignorado por `.gitignore`, adiciona com:
```powershell
git add -f legislacao-renovaveis-user.spec
```
