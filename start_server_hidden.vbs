' 智慧养老平台 静默启动（无窗口），配合 Windows 任务计划开机自启
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "E:\eldercare-platform"
WshShell.Run """D:\daima\Anaconda3\python.exe"" ""E:\eldercare-platform\run.py""", 0, False
