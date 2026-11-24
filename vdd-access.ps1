# Lets the signed-in user see and start the two "iPad Display" tasks that
# display-setup.bat creates. They run as SYSTEM (switching a display adapter
# needs it), and Windows hides such tasks from normal programs and refuses to
# start them ("Access is denied") unless they say otherwise. Without this the
# host can't switch the iPad's virtual screen on or off. display-setup.bat
# runs it with admin rights; it only changes who may read and run those tasks.
$ErrorActionPreference = 'Stop'
$ace = '(A;;GRGX;;;AU)'            # Authenticated Users: read and run

function Grant($obj) {
    $sd = $obj.GetSecurityDescriptor(4)            # the DACL only, e.g. "D:PAI(A;;FA;;;SY)..."
    if ($sd -like "*$ace*") { return }
    # Put the new entry first so it sits before any inherited entries.
    $new = $sd -replace '^(D:[A-Z_]*)', ('$1' + $ace)
    $obj.SetSecurityDescriptor($new, 0)
}

try {
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $folder = $svc.GetFolder('\iPad Display')
    Grant $folder
    foreach ($name in 'Screen on', 'Screen off') { Grant ($folder.GetTask($name)) }
    'ok'
} catch {
    "failed: $($_.Exception.Message)"
    exit 1
}
