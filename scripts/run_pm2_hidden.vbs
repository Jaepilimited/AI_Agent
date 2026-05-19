Set WshShell = WScript.CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -WindowStyle Hidden -Command ""cd 'C:\Users\DB_PC\Desktop\python_bcj\AI_Agent'; pm2 resurrect; pm2 save""", 0, False
