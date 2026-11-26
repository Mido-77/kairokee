# Kairokee - launcher and first-run setup for Windows 10/11 (64-bit).
# Written for the Windows PowerShell 5.1 that comes with Windows. Keep this file plain ASCII.
param(
    [switch]$Phone,    # let phones on the same Wi-Fi open the app
    [switch]$Repair    # throw away the installed packages and install them again
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

# ----------------------------------------------------------------------------- settings
$SetupVersion = 2            # raise this when a new app version needs a fresh setup (2: PyTorch 2.14)
$FirstPort = 8000            # if it is taken, the next free one up to $LastPort is used
$LastPort = 8010
$TorchVersion = '2.14.1'
$TorchAudioVersion = '2.11.0'  # torchaudio stopped at 2.11; it works with newer PyTorch
$TorchVisionVersion = '0.29.1'
# Which PyTorch build for which NVIDIA card:
#  cu126 - every card from the GTX 900 series up to the RTX 40 series, NVIDIA driver 528 or newer
#  cu130 - RTX 50 series (Blackwell) and newer, NVIDIA driver 580 or newer
$CudaBuilds = @{
    cu126 = @{ Index = 'https://download.pytorch.org/whl/cu126'; MinDriver = 528 }
    cu130 = @{ Index = 'https://download.pytorch.org/whl/cu130'; MinDriver = 580 }
}
$CpuIndex = 'https://download.pytorch.org/whl/cpu'
$MinCompute = 5.0            # oldest card generation PyTorch still supports (Maxwell, GTX 900)
$BlackwellCompute = 10.0     # from here on (RTX 50) the cu130 build is needed
$MinFreeGB = 12
$MinRamGB = 6
# Fixed versions with checksums: a broken or changed upstream release cannot break new installs.
$UvUrl = 'https://github.com/astral-sh/uv/releases/download/0.13.9/uv-x86_64-pc-windows-msvc.zip'
$UvSha = 'edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb'
$FfmpegUrls = @(    # the same file in both places (gyan.dev's own GitHub copy is much faster)
    'https://github.com/GyanD/codexffmpeg/releases/download/9.1/ffmpeg-9.1-essentials_build.zip',
    'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.1-essentials_build.zip'
)
$FfmpegSha = 'b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd'
$VcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
$DenoUrl = 'https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip'
$DenoSha = 'cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf'

$SetupDir = $PSScriptRoot
$Root = Split-Path -Parent $SetupDir
$AppDir = Join-Path $Root 'app'

# ----------------------------------------------------------------------------- small helpers
function Write-Title($text) { Write-Host ''; Write-Host $text -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    OK: $text" -ForegroundColor Green }
function Write-Note($text) { Write-Host "    NOTE: $text" -ForegroundColor Yellow }
function Write-Info($text) { Write-Host "    $text" }

function Get-KairokeeHome {
    if ($env:KAIROKEE_HOME) { return $env:KAIROKEE_HOME }
    $base = $env:LOCALAPPDATA
    # Some tools fail on folder names with non-English letters, so use a plain folder in that case.
    if ((-not $base) -or ($base -match '[^\x00-\x7F]')) { return 'C:\Kairokee' }
    return (Join-Path $base 'Kairokee')
}

function Get-NativeOutput {
    # Run a program and capture what it prints, without PowerShell turning warnings into errors.
    param([string]$Exe, [string[]]$ArgList)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = & $Exe @ArgList 2>&1 | ForEach-Object { "$_" }
        return @{ Code = $LASTEXITCODE; Out = @($lines) }
    }
    catch {
        return @{ Code = 1; Out = @("$_") }
    }
    finally { $ErrorActionPreference = $old }
}

function Invoke-Native {
    # Run a program and show its output. Stop with a clear message if it fails.
    param([string]$Exe, [string[]]$ArgList, [string]$What, [int]$Tries = 1)
    for ($i = 1; $i -le $Tries; $i++) {
        & $Exe @ArgList
        if ($LASTEXITCODE -eq 0) { return }
        if ($i -lt $Tries) {
            Write-Note "$What did not finish (try $i of $Tries). Trying again..."
            Start-Sleep -Seconds 3
        }
    }
    throw "$What failed. Scroll up to read the error. A weak internet connection is the usual cause: just open Kairokee again and it will continue."
}

function Get-File([string]$Url, [string]$Dest, [string]$Sha256 = '') {
    $part = "$Dest.part"
    if (Test-Path $part) { Remove-Item -Force $part }
    Invoke-WebRequest -Uri $Url -OutFile $part -UseBasicParsing
    if ($Sha256) {
        $got = (Get-FileHash -Algorithm SHA256 $part).Hash.ToLowerInvariant()
        if ($got -ne $Sha256.ToLowerInvariant()) {
            Remove-Item -Force $part -ErrorAction SilentlyContinue
            throw "The download from $(([uri]$Url).Host) was damaged or changed (checksum does not match). Open Kairokee again to retry."
        }
    }
    Move-Item -Force $part $Dest
}

function Test-Internet {
    try {
        Invoke-WebRequest -Uri 'https://pypi.org/simple/' -Method Head -UseBasicParsing -TimeoutSec 15 | Out-Null
        return $true
    }
    catch { return $false }
}

function Read-State {
    if (Test-Path $StateFile) {
        try { return (Get-Content -Raw -Encoding UTF8 $StateFile | ConvertFrom-Json) } catch { }
    }
    return $null
}

function Save-State($state) {
    $state | ConvertTo-Json | Set-Content -Encoding UTF8 $StateFile
}

function Get-Whisper([string]$device, [int]$vram) {
    if ($device -eq 'cuda') {
        if ($vram -ge 8000) { return 'large-v3' }
        if ($vram -ge 3500) { return 'medium' }
    }
    return 'small'
}

# ----------------------------------------------------------------------------- graphics card
function Get-GpuPlan {
    $plan = @{ Device = 'cpu'; Name = ''; VramMB = 0; Driver = ''; Reason = ''; Compute = 0.0; Build = '' }
    $smi = $null
    $found = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($found) { $smi = $found.Source }
    if (-not $smi) {
        foreach ($c in @("$env:SystemRoot\System32\nvidia-smi.exe", "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe")) {
            if (Test-Path $c) { $smi = $c; break }
        }
    }
    if (-not $smi) { $plan.Reason = 'No NVIDIA graphics card was found.'; return $plan }

    $hasCc = $true
    $r = Get-NativeOutput $smi @('--query-gpu=name,driver_version,memory.total,compute_cap', '--format=csv,noheader,nounits')
    if ($r.Code -ne 0) {
        $hasCc = $false
        $r = Get-NativeOutput $smi @('--query-gpu=name,driver_version,memory.total', '--format=csv,noheader,nounits')
    }
    if (($r.Code -ne 0) -or ($r.Out.Count -eq 0)) {
        $plan.Reason = 'The NVIDIA driver did not answer. It may need to be updated or reinstalled.'
        return $plan
    }

    # if there are several cards, use the one with the most memory
    $best = $null
    $bestMem = -1
    foreach ($line in $r.Out) {
        $parts = @("$line".Split(',') | ForEach-Object { $_.Trim() })
        if ($parts.Count -lt 3) { continue }
        $mem = 0
        [void][int]::TryParse($parts[2], [ref]$mem)
        if ($mem -gt $bestMem) { $bestMem = $mem; $best = $parts }
    }
    if (-not $best) { $plan.Reason = 'Could not read the NVIDIA card details.'; return $plan }

    $plan.Name = $best[0]
    $plan.Driver = $best[1]
    $plan.VramMB = $bestMem
    $driverMajor = 0
    [void][int]::TryParse($best[1].Split('.')[0], [ref]$driverMajor)
    $cc = $null
    if ($hasCc -and ($best.Count -ge 4)) {
        $tmp = 0.0
        if ([double]::TryParse($best[3], [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$tmp)) { $cc = $tmp }
    }

    if ($cc -ne $null) { $plan.Compute = $cc }

    if (($cc -ne $null) -and ($cc -lt $MinCompute)) {
        $plan.Reason = "The graphics card ($($plan.Name)) is too old for GPU mode."
        return $plan
    }
    $build = 'cu126'
    if (($cc -ne $null) -and ($cc -ge $BlackwellCompute)) { $build = 'cu130' }
    $need = $CudaBuilds[$build].MinDriver
    if ($driverMajor -lt $need) {
        $plan.Reason = "The NVIDIA driver is too old for this card (version $($best[1]); version $need or newer is needed). Update it from nvidia.com/drivers, then open Kairokee-Repair.bat to use the graphics card."
        return $plan
    }
    $plan.Build = $build
    if ($bestMem -lt 2000) {
        $plan.Reason = "The graphics card ($($plan.Name)) has too little memory ($bestMem MB) for GPU mode."
        return $plan
    }
    $plan.Device = 'cuda'
    return $plan
}

# ----------------------------------------------------------------------------- setup (first run)
function Install-Torch([string]$device, [string]$build = '', [bool]$swapOnly = $false) {
    if ($device -eq 'cuda') { $index = $CudaBuilds[$build].Index } else { $index = $CpuIndex }
    $a = @('pip', 'install', '--python', $VenvPy, '--reinstall')
    if ($swapOnly) { $a += '--no-deps' }
    $a += @("torch==$TorchVersion", "torchaudio==$TorchAudioVersion", "torchvision==$TorchVisionVersion", '--index-url', $index)
    Invoke-Native $Uv $a 'Installing PyTorch (the biggest download)' 3
}

function Invoke-SelfTest {
    $r = Get-NativeOutput $VenvPy @((Join-Path $SetupDir 'selftest.py'))
    foreach ($line in $r.Out) {
        if ($line.StartsWith('KAIROKEE_SELFTEST ')) {
            return ($line.Substring(18) | ConvertFrom-Json)
        }
    }
    return [pscustomobject]@{ torch = $null; cuda_available = $false; gpu_test = 'not run'; problems = @(); torch_error = ('The test program printed nothing useful: ' + ($r.Out -join ' ')) }
}

function Show-DllHint($text) {
    if ($text -match 'DLL load failed|WinError 126|WinError 127|c10\.dll|VCRUNTIME|MSVCP') {
        Write-Host ''
        Write-Host '    Windows is missing a free Microsoft component (Visual C++ Redistributable).' -ForegroundColor Yellow
        Write-Host "    Install it from:  $VcRedistUrl" -ForegroundColor Yellow
        Write-Host '    Then open Kairokee-Repair.bat.' -ForegroundColor Yellow
    }
}

function Invoke-Setup {
    $total = 7

    Write-Title "[1/$total] Checking this PC"
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Kairokee needs 64-bit Windows 10 or 11.' }
    if ([Environment]::OSVersion.Version.Build -lt 17763) { throw 'Kairokee needs Windows 10 (version 1809 or newer) or Windows 11. Please update Windows.' }
    if ($IsArm) {
        if ([Environment]::OSVersion.Version.Build -lt 22000) { throw 'On a Windows on ARM computer, Kairokee needs Windows 11 (Windows 10 on ARM cannot run it).' }
        Write-Note 'This is a Windows on ARM computer (for example Snapdragon). Kairokee runs here in compatibility mode on the processor: it works, but slowly. This has not been fully tested yet.'
    }
    Write-Ok "Windows build $([Environment]::OSVersion.Version.Build)"

    New-Item -ItemType Directory -Force $KHome | Out-Null
    $drive = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($KHome))
    $freeGB = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    if ($freeGB -lt $MinFreeGB) { throw "Not enough free disk space on $($drive.Name): $freeGB GB free, about $MinFreeGB GB needed. Free some space and open Kairokee again." }
    Write-Ok "$freeGB GB free on $($drive.Name)"

    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    if ($ramGB -lt $MinRamGB) { throw "This PC has only $ramGB GB of memory (RAM). Kairokee needs at least $MinRamGB GB (8 GB or more is recommended)." }
    if ($ramGB -lt 8) { Write-Note "Only $ramGB GB of RAM: it will work, but slowly." } else { Write-Ok "$ramGB GB of RAM" }

    if (-not (Test-Internet)) { throw 'No internet connection. The first setup needs the internet (about 3 GB). Connect and open Kairokee again.' }
    Write-Ok 'Internet works'

    if ($IsArm) { $gpu = @{ Device = 'cpu'; Name = ''; VramMB = 0; Reason = 'Windows on ARM: NVIDIA graphics cards are not supported here.'; Build = '' } }
    else { $gpu = Get-GpuPlan }
    if ($gpu.Device -eq 'cuda') {
        Write-Ok "NVIDIA graphics card: $($gpu.Name), $($gpu.VramMB) MB memory, driver $($gpu.Driver) -> fast GPU mode (PyTorch build $($gpu.Build))"
    }
    else {
        Write-Note "$($gpu.Reason)"
        Write-Note 'Kairokee will use the processor (CPU). It works, but each song takes much longer.'
    }

    Write-Title "[2/$total] Getting the installer tool (uv)"
    if (-not (Test-Path $Uv)) {
        New-Item -ItemType Directory -Force $ToolsDir | Out-Null
        $zip = Join-Path $ToolsDir 'uv.zip'
        Write-Info 'Downloading...'
        Get-File $UvUrl $zip $UvSha
        Expand-Archive -Force $zip $ToolsDir
        Remove-Item -Force $zip
        if (-not (Test-Path $Uv)) {
            $f = Get-ChildItem $ToolsDir -Recurse -Filter uv.exe | Select-Object -First 1
            if ($f) { Copy-Item $f.FullName $Uv }
        }
        if (-not (Test-Path $Uv)) { throw 'Could not set up the installer tool (uv).' }
    }
    Write-Ok 'Ready'

    Write-Title "[3/$total] Getting Python (a private copy, nothing else on your PC is touched)"
    Invoke-Native $Uv @('python', 'install', $PyRequest, '--no-bin') 'Installing Python' 3
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
    Invoke-Native $Uv @('venv', $VenvDir, '--python', $PyRequest) 'Creating the Kairokee environment'
    Write-Ok 'Ready'

    Write-Title "[4/$total] Installing PyTorch ($($gpu.Device.ToUpper()) version) - this is the big one, please wait"
    Install-Torch $gpu.Device $gpu.Build
    Write-Ok 'Installed'

    Write-Title "[5/$total] Installing the other parts"
    $reqArgs = @('pip', 'install', '--python', $VenvPy, '-r', (Join-Path $SetupDir 'requirements.txt'))
    $cons = Join-Path $SetupDir 'constraints.txt'     # exact versions that are known to work together
    if (Test-Path $cons) { $reqArgs += @('-c', $cons) }
    Invoke-Native $Uv $reqArgs 'Installing the other parts' 3
    Write-Ok 'Installed'

    Write-Title "[6/$total] Getting ffmpeg (audio tool)"
    $haveSystem = (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)
    $haveOwn = (Test-Path (Join-Path $FfmpegDir 'ffmpeg.exe')) -and (Test-Path (Join-Path $FfmpegDir 'ffprobe.exe'))
    if ($haveSystem) { Write-Ok 'ffmpeg is already installed on this PC' }
    elseif ($haveOwn) { Write-Ok 'Ready' }
    else {
        $done = $false
        foreach ($url in $FfmpegUrls) {
            try {
                Write-Info "Downloading from $(([uri]$url).Host) ..."
                $tmp = Join-Path $KHome 'cache\ffmpeg-tmp'
                if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
                New-Item -ItemType Directory -Force $tmp | Out-Null
                $zip = Join-Path $tmp 'ffmpeg.zip'
                Get-File $url $zip $FfmpegSha
                Expand-Archive -Force $zip (Join-Path $tmp 'x')
                $ff = Get-ChildItem (Join-Path $tmp 'x') -Recurse -Filter ffmpeg.exe | Select-Object -First 1
                $fp = Get-ChildItem (Join-Path $tmp 'x') -Recurse -Filter ffprobe.exe | Select-Object -First 1
                if ($ff -and $fp) {
                    New-Item -ItemType Directory -Force $FfmpegDir | Out-Null
                    Copy-Item $ff.FullName (Join-Path $FfmpegDir 'ffmpeg.exe') -Force
                    Copy-Item $fp.FullName (Join-Path $FfmpegDir 'ffprobe.exe') -Force
                    $done = $true
                }
                Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
                if ($done) { break }
            }
            catch { Write-Note "That download did not work: $($_.Exception.Message)" }
        }
        if (-not $done) { throw 'Could not download ffmpeg. Check the internet connection and open Kairokee again.' }
        Write-Ok 'Ready'
    }

    Write-Title "[7/$total] Testing everything"
    $device = $gpu.Device
    $test = Invoke-SelfTest
    if ($test.torch_error) {
        Show-DllHint $test.torch_error
        throw "PyTorch does not start on this PC: $($test.torch_error)"
    }
    if (($device -eq 'cuda') -and (-not $test.cuda_build)) {
        Write-Note 'Another part replaced PyTorch with the CPU version. Putting the GPU version back...'
        Install-Torch 'cuda' $gpu.Build $true
        $test = Invoke-SelfTest
        if ($test.torch_error) { Show-DllHint $test.torch_error; throw "PyTorch does not start on this PC: $($test.torch_error)" }
    }
    if ($device -eq 'cuda') {
        if ((-not $test.cuda_available) -or ($test.gpu_test -ne 'ok')) {
            Write-Note "The graphics card did not pass the test ($($test.gpu_test)). Switching to CPU mode."
            Install-Torch 'cpu' '' $true
            $device = 'cpu'
            $test = Invoke-SelfTest
        }
        else { Write-Ok "Graphics card works: $($test.gpu)" }
    }
    if ($test.torch_error) { throw "PyTorch does not start on this PC: $($test.torch_error)" }
    $problems = @($test.problems | Where-Object { $_ })
    if ($problems.Count -gt 0) {
        foreach ($p in $problems) { Write-Host "    PROBLEM: $p" -ForegroundColor Red }
        throw 'Some parts did not install correctly. Open Kairokee-Repair.bat. If it happens again, send the log file to Mido.'
    }
    Write-Ok "PyTorch $($test.torch), mode: $($device.ToUpper())"

    $vram = 0
    if ($device -eq 'cuda') { $vram = $gpu.VramMB }
    $seg = 0
    if (($device -eq 'cuda') -and ($vram -lt 3500)) { $seg = 4 }
    Save-State ([pscustomobject]@{
            SetupVersion = $SetupVersion
            Device = $device
            GpuName = $gpu.Name
            VramMB = $vram
            Whisper = (Get-Whisper $device $vram)
            Segment = $seg
            CudaBuild = $gpu.Build
            LastYtdlpCheck = (Get-Date).ToString('o')
        })
    Write-Host ''
    Write-Host 'Setup finished. You will not see this part again.' -ForegroundColor Green
}

# ----------------------------------------------------------------------------- start the app
function Update-YtDlp($state) {
    # YouTube changes often and yt-dlp must follow. Check at most once a day; never block on failure.
    try {
        $last = [datetime]::Parse($state.LastYtdlpCheck)
        if (((Get-Date) - $last).TotalHours -lt 24) { return $state }
    }
    catch { }
    Write-Info 'Checking for a newer yt-dlp (the YouTube downloader)...'
    $r = Get-NativeOutput $Uv @('pip', 'install', '--python', $VenvPy, '--upgrade-package', 'yt-dlp', '--quiet', 'yt-dlp[default]')
    if ($r.Code -eq 0) {
        $state.LastYtdlpCheck = (Get-Date).ToString('o')
        Save-State $state
    }
    return $state
}

function Confirm-YouTubeHelpers {
    # Since late 2025 YouTube needs two helpers next to yt-dlp: the "yt-dlp-ejs" package and a
    # JavaScript runtime (Deno). This installs whichever is missing (fast when everything is there).
    try {
        $r = Get-NativeOutput $Uv @('pip', 'show', '--python', $VenvPy, 'yt-dlp-ejs')
        if ($r.Code -ne 0) {
            Write-Info 'Installing the YouTube helper package (yt-dlp-ejs)...'
            Invoke-Native $Uv @('pip', 'install', '--python', $VenvPy, 'yt-dlp[default]') 'Installing the YouTube helper package' 3
        }
        $denoExe = Join-Path $DenoDir 'deno.exe'
        if (-not (Test-Path $denoExe)) {
            Write-Info 'Getting Deno (a small program that YouTube downloads need)...'
            New-Item -ItemType Directory -Force $DenoDir | Out-Null
            $zip = Join-Path $DenoDir 'deno.zip'
            Get-File $DenoUrl $zip $DenoSha
            Expand-Archive -Force $zip $DenoDir
            Remove-Item -Force $zip
            if (-not (Test-Path $denoExe)) { throw 'deno.exe was not found after unpacking.' }
        }
    }
    catch {
        Write-Note "Could not set up the YouTube helpers ($($_.Exception.Message)). Downloading songs may fail until this works; open Kairokee again with the internet on."
    }
}

function Test-KairokeeRunning([int]$p) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/api/config" -UseBasicParsing -TimeoutSec 3
        return (($r.StatusCode -eq 200) -and ($r.Content -match '"perMinute"'))
    }
    catch { return $false }
}

