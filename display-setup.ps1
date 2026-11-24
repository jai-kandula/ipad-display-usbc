# Sets up (or removes) the two tasks that switch the iPad's virtual screen on
# and off. display-setup.bat runs this with admin rights.
#
#  - The tasks run vdd.ps1 as SYSTEM (switching a display adapter needs it).
#    vdd.ps1 is copied to "Program Files\iPad Display" first and run from
#    there: run as SYSTEM from Downloads, any program could edit it and take
#    over the PC.
#  - Windows hides SYSTEM tasks from normal programs and refuses to start them
#    ("Access is denied"), so the signed-in user (only that account) is
#    allowed to see and start these two tasks. That is all it may do: it
#    can't change them.
#  - They also run on battery (Windows' default skips tasks on battery).
#  - "Screen off" also runs at start-up, so the screen is gone after a restart.
param([string]$UserSid = '', [switch]$Remove)
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:ProgramFiles 'iPad Display'
$names = 'Screen on', 'Screen off'

if ($Remove) {
    foreach ($n in $names) { Unregister-ScheduledTask -TaskPath '\iPad Display\' -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    'Removed. The virtual screen now stays as you leave it in Device Manager.'
    exit 0
}

if (-not $UserSid) { $UserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
if ($UserSid -notmatch '^S-1-[0-9-]+$') { throw "That doesn't look like a Windows account id: $UserSid" }

# 1. vdd.ps1 into Program Files (only admins can change files there).
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot 'vdd.ps1') (Join-Path $dir 'vdd.ps1')
$script = Join-Path $dir 'vdd.ps1'

# 2. The tasks.
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
foreach ($mode in 'on', 'off') {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Mode $mode"
    $reg = @{ TaskPath = '\iPad Display\'; TaskName = "Screen $mode"; Action = $action
               Principal = $principal; Settings = $settings; Force = $true }
    if ($mode -eq 'off') { $reg.Trigger = New-ScheduledTaskTrigger -AtStartup }
    Register-ScheduledTask @reg | Out-Null
}

# 3. Let this account see and start them (read + run, nothing else).
$ace = "(A;;GRGX;;;$UserSid)"
function Grant($obj) {
    $sd = $obj.GetSecurityDescriptor(4)            # the DACL only, e.g. "D:PAI(A;;FA;;;SY)..."
    if ($sd -like "*$ace*") { return }
    $obj.SetSecurityDescriptor(($sd -replace '^(D:[A-Z_]*)', ('$1' + $ace)), 0)
}
$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
$folder = $svc.GetFolder('\iPad Display')
Grant $folder
foreach ($n in $names) { Grant ($folder.GetTask($n)) }
"ok: tasks set up, run from $script"
