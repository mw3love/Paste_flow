Set objShell = CreateObject("WScript.Shell")
objShell.Run "pythonw """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\run.pyw""", 0, False