function Test-PortBusy([int]$p) {
    return [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

function Start-Kairokee($state) {
    $Port = 0
    for ($p = $FirstPort; $p -le $LastPort; $p++) {
        if (Test-PortBusy $p) {
            if (Test-KairokeeRunning $p) {
                Write-Host 'Kairokee is already running. Opening it...' -ForegroundColor Green
                Start-Process "http://localhost:$p"
                return
            }
            continue
        }
        $Port = $p
        break
    }
    if ($Port -eq 0) { throw "Other programs are using all the ports Kairokee can use ($FirstPort to $LastPort). Restart the PC and open Kairokee again." }
    if ($Port -ne $FirstPort) { Write-Note "Port $FirstPort is used by another program, so Kairokee uses port $Port." }
    $env:KAIROKEE_PORT = "$Port"

    New-Item -ItemType Directory -Force (Join-Path $DataDir 'output') | Out-Null
    $env:KAIROKEE_DATA = $DataDir
    $env:KAIROKEE_WHISPER_MODEL = $state.Whisper
    if ([int]$state.Segment -gt 0) { $env:KAIROKEE_SEGMENT = "$($state.Segment)" }
    if (-not $env:KAIROKEE_SPLIT_PRECISION) {
        # Half-precision math makes the singer/ad-lib split about twice as fast with the same result
        # on RTX 30/40 cards (checked on an RTX 3050 Ti). Older cards keep the normal precision.
        $prec = 'fp32'
        if ($state.Device -eq 'cuda') {
            $g = Get-GpuPlan
            if (($g.Device -eq 'cuda') -and ($g.Compute -ge 8.0)) { $prec = 'autocast' }
        }
        $env:KAIROKEE_SPLIT_PRECISION = $prec
    }
    $env:KAIROKEE_DEVICE = $state.Device
    if ($state.GpuName) { $env:KAIROKEE_GPU_NAME = $state.GpuName }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
    if (Test-Path $FfmpegDir) { $env:PATH = "$FfmpegDir;$env:PATH" }
    if (Test-Path $DenoDir) { $env:PATH = "$DenoDir;$env:PATH" }
    $env:DENO_NO_UPDATE_CHECK = '1'

    if ($state.Device -eq 'cuda') { $modeText = "fast GPU mode ($($state.GpuName))" } else { $modeText = 'CPU mode (slower: a song can take many minutes)' }
    Write-Host ''
    Write-Host '  Kairokee' -ForegroundColor Cyan
    Write-Host "  Mode: $modeText"
    Write-Host '  Starting... your browser will open in a moment.'
    if ($Phone) {
        Write-Host ''
        Write-Host '  PHONE MODE: phones on the same Wi-Fi can open the address shown below.' -ForegroundColor Yellow
        Write-Host '  If the phone cannot open it: set this Wi-Fi to "Private" in Windows' -ForegroundColor Yellow
        Write-Host '  (Settings > Network > Wi-Fi > your network > Private), and click Allow if Windows asks.' -ForegroundColor Yellow
        $pub = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' }
        if ($pub) { Write-Host "  Right now your network is set to Public: $($pub[0].Name)" -ForegroundColor Red }
    }
    Write-Host '  To stop Kairokee, close this window.'

    $serverArgs = @('"' + (Join-Path $AppDir 'server.py') + '"')
    if ($Phone) { $serverArgs += '--phone' }
    $proc = Start-Process -FilePath $VenvPy -ArgumentList $serverArgs -WorkingDirectory $AppDir -NoNewWindow -PassThru
    $null = $proc.Handle

    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        if ($proc.HasExited) { break }
        if (Test-KairokeeRunning $Port) { $ready = $true; break }
        Start-Sleep -Milliseconds 700
    }
    if ($ready) { Start-Process "http://localhost:$Port" }
    elseif ($proc.HasExited) { throw "Kairokee stopped right after starting (code $($proc.ExitCode)). Read the message above." }
    else { Write-Note "Kairokee is taking long to start. Open http://localhost:$Port yourself in a minute." }
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) { throw "Kairokee stopped (code $($proc.ExitCode)). Read the message above." }
}

