Set WshShell = WScript.CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c " & Chr(34) & "C:\Users\DB_PC\Desktop\python_bcj\AI_Agent\scripts\daily_git_push.bat" & Chr(34), 0, False
