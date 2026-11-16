// PasteFlow 언어 비교 시험판 — C#(.NET 10 + WPF).
// 지금 앱(pasteflow/paste_interceptor.py · paste_queue.py)과 같은 규칙:
//   Ctrl+Shift+V → 그 키는 삼키고, 클립보드를 줄의 다음 항목으로 바꾼 뒤 깨끗한 Ctrl+V를 주입
//   평범한 Ctrl+V → 통과시키고 줄만 비움 · 자기가 쓴 클립보드는 새 복사로 치지 않음
// 로그(exe 옆 spike.log): READY <ms> · SWAP <받은 ms> <끝난 ms>

using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Threading;
using WpfApp = System.Windows.Application;
using WpfLabel = System.Windows.Controls.TextBlock;

namespace SpikeCs;

static class Log
{
    static readonly string PathLog = System.IO.Path.Combine(AppContext.BaseDirectory, "spike.log");
    public static long NowMs() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    public static void Line(string s) { try { File.AppendAllText(PathLog, s + Environment.NewLine); } catch { } }
}

// pasteflow/paste_queue.py와 같은 규칙
sealed class PasteQueue
{
    const double IdleResetSec = 10.0;
    readonly List<string> _items = new();
    int _pointer;
    DateTime? _lastCopy;

    public int Count => _items.Count;
    public bool HasNext => _pointer < _items.Count;

    public void Add(string text)
    {
        bool idle = _lastCopy is DateTime t && (DateTime.UtcNow - t).TotalSeconds >= IdleResetSec;
        if (_pointer > 0 || idle) _items.Clear();
        _items.Add(text);
        _pointer = 0;
        _lastCopy = DateTime.UtcNow;
    }
    public string? Next() => HasNext ? _items[_pointer++] : null;
    public void MarkPlainPaste() { _items.Clear(); _pointer = 0; _lastCopy = null; }
}

static class Native
{
    public const int WH_KEYBOARD_LL = 13;
    public const int WM_KEYDOWN = 0x0100, WM_KEYUP = 0x0101, WM_SYSKEYDOWN = 0x0104, WM_SYSKEYUP = 0x0105;
    public const int WM_CLIPBOARDUPDATE = 0x031D;
    public const uint LLKHF_INJECTED = 0x10;
    public const uint CF_UNICODETEXT = 13;
    public const uint GMEM_MOVEABLE = 0x0002;
    public const ushort VK_CONTROL = 0x11, VK_SHIFT = 0x10, VK_MENU = 0x12, VK_V = 0x56, VK_MASK = 0xE8;
    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const int GWL_EXSTYLE = -20;
    // 자기가 넣은 키 표식 — LLKHF_INJECTED 대신 이것으로 거른다(자동 시험의 주입 키는 받아야 해서)
    public static readonly IntPtr SelfMark = new IntPtr(0x5350494B);
    public const long WS_EX_NOACTIVATE = 0x08000000L, WS_EX_TOOLWINDOW = 0x00000080L, WS_EX_TOPMOST = 0x00000008L;

    public delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct KBDLLHOOKSTRUCT { public uint vkCode, scanCode, flags, time; public IntPtr dwExtraInfo; }

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Explicit)]
    public struct InputUnion { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public InputUnion U; }

    [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr SetWindowsHookEx(int idHook, HookProc lpfn, IntPtr hMod, uint dwThreadId);
    [DllImport("user32.dll")] public static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr GetModuleHandle(string? name);
    [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vKey);
    [DllImport("user32.dll", SetLastError = true)] public static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll", SetLastError = true)] public static extern bool OpenClipboard(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool CloseClipboard();
    [DllImport("user32.dll")] public static extern bool EmptyClipboard();
    [DllImport("user32.dll")] public static extern IntPtr SetClipboardData(uint fmt, IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetClipboardData(uint fmt);
    [DllImport("user32.dll")] public static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll", SetLastError = true)] public static extern bool AddClipboardFormatListener(IntPtr hwnd);
    [DllImport("kernel32.dll")] public static extern IntPtr GlobalAlloc(uint flags, UIntPtr bytes);
    [DllImport("kernel32.dll")] public static extern IntPtr GlobalLock(IntPtr h);
    [DllImport("kernel32.dll")] public static extern bool GlobalUnlock(IntPtr h);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] public static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int idx);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] public static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int idx, IntPtr v);
}

