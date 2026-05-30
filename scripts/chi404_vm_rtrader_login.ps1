# Headless R|Trader login via Win32 WM_SETTEXT (no SendKeys password typing).
$ErrorActionPreference = "Stop"
$SessionFile = "C:\chi404_rtrader_session.json"

function Write-SessionState {
    param([string]$WindowTitle, [bool]$LoggedInGuess, [string]$Note = "")
    $payload = @{
        window_title = $WindowTitle
        logged_in_guess = $LoggedInGuess
        ts = (Get-Date).ToUniversalTime().ToString("o")
        note = $Note
    }
    $payload | ConvertTo-Json | Set-Content -Path $SessionFile -Encoding UTF8
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;

public class RtraderUi {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hWnd, EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    public const uint WM_SETTEXT = 0x000C;
    public const uint BM_CLICK = 0x00F5;
    public const uint WM_KEYDOWN = 0x0100;
    public const uint WM_KEYUP = 0x0101;
    public const int VK_RETURN = 0x0D;

    public static IntPtr FindRtraderWindow() {
        IntPtr found = IntPtr.Zero;
        EnumWindows((hWnd, lParam) => {
            if (!IsWindowVisible(hWnd)) return true;
            var sb = new StringBuilder(512);
            GetWindowText(hWnd, sb, sb.Capacity);
            string title = sb.ToString();
            if (title.IndexOf("Rithmic", StringComparison.OrdinalIgnoreCase) >= 0) {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static List<IntPtr> CollectEdits(IntPtr root) {
        var edits = new List<IntPtr>();
        EnumChildWindows(root, (hWnd, lParam) => {
            var cls = new StringBuilder(256);
            GetClassName(hWnd, cls, cls.Capacity);
            if (cls.ToString().Equals("Edit", StringComparison.OrdinalIgnoreCase)) {
                edits.Add(hWnd);
            }
            return true;
        }, IntPtr.Zero);
        return edits;
    }

    public static List<IntPtr> CollectButtons(IntPtr root) {
        var buttons = new List<IntPtr>();
        EnumChildWindows(root, (hWnd, lParam) => {
            var cls = new StringBuilder(256);
            GetClassName(hWnd, cls, cls.Capacity);
            if (cls.ToString().Equals("Button", StringComparison.OrdinalIgnoreCase)) {
                buttons.Add(hWnd);
            }
            return true;
        }, IntPtr.Zero);
        return buttons;
    }

    public static string GetWindowTitle(IntPtr hWnd) {
        var sb = new StringBuilder(512);
        GetWindowText(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static void SetEditText(IntPtr edit, string text) {
        SendMessage(edit, WM_SETTEXT, IntPtr.Zero, text ?? "");
    }

    public static void ClickButton(IntPtr btn) {
        SendMessage(btn, BM_CLICK, IntPtr.Zero, null);
    }

    public static void PressEnter(IntPtr hWnd) {
        PostMessage(hWnd, WM_KEYDOWN, (IntPtr)VK_RETURN, IntPtr.Zero);
        PostMessage(hWnd, WM_KEYUP, (IntPtr)VK_RETURN, IntPtr.Zero);
    }
}
"@

$envFile = "C:\rithmic_login.env"
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile - run infrastructure/chi404/10_rtrader_smb_share.sh on CHI404"
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
$user = $env:RITHMIC_USERNAME
$pass = $env:RITHMIC_PASSWORD
if (-not $user -or -not $pass) { throw "Missing RITHMIC_USERNAME/PASSWORD in $envFile" }

$uiEnv = $env:RITHMIC_UI_ENVIRONMENT
if (-not $uiEnv) {
    if ($env:RITHMIC_ENVIRONMENT -match 'Paper') { $uiEnv = 'Paper' }
    elseif ($env:RITHMIC_ENVIRONMENT) { $uiEnv = $env:RITHMIC_ENVIRONMENT }
    else { $uiEnv = 'Paper' }
}
$uiGw = if ($env:RITHMIC_GATEWAY) { $env:RITHMIC_GATEWAY } else { 'Chicago' }

function Test-LoggedInTitle {
    param([string]$Title)
    if (-not $Title) { return $false }
    if ($Title -match 'Login|Sign In|waiting for price history') { return $false }
    return $Title -match 'Rithmic'
}

function Invoke-RtraderLoginAttempt {
    $hwnd = [RtraderUi]::FindRtraderWindow()
    if ($hwnd -eq [IntPtr]::Zero) { return $false }
    [void][RtraderUi]::SetForegroundWindow($hwnd)
    $title = [RtraderUi]::GetWindowTitle($hwnd)
    Write-Output "Found window: $title"

    if (Test-LoggedInTitle $title) {
        Write-SessionState -WindowTitle $title -LoggedInGuess $true -Note "already_logged_in"
        return $true
    }

    $edits = [RtraderUi]::CollectEdits($hwnd)
    if ($edits.Count -lt 2) {
        Write-Output "Login edits not ready (count=$($edits.Count))"
        return $false
    }

    [RtraderUi]::SetEditText($edits[0], $user)
    [RtraderUi]::SetEditText($edits[1], $pass)
    if ($edits.Count -ge 3) { [RtraderUi]::SetEditText($edits[2], $uiEnv) }
    if ($edits.Count -ge 4) { [RtraderUi]::SetEditText($edits[3], $uiGw) }

    $buttons = [RtraderUi]::CollectButtons($hwnd)
    $clicked = $false
    foreach ($btn in $buttons) {
        $sb = New-Object System.Text.StringBuilder 256
        [RtraderUi]::GetWindowText($btn, $sb, $sb.Capacity) | Out-Null
        $label = $sb.ToString()
        if ($label -match 'Log|Connect|OK|Submit') {
            [RtraderUi]::ClickButton($btn)
            $clicked = $true
            break
        }
    }
    if (-not $clicked) {
        [RtraderUi]::PressEnter($hwnd)
    }
    Start-Sleep -Seconds 15
    $hwnd2 = [RtraderUi]::FindRtraderWindow()
    $title2 = [RtraderUi]::GetWindowTitle($hwnd2)
    $ok = Test-LoggedInTitle $title2
    Write-SessionState -WindowTitle $title2 -LoggedInGuess $ok -Note "login_attempt"
    return $ok
}

$deadline = (Get-Date).AddMinutes(5)
$hwnd = [IntPtr]::Zero
while ((Get-Date) -lt $deadline -and $hwnd -eq [IntPtr]::Zero) {
    $hwnd = [RtraderUi]::FindRtraderWindow()
    if ($hwnd -eq [IntPtr]::Zero) { Start-Sleep -Seconds 5 }
}
if ($hwnd -eq [IntPtr]::Zero) {
    Write-SessionState -WindowTitle "" -LoggedInGuess $false -Note "window_not_found"
    throw "R|Trader window not found after 10m"
}

$success = $false
for ($i = 1; $i -le 3; $i++) {
    Write-Output "Login attempt $i/3"
    if (Invoke-RtraderLoginAttempt) {
        $success = $true
        break
    }
    Start-Sleep -Seconds 60
}

if (-not $success) {
    $hwnd = [RtraderUi]::FindRtraderWindow()
    $title = [RtraderUi]::GetWindowTitle($hwnd)
    Write-SessionState -WindowTitle $title -LoggedInGuess $false -Note "login_failed"
    throw "R|Trader login failed after 3 attempts (title=$title)"
}

Write-Output "Login succeeded ($uiEnv / $uiGw)"
