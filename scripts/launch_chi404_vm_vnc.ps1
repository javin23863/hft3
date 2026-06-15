# CHI404 Windows VM console: SSH tunnel + VNC viewer (workstation -> colo sidecar).
# VM VNC listens on chi404 127.0.0.1:5900 only; tunnel maps localhost:5900.
param(
    [string]$SshHost = 'chi404',
    [int]$LocalPort = 5900,
    [string]$RemoteHost = '127.0.0.1',
    [int]$RemotePort = 5900,
    [switch]$InstallDesktopShortcut,
    [switch]$TunnelOnly,
    [switch]$ResetTunnel
)

$ErrorActionPreference = 'Stop'
$CanonicalScript = 'C:\Users\MSI\repos\hft3\scripts\launch_chi404_vm_vnc.ps1'
$CurrentScript = (Resolve-Path -LiteralPath $MyInvocation.MyCommand.Path).Path
if ($CurrentScript -ne $CanonicalScript) {
    if (-not (Test-Path -LiteralPath $CanonicalScript)) {
        throw "Canonical hft3 VNC launcher missing: $CanonicalScript"
    }
    Write-Host "Redirecting to canonical hft3 repo: $CanonicalScript"
    & $CanonicalScript @PSBoundParameters
    exit $LASTEXITCODE
}
$RepoRoot = Split-Path -Parent (Split-Path -Parent $CurrentScript)
$ScriptPath = $CurrentScript
$TunnelStateDir = Join-Path $env:LOCALAPPDATA 'hft3\tunnels'

function Find-VncViewer {
    $candidates = @(
        (Get-Command vncviewer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "${env:ProgramFiles}\TigerVNC\vncviewer.exe",
        "${env:ProgramFiles(x86)}\TigerVNC\vncviewer.exe",
        "${env:ProgramFiles}\RealVNC\VNC Viewer\vncviewer.exe",
        "${env:ProgramFiles(x86)}\RealVNC\VNC Viewer\vncviewer.exe",
        "${env:ProgramFiles}\TightVNC\tvnviewer.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    $candidates | Select-Object -First 1
}

function Install-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnkPath = Join-Path $desktop 'CHI404 RTrader VM VNC.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($lnkPath)
    $shortcut.TargetPath = (Get-Command powershell.exe).Source
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.Description = 'SSH tunnel + VNC to hft3-rtrader-win on CHI404'
    $shortcut.IconLocation = "$env:SystemRoot\System32\mstsc.exe,0"
    $shortcut.Save()
    Write-Host "Desktop shortcut: $lnkPath"
}

function Get-PortListeners {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1', '0.0.0.0') }
}

function Get-TunnelMarkerPath {
    param(
        [int]$Port,
        [string]$HostAlias,
        [string]$Remote,
        [int]$RemoteP
    )
    $safe = "${HostAlias}_${Remote}_${RemoteP}_${Port}" -replace '[^A-Za-z0-9_.-]', '_'
    Join-Path $TunnelStateDir "chi404-vnc-${safe}.pid"
}

function Get-MarkedTunnelProcess {
    param(
        [int]$Port,
        [string]$HostAlias,
        [string]$Remote,
        [int]$RemoteP
    )
    $marker = Get-TunnelMarkerPath -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    if (-not (Test-Path -LiteralPath $marker)) { return $null }
    $raw = (Get-Content -LiteralPath $marker -ErrorAction SilentlyContinue | Select-Object -First 1)
    $procId = 0
    if (-not [int]::TryParse($raw, [ref]$procId)) { return $null }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -ieq 'ssh') { return $proc }
    return $null
}

function Get-TunnelListenerInfo {
    param(
        [int]$Port,
        [string]$HostAlias,
        [string]$Remote,
        [int]$RemoteP
    )
    $markedProc = Get-MarkedTunnelProcess -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    foreach ($listener in (Get-PortListeners -Port $Port)) {
        $proc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        $isExpected = [bool]($proc -and $markedProc -and $proc.Id -eq $markedProc.Id)
        [pscustomobject]@{
            Listener = $listener
            Process = $proc
            MarkerPath = Get-TunnelMarkerPath -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
            IsExpectedTunnel = $isExpected
        }
    }
}

function Test-PortListening {
    param([int]$Port)
    return [bool](Get-PortListeners -Port $Port)
}

