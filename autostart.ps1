# Adds or removes the iPad Display host from Windows startup (no admin needed).
# Called by autostart-setup.bat.
param([ValidateSet('on', 'off')][string]$Mode = 'on')

$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'iPad Display host.lnk'
$pyw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$server = Join-Path $PSScriptRoot 'server.py'

# Stop a host that is already running in the background from this folder.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$server*--background*" } |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }

if ($Mode -eq 'off') {
    Remove-Item $lnk -ErrorAction SilentlyContinue
    Write-Host 'Auto-start removed. Use start.bat again when you want the host.'
    exit 0
}

if (-not (Test-Path $pyw)) {
    Write-Host 'Run start.bat once first (it installs what the host needs), then run this again.'
    exit 1
}
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = "`"$server`" --background"
$s.WorkingDirectory = $PSScriptRoot
$s.Description = 'iPad Display host (runs in the background)'
$s.Save()
Start-Process -FilePath $pyw -ArgumentList "`"$server`" --background" -WorkingDirectory $PSScriptRoot
Write-Host 'Auto-start is on, and the host is running in the background now.'
