# Opens SSH session to CHI404 bare-metal host (uses ~/.ssh/config Host chi404)
$ErrorActionPreference = 'Stop'
if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    Start-Process wt.exe -ArgumentList 'ssh', 'chi404'
} else {
    Start-Process ssh.exe -ArgumentList 'chi404' -Wait
}
