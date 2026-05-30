# Shared R|Trader Win32 helpers for CHI404 VM UI automation (login, subscribe, paper orders).
$ErrorActionPreference = "Stop"

$Script:RtraderExportDenyNames = @(
    'sweep_manifest.json',
    'rithmic_trial_export.log',
    'rithmic_trial_smoke_export.log'
)

$Script:RtraderExportDenyPrefixes = @(
    'paper_sweep',
    'paper_roundtrip'
)

function Test-RtraderExportFile {
    param([string]$Name)
    $lower = $Name.ToLower()
    if ($Script:RtraderExportDenyNames -contains $lower) { return $false }
    foreach ($pfx in $Script:RtraderExportDenyPrefixes) {
        if ($lower.StartsWith($pfx)) { return $false }
    }
    if ($lower -like '*.cur.txt') { return $true }
    if ($lower -like '*.log') { return $true }
    return $false
}

function Test-RtraderOrderExportLine {
    param([string]$Line)
    if (-not $Line) { return $false }
    if ($Line -match 'SWEEP-|MKT-\d') { return $false }
    return $Line -match '(?i)(order_submit|,ack,|,ACK,|,Trade,|,Fill,|,submit,)'
}

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class RtraderUi {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hWnd, EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    public const uint WM_SETTEXT = 0x000C;
    public const uint BM_CLICK = 0x00F5;
    public const uint WM_KEYDOWN = 0x0100;
    public const uint WM_KEYUP = 0x0101;
    public const int VK_RETURN = 0x0D;
    public const int SW_RESTORE = 9;
    public const int SW_SHOW = 5;

    public static string GetWindowTitle(IntPtr hWnd) {
        var sb = new StringBuilder(512);
        GetWindowText(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static IntPtr FindByTitle(bool requireVisible) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((hWnd, lParam) => {
            if (requireVisible && !IsWindowVisible(hWnd)) return true;
            var title = GetWindowTitle(hWnd);
            if (title.IndexOf("Rithmic", StringComparison.OrdinalIgnoreCase) >= 0) {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static IntPtr FindForProcess(int pid) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((hWnd, lParam) => {
            uint winPid;
            GetWindowThreadProcessId(hWnd, out winPid);
            if (winPid != pid) return true;
            var title = GetWindowTitle(hWnd);
            if (title.IndexOf("Rithmic", StringComparison.OrdinalIgnoreCase) >= 0) {
                found = hWnd;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static IntPtr FindRtraderWindow() {
        var hwnd = FindByTitle(true);
        if (hwnd != IntPtr.Zero) return hwnd;
        hwnd = FindByTitle(false);
        if (hwnd != IntPtr.Zero) return hwnd;
        var procs = System.Diagnostics.Process.GetProcessesByName("Rithmic Trader Pro");
        foreach (var p in procs) {
            if (p.MainWindowHandle != IntPtr.Zero) {
                var title = GetWindowTitle(p.MainWindowHandle);
                if (title.IndexOf("Rithmic", StringComparison.OrdinalIgnoreCase) >= 0) {
                    return p.MainWindowHandle;
                }
            }
            hwnd = FindForProcess(p.Id);
            if (hwnd != IntPtr.Zero) return hwnd;
        }
        return IntPtr.Zero;
    }

    public static void FocusWindow(IntPtr hWnd) {
        ShowWindow(hWnd, SW_RESTORE);
        ShowWindow(hWnd, SW_SHOW);
        SetForegroundWindow(hWnd);
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

function Get-RtraderWindow {
    return [RtraderUi]::FindRtraderWindow()
}

function Focus-RtraderWindow {
    $hwnd = Get-RtraderWindow
    if ($hwnd -eq [IntPtr]::Zero) {
        throw "R|Trader window not found"
    }
    [void][RtraderUi]::FocusWindow($hwnd)
    Start-Sleep -Milliseconds 500
    return $hwnd
}

function Test-RtraderLoggedIn {
    param([string]$Title = "")
    if (-not $Title) {
        $hwnd = Get-RtraderWindow
        if ($hwnd -eq [IntPtr]::Zero) { return $false }
        $Title = [RtraderUi]::GetWindowTitle($hwnd)
    }
    if (-not $Title) { return $false }
    if ($Title -match 'Login|Sign In|waiting for price history') { return $false }
    return $Title -match 'Rithmic'
}

function Get-RtraderLogDir {
    return "$env:USERPROFILE\Documents\Rithmic"
}

function Get-RtraderExportOffsets {
    $dir = Get-RtraderLogDir
    $offsets = @{}
    if (-not (Test-Path $dir)) { return $offsets }
    Get-ChildItem $dir -File -ErrorAction SilentlyContinue | ForEach-Object {
        if (Test-RtraderExportFile $_.Name) {
            $offsets[$_.Name] = $_.Length
        }
    }
    return $offsets
}

function Wait-RtraderOrderExportLine {
    param(
        [hashtable]$BeforeOffsets,
        [int]$TimeoutSec = 45
    )
    $dir = Get-RtraderLogDir
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $dir) {
            foreach ($file in Get-ChildItem $dir -File -ErrorAction SilentlyContinue) {
                if (-not (Test-RtraderExportFile $file.Name)) { continue }
                $prev = 0
                if ($BeforeOffsets.ContainsKey($file.Name)) { $prev = [int]$BeforeOffsets[$file.Name] }
                if ($file.Length -le $prev) { continue }
                try {
                    $fs = [System.IO.File]::Open($file.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                    $fs.Seek($prev, [System.IO.SeekOrigin]::Begin) | Out-Null
                    $buf = New-Object byte[] ($file.Length - $prev)
                    [void]$fs.Read($buf, 0, $buf.Length)
                    $fs.Close()
                    $chunk = [System.Text.Encoding]::UTF8.GetString($buf)
                    foreach ($line in ($chunk -split "`n")) {
                        if (Test-RtraderOrderExportLine $line) {
                            $trim = $line.Trim()
                            return @{
                                ok   = $true
                                file = $file.Name
                                line = $trim.Substring(0, [Math]::Min(120, $trim.Length))
                            }
                        }
                    }
                } catch {
                    continue
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return @{ ok = $false }
}
