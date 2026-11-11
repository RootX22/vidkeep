# Build the Windows installer: vidkeep frozen with PyInstaller (the window, plus the command line
# behind --cli), its own ffmpeg, ffprobe and deno, wrapped by Inno Setup into one setup file.
# usage (from the repository root, on Windows with Python 3.12+):  packaging\windows\build.ps1
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is many times slower with the bar

$root = Resolve-Path "$PSScriptRoot\..\.."
$work = Join-Path $root "build\windows"
$version = (python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

# Deno is pinned and checked against its hash. yt-dlp's FFmpeg builds are only published as a rolling
# "latest", so that download is checked against the checksum list of the same release.
$denoUrl = "https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip"
$denoSha = "CBA84B26E0DF5AE7F74FC66B86A95B866872A86292EBD84FA1E959927EDD93BF"
$ffmpegRelease = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest"
$ffmpegZip = "ffmpeg-master-latest-win64-gpl-shared.zip"

function Fetch($url, $file, $sha) {
    Invoke-WebRequest -Uri $url -OutFile $file
    $actual = (Get-FileHash -Algorithm SHA256 $file).Hash
    if ($actual -ne $sha.ToUpper()) { throw "checksum mismatch for $url" }
}

if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force $work | Out-Null
Set-Location $root

python -m pip install --quiet ".[desktop]" pyinstaller==6.22.3
python -c "from PIL import Image; Image.open('vidkeep/assets/icon-256.png').save(r'$work\vidkeep.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
python -m PyInstaller --noconfirm --log-level WARN --windowed --name Vidkeep `
    --icon "$work\vidkeep.ico" `
    --collect-all yt_dlp_ejs --collect-submodules rich --collect-data sv_ttk --collect-data vidkeep --exclude-module mutagen `
    --distpath "$work\dist" --workpath "$work\pyinstaller" --specpath $work `
    packaging\desktop_entry.py

$bin = Join-Path $work "dist\Vidkeep\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
Fetch $denoUrl "$work\deno.zip" $denoSha
Expand-Archive "$work\deno.zip" -DestinationPath $bin
Invoke-WebRequest -Uri "$ffmpegRelease/checksums.sha256" -OutFile "$work\ffmpeg.sha256"
$ffmpegSha = ((Get-Content "$work\ffmpeg.sha256") | Where-Object { $_ -match " $ffmpegZip$" }).Split(" ")[0]
Fetch "$ffmpegRelease/$ffmpegZip" "$work\ffmpeg.zip" $ffmpegSha
Expand-Archive "$work\ffmpeg.zip" -DestinationPath "$work\ffmpeg"
$ffmpegBin = Get-ChildItem -Recurse -Directory "$work\ffmpeg" | Where-Object { $_.Name -eq "bin" } | Select-Object -First 1
Copy-Item "$($ffmpegBin.FullName)\ffmpeg.exe", "$($ffmpegBin.FullName)\ffprobe.exe", "$($ffmpegBin.FullName)\*.dll" $bin
Copy-Item packaging\THIRD_PARTY.txt, LICENSE "$work\dist\Vidkeep"

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { choco install innosetup --no-progress -y | Out-Null }
& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$work\dist\Vidkeep" "/DIconFile=$work\vidkeep.ico" `
    "/DOutputDir=$root\dist" packaging\windows\vidkeep.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Get-ChildItem "$root\dist\vidkeep-setup.exe"
