' Eldercare Platform silent launcher (no window), for Task Scheduler auto-start
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "E:\eldercare-platform"
WshShell.Run """D:\daima\Anaconda3\python.exe"" ""E:\eldercare-platform\run.py""", 0, False
