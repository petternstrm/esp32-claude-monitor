' Starts the Claude usage proxy server silently in the background.
' Place this file in your Windows Startup folder to auto-start at login:
'   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
'
' Edit the two paths below before using:
'   1. PYTHON_EXE — path to your python.exe
'      Find it by running in cmd:  where python
'      If using Microsoft Store Python, use the full versioned path, e.g.:
'      C:\Users\<you>\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe
'   2. SCRIPT_PATH — full path to proxy_server.py in this repo

Const PYTHON_EXE  = "C:\Path\To\python.exe"
Const SCRIPT_PATH = "C:\Path\To\esp32-claude-monitor\proxy_server.py"

Set WShell = CreateObject("WScript.Shell")
WShell.Run """" & PYTHON_EXE & """ """ & SCRIPT_PATH & """", 0, False
