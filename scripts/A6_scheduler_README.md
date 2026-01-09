# A6.2 — Agendamento (Windows Task Scheduler)

Este guia agenda o coletor para correr diariamente via Task Scheduler, usando o launcher PowerShell `scripts/run_coletor.ps1`.

Repo detetado (pelo teu output): `D:\02 Technical\Apps\legislacao-renovaveis`

## Opção 1 — Importar XML (mais rápido)
1. Abrir **Task Scheduler**.
2. **Import Task…**
3. Escolher o ficheiro:
   - `scripts/task_scheduler_legislacao_renovaveis.xml`
4. No separador **Triggers**, ajusta a hora/dias se necessário.
5. Guarda.

## Opção 2 — Criar pela GUI (mais flexível)
1. Abrir **Task Scheduler**.
2. **Create Task…** (não “Basic Task”).
3. Trigger: Daily (ou o que quiseres).
4. Action → Start a program:
   - Program/script:
     `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
   - Add arguments:
     `-NoProfile -ExecutionPolicy Bypass -File "D:\02 Technical\Apps\legislacao-renovaveis\scripts\run_coletor.ps1" -Profile renovaveis_todos -Days 1`
   - Start in:
     `D:\02 Technical\Apps\legislacao-renovaveis`

## Logs para ficheiro (recomendado)
O Task Scheduler não é ótimo a guardar output; a forma simples é redirecionar para um ficheiro.

1. Cria a pasta:
   - `D:\02 Technical\Apps\legislacao-renovaveis\logs`
2. Em vez de `-File ...`, usa `-Command ...`:
   - Program/script:
     `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
   - Add arguments (linha única):
     `-NoProfile -ExecutionPolicy Bypass -Command "& 'D:\02 Technical\Apps\legislacao-renovaveis\scripts\run_coletor.ps1' -Profile renovaveis_todos -Days 1 *>> 'D:\02 Technical\Apps\legislacao-renovaveis\logs\task.log'"`

> `*>>` faz append de stdout+stderr.

## Testar manualmente
No Task Scheduler, botão direito na task → **Run**.
Depois abre `logs\task.log` (se ativaste logs) e confirma que gerou relatório.