function Stop-TunnelOnPort {
    param(
        [int]$Port,
        [string]$HostAlias,
        [string]$Remote,
        [int]$RemoteP
    )
    $marker = Get-TunnelMarkerPath -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    $markedProc = Get-MarkedTunnelProcess -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    if ($markedProc) {
        Write-Host "Stopping marked SSH tunnel (PID $($markedProc.Id)) for port $Port"
        Stop-Process -Id $markedProc.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $marker) {
        Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    }
    $listeners = Get-TunnelListenerInfo -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    foreach ($l in $listeners) {
        $proc = $l.Process
        if ($proc -and $l.IsExpectedTunnel) {
            Write-Host "Stopping expected SSH tunnel (PID $($proc.Id)) on port $Port"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        } elseif ($proc) {
            Write-Host "Not stopping unrelated listener $($proc.ProcessName) (PID $($proc.Id)) on port $Port"
        }
    }
    Start-Sleep -Seconds 1
}

function Test-TunnelReady {
    param(
        [int]$Port,
        [string]$HostAlias,
        [string]$Remote,
        [int]$RemoteP
    )
    $expected = Get-TunnelListenerInfo -Port $Port -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP |
        Where-Object { $_.IsExpectedTunnel }
    if (-not $expected) { return $false }
    try {
        $client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Get-SshExe {
    $ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if (-not $ssh) {
        $fallback = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
        if (Test-Path $fallback) { return $fallback }
        throw 'OpenSSH client not found. Install via Settings -> Apps -> Optional features -> OpenSSH Client.'
    }
    return $ssh.Source
}

function Start-SshTunnel {
    param([string]$HostAlias, [int]$Local, [string]$Remote, [int]$RemoteP)
    if (Test-PortListening -Port $Local) {
        Write-Host "Port $Local already in use - skipping new SSH tunnel."
        return
    }
    $sshExe = Get-SshExe
    $forward = "${Local}:${Remote}:${RemoteP}"
    New-Item -ItemType Directory -Force -Path $TunnelStateDir | Out-Null
    $marker = Get-TunnelMarkerPath -Port $Local -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    $sshArgs = @(
        '-N',
        '-o', 'ConnectTimeout=15',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=15',
        '-o', 'ServerAliveCountMax=2',
        '-L', $forward,
        $HostAlias
    )
    $proc = Start-Process -FilePath $sshExe -ArgumentList $sshArgs -PassThru
    Set-Content -LiteralPath $marker -Value ([string]$proc.Id) -Encoding ascii
}

function Ensure-Tunnel {
    param(
        [string]$HostAlias,
        [int]$Local,
        [string]$Remote,
        [int]$RemoteP,
        [bool]$Reset
    )
    if ($Reset) {
        Stop-TunnelOnPort -Port $Local -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP
    }
    if (Test-TunnelReady -Port $Local -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP) {
        Write-Host "Reusing tunnel on 127.0.0.1:${Local}"
        return
    }
    if (Test-PortListening -Port $Local) {
        Write-Host "Port ${Local} is in use but not accepting connections."
        Write-Host "Close the old tunnel window or run: .\scripts\launch_chi404_vm_vnc.ps1 -ResetTunnel"
        throw "Stale listener on port ${Local}."
    }
    Write-Host "Starting SSH tunnel: localhost:${Local} -> ${HostAlias}:${Remote}:${RemoteP}"
    Start-SshTunnel -HostAlias $HostAlias -Local $Local -Remote $Remote -RemoteP $RemoteP
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (Test-TunnelReady -Port $Local -HostAlias $HostAlias -Remote $Remote -RemoteP $RemoteP) { return }
        Start-Sleep -Milliseconds 400
    }
    throw ("Tunnel did not come up on 127.0.0.1:{0}. Check: ssh {1} 'virsh domstate hft3-rtrader-win'" -f $Local, $HostAlias)
}

if ($InstallDesktopShortcut) {
    Install-DesktopShortcut
    if (-not $TunnelOnly -and -not $ResetTunnel) { return }
}

if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'))) {
    throw 'OpenSSH client not found. Install via Settings -> Apps -> Optional features -> OpenSSH Client.'
}

try {
    Ensure-Tunnel -HostAlias $SshHost -Local $LocalPort -Remote $RemoteHost -RemoteP $RemotePort -Reset:$ResetTunnel

    if ($TunnelOnly) { return }

    $viewer = Find-VncViewer
    if (-not $viewer) {
        Write-Host @"

No VNC viewer found. Install TigerVNC Viewer, then run this script again:
  https://tigervnc.org/

Manual connect: 127.0.0.1:${LocalPort}
OOBE password (if prompted): set VM_ADMIN_PASSWORD on CHI404 (see .env.example)
"@
        exit 1
    }

    $target = "127.0.0.1:${LocalPort}"
    Write-Host "Opening VNC: $target"
    Start-Process -FilePath $viewer -ArgumentList $target
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    if ($Host.Name -eq 'ConsoleHost' -and $MyInvocation.Line -match 'Desktop') {
        Read-Host 'Press Enter to close'
    }
    exit 1
}
