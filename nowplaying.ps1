# What's playing on Windows (Spotify, a browser, any app that shows in the
# volume flyout's media controls), printed as one JSON line whenever it
# changes. The album art is saved to the file given by -Art. Read by stats.py.
param([string]$Art)
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    if ($t.Wait(3000)) { return $t.Result }
    return $null
}
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
if (-not $mgr) { exit 1 }
$last = ''; $lastArt = ''; $artVer = 0
while ($true) {
    $o = @{ title = ''; artist = ''; album = ''; app = ''; playing = $false; pos = 0; len = 0; art = $artVer }
    $s = $mgr.GetCurrentSession()
    if ($s) {
        $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        if ($p) {
            $o.title = [string]$p.Title; $o.artist = [string]$p.Artist; $o.album = [string]$p.AlbumTitle
            $key = "$($p.Title)|$($p.Artist)"
            if ($key -ne $lastArt -and $Art) {
                $lastArt = $key
                Remove-Item -LiteralPath $Art -Force
                if ($p.Thumbnail) {
                    $st = Await ($p.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
                    if ($st) {
                        $net = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($st)
                        $fs = [System.IO.File]::Create($Art); $net.CopyTo($fs); $fs.Close(); $net.Dispose()
                    }
                }
                $artVer++; $o.art = $artVer
            }
        }
        $o.app = [string]$s.SourceAppUserModelId
        $o.playing = ([int]$s.GetPlaybackInfo().PlaybackStatus -eq 4)
        $tl = $s.GetTimelineProperties()
        if ($tl) { $o.pos = [int]$tl.Position.TotalSeconds; $o.len = [int]$tl.EndTime.TotalSeconds }
    }
    $j = $o | ConvertTo-Json -Compress
    if ($j -ne $last) { $last = $j; [Console]::Out.WriteLine($j); [Console]::Out.Flush() }
    Start-Sleep -Milliseconds 900
}
