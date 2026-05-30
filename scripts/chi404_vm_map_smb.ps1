# Map SMB watch share at logon (before R|Trader starts).
$ErrorActionPreference = "Continue"
$envFile = "C:\rtrader_smb.env"
if (-not (Test-Path $envFile)) { exit 1 }
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
$SmbHost = $env:RTRADER_SMB_HOST
$SmbUser = $env:RTRADER_SMB_USER
$SmbPass = $env:RTRADER_SMB_PASS
if (-not $SmbHost) { exit 1 }

$shareRoot = "\\$SmbHost\rtrader_watch"
$docs = "$env:USERPROFILE\Documents\Rithmic"

$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    if (Test-NetConnection $SmbHost -Port 445 -WarningAction SilentlyContinue | Where-Object { $_.TcpTestSucceeded }) { break }
    Start-Sleep -Seconds 5
}

cmdkey /add:$SmbHost /user:$SmbUser /pass:$SmbPass | Out-Null
net use $shareRoot /user:$SmbUser $SmbPass /persistent:yes 2>$null | Out-Null

$item = Get-Item $docs -Force -ErrorAction SilentlyContinue
$linkOk = $false
if ($item -and $item.LinkType -in @("Junction", "SymbolicLink")) {
    $target = ($item.Target -join "").ToLower()
    if ($target -like "*rtrader_watch*") { $linkOk = $true }
}
if (-not $linkOk) {
    if ($item) {
        Get-Process "Rithmic Trader Pro" -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 3
        if ($item.LinkType -notin @("Junction", "SymbolicLink")) {
            $bak = "$docs.bak_$(Get-Date -Format yyyyMMddHHmmss)"
            Move-Item $docs $bak -Force
        } else {
            Remove-Item $docs -Force -Recurse -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path $docs)) {
        cmd /c mklink /D "$docs" "$shareRoot"
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }
}

if (-not (Test-Path "R:\")) {
    net use R: /delete /y 2>$null | Out-Null
    cmd /c "net use R: `"$shareRoot`" /user:$SmbUser $SmbPass /persistent:yes" 2>$null | Out-Null
}

# Merge orphaned Rithmic.bak_* logs into symlink target (pre-symlink sessions).
if (Test-Path $docs) {
    Get-ChildItem "$env:USERPROFILE\Documents" -Directory -Filter 'Rithmic.bak_*' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | ForEach-Object {
            Get-ChildItem $_.FullName -File -ErrorAction SilentlyContinue | ForEach-Object {
                Copy-Item $_.FullName (Join-Path $docs $_.Name) -Force -ErrorAction SilentlyContinue
            }
        }
}
