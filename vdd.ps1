# Turns the Virtual Display Driver (the iPad's virtual screen) on or off.
# Run by the "iPad Display" scheduled tasks that display-setup.bat creates,
# so start.bat can switch it without asking for admin rights every time.
param([ValidateSet('on', 'off')][string]$Mode = 'on')

$log = Join-Path $PSScriptRoot 'vdd.log'
try {
    $dev = Get-PnpDevice -Class Display -ErrorAction Stop |
        Where-Object { $_.FriendlyName -like '*Virtual Display*' }
    if (-not $dev) { "$(Get-Date -Format s) $Mode : no Virtual Display Driver found" | Out-File $log -Append; exit 2 }
    if ($Mode -eq 'on') { $dev | Enable-PnpDevice -Confirm:$false -ErrorAction Stop }
    else { $dev | Disable-PnpDevice -Confirm:$false -ErrorAction Stop }
    "$(Get-Date -Format s) $Mode : ok ($($dev.FriendlyName -join ', '))" | Out-File $log -Append
} catch {
    "$(Get-Date -Format s) $Mode : $($_.Exception.Message)" | Out-File $log -Append
    exit 1
}
