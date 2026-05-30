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
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = $MyInvocation.MyCommand.Path

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
    $shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""
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

function Test-PortListening {
    param([int]$Port)
    return [bool](Get-PortListeners -Port $Port)
}

function Stop-TunnelOnPort {
    param([int]$Port)
    $listeners = Get-PortListeners -Port $Port
    foreach ($l in $listeners) {
        $proc = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -match '^(ssh|wsl|WindowsTerminal)$') {
            Write-Host "Stopping $($proc.ProcessName) (PID $($proc.Id)) on port $Port"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 1
}

function Test-TunnelReady {
    param([int]$Port)
    if (-not (Test-PortListening -Port $Port)) { return $false }
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
    $tunnelTitle = 'CHI404 VNC tunnel (keep this window open)'
    # Launch ssh directly (avoid wt.exe --title parsing bug 0x80070002).
    $argList = "-NoExit", "-NoProfile", "-Command", "`$Host.UI.RawUI.WindowTitle = '$tunnelTitle'; & '$sshExe' -N -L $forward $HostAlias; if (`$LASTEXITCODE -ne 0) { Read-Host 'Tunnel failed - press Enter' }"
    Start-Process -FilePath (Get-Command powershell.exe).Source -ArgumentList $argList
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
        Stop-TunnelOnPort -Port $Local
    }
    if (Test-TunnelReady -Port $Local) {
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
        if (Test-TunnelReady -Port $Local) { return }
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