sealed class Spike
{
    readonly PasteQueue _queue = new();
    readonly Native.HookProc _proc; // GC가 대리자를 치우지 않게 붙잡아 둔다
    IntPtr _hook;
    uint _selfSeq;
    uint _suppressVk;
    long _swapT0;
    Window? _popup;
    WpfLabel? _label;
    System.Windows.Forms.NotifyIcon? _tray;

    public Spike() { _proc = HookCallback; }

    public void Start()
    {
        // 클립보드 감시: 메시지 전용 창
        var p = new HwndSourceParameters("SpikeClipListener") { ParentWindow = new IntPtr(-3), Width = 0, Height = 0 };
        var src = new HwndSource(p);
        src.AddHook(WndProc);
        bool listening = Native.AddClipboardFormatListener(src.Handle);

        _hook = Native.SetWindowsHookEx(Native.WH_KEYBOARD_LL, _proc, Native.GetModuleHandle(null), 0);

        BuildPopup();

        _tray = new System.Windows.Forms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Application,
            Text = "spike-cs",
            Visible = true,
        };
        Log.Line($"READY {Log.NowMs()} hook_ok={(_hook != IntPtr.Zero && listening)}");
    }

    void BuildPopup()
    {
        _label = new WpfLabel
        {
            Text = "줄 0개",
            Foreground = new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(0xcd, 0xd6, 0xf4)),
            HorizontalAlignment = System.Windows.HorizontalAlignment.Center,
            VerticalAlignment = System.Windows.VerticalAlignment.Center,
            FontSize = 13,
        };
        _popup = new Window
        {
            Width = 160, Height = 44,
            WindowStyle = WindowStyle.None, ResizeMode = ResizeMode.NoResize,
            ShowInTaskbar = false, Topmost = true, ShowActivated = false,
            Background = new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(0x1e, 0x1e, 0x2e)),
            Content = _label,
            Left = SystemParameters.WorkArea.Right - 180,
            Top = SystemParameters.WorkArea.Bottom - 64,
        };
        _popup.SourceInitialized += (_, _) =>
        {
            var h = new WindowInteropHelper(_popup).Handle;
            long ex = Native.GetWindowLongPtr(h, Native.GWL_EXSTYLE).ToInt64();
            Native.SetWindowLongPtr(h, Native.GWL_EXSTYLE, new IntPtr(ex | Native.WS_EX_NOACTIVATE | Native.WS_EX_TOOLWINDOW | Native.WS_EX_TOPMOST));
        };
    }

    IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == Native.WM_CLIPBOARDUPDATE)
        {
            handled = true;
            if (Native.GetClipboardSequenceNumber() != _selfSeq)
            {
                string? text = ReadClipboardText();
                if (text != null)
                {
                    _queue.Add(text);
                    if (_label != null) _label.Text = $"줄 {_queue.Count}개";
                    _popup?.Show();
                }
            }
        }
        return IntPtr.Zero;
    }

    static bool OpenClipboardRetry()
    {
        for (int i = 0; i < 5; i++)
        {
            if (Native.OpenClipboard(IntPtr.Zero)) return true;
            System.Threading.Thread.Sleep(10);
        }
        return false;
    }

    static string? ReadClipboardText()
    {
        if (!OpenClipboardRetry()) return null;
        try
        {
            IntPtr h = Native.GetClipboardData(Native.CF_UNICODETEXT);
            if (h == IntPtr.Zero) return null;
            IntPtr p = Native.GlobalLock(h);
            if (p == IntPtr.Zero) return null;
            try { return Marshal.PtrToStringUni(p); }
            finally { Native.GlobalUnlock(h); }
        }
        finally { Native.CloseClipboard(); }
    }

    void WriteClipboardText(string text)
    {
        if (!OpenClipboardRetry()) return;
        try
        {
            Native.EmptyClipboard();
            byte[] bytes = System.Text.Encoding.Unicode.GetBytes(text + "\0");
            IntPtr h = Native.GlobalAlloc(Native.GMEM_MOVEABLE, (UIntPtr)bytes.Length);
            IntPtr p = Native.GlobalLock(h);
            Marshal.Copy(bytes, 0, p, bytes.Length);
            Native.GlobalUnlock(h);
            Native.SetClipboardData(Native.CF_UNICODETEXT, h);
        }
        finally
        {
            Native.CloseClipboard();
            _selfSeq = Native.GetClipboardSequenceNumber(); // 우리 쓰기로 생긴 알림의 순번
        }
    }

    static bool Held(ushort vk) => (Native.GetAsyncKeyState(vk) & 0x8000) != 0;

    static Native.INPUT Key(ushort vk, bool up) => new()
    {
        type = 1,
        U = new Native.InputUnion { ki = new Native.KEYBDINPUT { wVk = vk, dwFlags = up ? Native.KEYEVENTF_KEYUP : 0, dwExtraInfo = Native.SelfMark } },
    };

    // paste_interceptor.py의 _send_clean_key와 같은 순서
    static void SendCleanCtrlV()
    {
        bool c = Held(Native.VK_CONTROL), s = Held(Native.VK_SHIFT), a = Held(Native.VK_MENU);
        bool mask = c && s;
        var v = new List<Native.INPUT>();
        if (mask) { v.Add(Key(Native.VK_MASK, false)); v.Add(Key(Native.VK_MASK, true)); }
        if (a) v.Add(Key(Native.VK_MENU, true));
        if (s) v.Add(Key(Native.VK_SHIFT, true));
        if (c) v.Add(Key(Native.VK_CONTROL, true));
        v.Add(Key(Native.VK_CONTROL, false));
        v.Add(Key(Native.VK_V, false));
        v.Add(Key(Native.VK_V, true));
        v.Add(Key(Native.VK_CONTROL, true));
        if (c) v.Add(Key(Native.VK_CONTROL, false));
        if (s) v.Add(Key(Native.VK_SHIFT, false));
        if (a) v.Add(Key(Native.VK_MENU, false));
        if (mask) { v.Add(Key(Native.VK_MASK, false)); v.Add(Key(Native.VK_MASK, true)); }
        Native.SendInput((uint)v.Count, v.ToArray(), Marshal.SizeOf<Native.INPUT>());
    }

    void DoSwap()
    {
        string? text = _queue.Next();
        if (text == null) return;
        WriteClipboardText(text);
        Log.Line($"SWAP {_swapT0} {Log.NowMs()}");
        SendCleanCtrlV();
    }

    IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0)
        {
            var kb = Marshal.PtrToStructure<Native.KBDLLHOOKSTRUCT>(lParam);
            int msg = wParam.ToInt32();
            bool injected = kb.dwExtraInfo == Native.SelfMark; // 자기 주입만 거른다

            if ((msg == Native.WM_KEYUP || msg == Native.WM_SYSKEYUP) && kb.vkCode != 0 && kb.vkCode == _suppressVk)
            {
                _suppressVk = 0;
                return new IntPtr(1);
            }

            if ((msg == Native.WM_KEYDOWN || msg == Native.WM_SYSKEYDOWN) && !injected && kb.vkCode == Native.VK_V)
            {
                bool c = Held(Native.VK_CONTROL), s = Held(Native.VK_SHIFT), a = Held(Native.VK_MENU);
                if (c && s && !a)
                {
                    if (_queue.HasNext)
                    {
                        _swapT0 = Log.NowMs();
                        // 무거운 일은 훅 밖에서 — 같은 스레드의 디스패처가 훅이 돌아간 뒤 처리
                        Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Send, new Action(DoSwap));
                        _suppressVk = kb.vkCode;
                        return new IntPtr(1);
                    }
                    // 줄이 소진되면 개입하지 않는다
                }
                else if (c && !s && !a)
                {
                    _queue.MarkPlainPaste();
                }
            }
        }
        return Native.CallNextHookEx(_hook, nCode, wParam, lParam);
    }
}

static class Program
{
    [STAThread]
    static void Main()
    {
        var app = new WpfApp { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var spike = new Spike();
        app.Startup += (_, _) => spike.Start();
        app.Run();
    }
}