# ----------------------------------------------------------------------------- main
$IsArm = ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') -or ($env:PROCESSOR_ARCHITEW6432 -eq 'ARM64')
# On Windows on ARM the normal x64 Python runs through Windows 11's emulation (most AI packages have no ARM build)
if ($IsArm) { $PyRequest = 'cpython-3.11-windows-x86_64-none' } else { $PyRequest = '3.11' }
$KHome = Get-KairokeeHome
$ToolsDir = Join-Path $KHome 'tools'
$Uv = Join-Path $ToolsDir 'uv.exe'
$VenvDir = Join-Path $KHome 'venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
$FfmpegDir = Join-Path $KHome 'ffmpeg'
$DenoDir = Join-Path $KHome 'deno'
$DataDir = Join-Path $KHome 'data'
$LogDir = Join-Path $KHome 'logs'
$StateFile = Join-Path $KHome 'state.json'

$env:UV_PYTHON_INSTALL_DIR = Join-Path $KHome 'python'
$env:UV_CACHE_DIR = Join-Path $KHome 'cache\uv'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_HTTP_TIMEOUT = '300'

$transcript = $false
try {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    $logFile = Join-Path $LogDir ("kairokee-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Start-Transcript -Path $logFile | Out-Null
    $transcript = $true
    Get-ChildItem $LogDir -Filter 'kairokee-*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item -Force -ErrorAction SilentlyContinue
}
catch { }

$exitCode = 0
try {
    if (-not (Test-Path (Join-Path $AppDir 'server.py'))) { throw 'The app files are missing. Please extract the whole ZIP file first (right-click the ZIP > Extract All), then open Kairokee.bat from the new folder.' }

    if ($Repair) {
        Write-Host 'Repair: removing the installed parts (your songs and downloaded models are kept)...' -ForegroundColor Yellow
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
        if (Test-Path $StateFile) { Remove-Item -Force $StateFile }
    }

    $state = Read-State
    $ready = $state -and ($state.SetupVersion -eq $SetupVersion) -and (Test-Path $VenvPy)
    if (-not $ready) {
        Write-Host ''
        Write-Host '  Welcome to Kairokee. First-time setup starts now.' -ForegroundColor Cyan
        Write-Host '  It downloads about 3 GB once and may take 10-60 minutes. Please keep this window open.'
        Invoke-Setup
        $state = Read-State
    }
    else {
        $state = Update-YtDlp $state
    }
    Confirm-YouTubeHelpers
    Start-Kairokee $state
}
catch {
    $exitCode = 1
    Write-Host ''
    Write-Host '  Kairokee could not start.' -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    Show-DllHint "$($_.Exception.Message)"
    if ($logFile) { Write-Host "  Details were saved here: $logFile" -ForegroundColor Yellow }
}
finally {
    if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
}
exit $exitCode
