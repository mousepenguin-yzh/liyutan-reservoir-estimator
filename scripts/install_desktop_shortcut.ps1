# Maintainer-only, one-time setup. Does not set any shared-storage capability.
param([string]$DestinationDirectory)
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction Stop).Source
& $python -c "import sys, tkinter; assert sys.version_info >= (3, 12); sys.path.insert(0, sys.argv[1]); from desktop_runtime import local_path; local_path(sys.argv[1])" $repository
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ with tkinter is required.' }
Get-Command git.exe -ErrorAction Stop | Out-Null
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe not found beside python.exe.' }
$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders.Item('Desktop')
if ($DestinationDirectory) { $desktop = $DestinationDirectory }
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'Liyutan Estimator.lnk'))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + (Join-Path $repository 'desktop_launcher.py') + '"'
$shortcut.WorkingDirectory = $repository
$shortcut.Description = 'Liyutan reservoir launcher and updater'
$shortcut.Save()
Write-Output 'Desktop shortcut installed. Keep this bootstrap repository in place.'
