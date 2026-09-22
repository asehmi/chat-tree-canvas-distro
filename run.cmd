@echo off
call .venv\Scripts\activate.bat

rem One-time-per-machine: register the local canvas package with bun so the
rem "chat-tree-canvas-react@link:..." dependency resolves (idempotent).
set BUN=%LocalAppData%\reflex\bun\bin\bun.exe
if exist "%BUN%" (
  pushd packages\chat-tree-canvas-react
  "%BUN%" link >nul 2>&1
  popd
)

reflex run
