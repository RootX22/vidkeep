# Install the built setup file the way a user would (silently here), then use the installed copy:
# its version, its own tools, a real download from a local web server, and a picture of the window.
# usage: packaging\windows\smoke.ps1 <link for the window to look up>
param([string]$Link)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
$work = Join-Path $root "build\smoke"
$app = Join-Path $work "installed"
New-Item -ItemType Directory -Force $work | Out-Null

Start-Process -Wait "$root\dist\vidkeep-setup.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/DIR=$app"
$exe = Join-Path $app "Vidkeep.exe"

function Invoke-Vidkeep([string[]]$arguments) {
    $output = & $exe --cli @arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Vidkeep.exe --cli $arguments failed:`n$output" }
    return $output
}

Write-Output (Invoke-Vidkeep @("--version"))
$doctor = Invoke-Vidkeep @("doctor", "-o", $work)
Write-Output $doctor
if ($doctor -match "missing") { throw "a bundled tool was not found" }

# A short video made by the bundled ffmpeg, downloaded by the installed vidkeep.
$site = Join-Path $work "site"
New-Item -ItemType Directory -Force $site | Out-Null
& "$app\bin\ffmpeg.exe" -v error -f lavfi -i "testsrc2=duration=5:size=320x180:rate=10" -c:v libx264 -pix_fmt yuv420p "$site\lecture.mp4"
$server = Start-Process -PassThru -WindowStyle Hidden python -ArgumentList "-m", "http.server", "8765", "--bind", "127.0.0.1", "--directory", $site
Start-Sleep -Seconds 2
try {
    Write-Output (Invoke-Vidkeep @("get", "-o", "$work\saved", "--", "http://127.0.0.1:8765/lecture.mp4"))
} finally {
    Stop-Process -Id $server.Id
}
$saved = Get-ChildItem "$work\saved" -Filter "lecture*.mp4"
if (-not $saved) { throw "the download did not save a file" }
Write-Output "saved $($saved.Name) ($($saved.Length) bytes)"

# The window, looking a link up, for the README and to see the theme on Windows.
$window = Start-Process -PassThru $exe -ArgumentList $Link
Start-Sleep -Seconds 40
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$picture = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
[System.Drawing.Graphics]::FromImage($picture).CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$picture.Save("$root\dist\windows-window.png")
if ($window.HasExited) { throw "the window closed by itself" }
Stop-Process -Id $window.Id
