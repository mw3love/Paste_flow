// PasteFlow 언어 비교 시험판 — Rust 쪽.
// 지금 앱(pasteflow/paste_interceptor.py · paste_queue.py)과 같은 규칙:
//   Ctrl+Shift+V → 그 키는 삼키고, 클립보드를 줄의 다음 항목으로 바꾼 뒤 깨끗한 Ctrl+V를 주입
//   평범한 Ctrl+V → 통과시키고 줄만 비움 · 자기가 쓴 클립보드는 새 복사로 치지 않음
// 로그(exe 옆 spike.log): READY <ms> · SWAP <받은 ms> <끝난 ms>

use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{mpsc, Mutex, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition};

use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{HANDLE, HGLOBAL, HINSTANCE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::DataExchange::{
    AddClipboardFormatListener, CloseClipboard, EmptyClipboard, GetClipboardData,
    GetClipboardSequenceNumber, OpenClipboard, SetClipboardData,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Memory::{GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE};
use windows::Win32::System::Ole::CF_UNICODETEXT;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_KEYUP, VIRTUAL_KEY, VK_CONTROL, VK_MENU, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW,
    PostThreadMessageW, RegisterClassW, SetWindowsHookExW, TranslateMessage, HWND_MESSAGE,
    KBDLLHOOKSTRUCT, MSG, WH_KEYBOARD_LL, WINDOW_EX_STYLE, WINDOW_STYLE,
    WM_APP, WM_CLIPBOARDUPDATE, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WNDCLASSW,
};

const VK_V: u32 = 0x56;
const VK_MASK: u16 = 0xE8; // 미할당 가상 키 — Ctrl+Shift 조합을 더럽혀 입력기 전환 팝업 방지
const IDLE_RESET_SECS: f64 = 10.0;
const WM_DO_SWAP: u32 = WM_APP + 1;
// 자기가 넣은 키 표식 — LLKHF_INJECTED 대신 이것으로 거른다(자동 시험의 주입 키는 받아야 해서)
const SELF_MARK: usize = 0x5350494B;

// ---------- 줄(큐) — pasteflow/paste_queue.py와 같은 규칙 ----------
struct Queue {
    items: Vec<String>,
    pointer: usize,
    last_copy: Option<Instant>,
}

impl Queue {
    const fn new() -> Self {
        Queue { items: Vec::new(), pointer: 0, last_copy: None }
    }
    fn add(&mut self, text: String) {
        let idle = self
            .last_copy
            .map(|t| t.elapsed().as_secs_f64() >= IDLE_RESET_SECS)
            .unwrap_or(false);
        if self.pointer > 0 || idle {
            self.items.clear();
        }
        self.items.push(text);
        self.pointer = 0;
        self.last_copy = Some(Instant::now());
    }
    fn has_next(&self) -> bool {
        self.pointer < self.items.len()
    }
    fn next(&mut self) -> Option<String> {
        if self.has_next() {
            let t = self.items[self.pointer].clone();
            self.pointer += 1;
            Some(t)
        } else {
            None
        }
    }
    fn mark_plain_paste(&mut self) {
        self.items.clear();
        self.pointer = 0;
        self.last_copy = None;
    }
}

static QUEUE: Mutex<Queue> = Mutex::new(Queue::new());
static APP: OnceLock<AppHandle> = OnceLock::new();
static HOOK_THREAD: AtomicU32 = AtomicU32::new(0);
static SUPPRESS_VK: AtomicU32 = AtomicU32::new(0);
static SELF_SEQ: AtomicU32 = AtomicU32::new(0);
static SWAP_T0: Mutex<u128> = Mutex::new(0);

fn now_ms() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
}

fn log_line(line: &str) {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(dir.join("spike.log")) {
                let _ = writeln!(f, "{line}");
            }
        }
    }
}

// ---------- 클립보드 ----------
unsafe fn open_clipboard_retry() -> bool {
    for _ in 0..5 {
        if OpenClipboard(HWND::default()).is_ok() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    false
}

unsafe fn read_clipboard_text() -> Option<String> {
    if !open_clipboard_retry() {
        return None;
    }
    let mut out = None;
    if let Ok(h) = GetClipboardData(CF_UNICODETEXT.0 as u32) {
        let hg = HGLOBAL(h.0);
        let p = GlobalLock(hg) as *const u16;
        if !p.is_null() {
            let mut len = 0usize;
            while *p.add(len) != 0 {
                len += 1;
            }
            out = Some(String::from_utf16_lossy(std::slice::from_raw_parts(p, len)));
            let _ = GlobalUnlock(hg);
        }
    }
    let _ = CloseClipboard();
    out
}

unsafe fn write_clipboard_text(text: &str) -> bool {
    let wide: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    if !open_clipboard_retry() {
        return false;
    }
    let mut ok = false;
    if EmptyClipboard().is_ok() {
        if let Ok(hg) = GlobalAlloc(GMEM_MOVEABLE, wide.len() * 2) {
            let p = GlobalLock(hg) as *mut u16;
            if !p.is_null() {
                std::ptr::copy_nonoverlapping(wide.as_ptr(), p, wide.len());
                let _ = GlobalUnlock(hg);
                ok = SetClipboardData(CF_UNICODETEXT.0 as u32, HANDLE(hg.0)).is_ok();
            }
        }
    }
    let _ = CloseClipboard();
    // 닫은 뒤의 순번이 우리 쓰기로 생긴 WM_CLIPBOARDUPDATE의 순번이다
    SELF_SEQ.store(GetClipboardSequenceNumber(), Ordering::SeqCst);
    ok
}

// ---------- 키 주입 — paste_interceptor.py의 _send_clean_key와 같은 순서 ----------
fn key(vk: u16, up: bool) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: 0,
                dwFlags: if up { KEYEVENTF_KEYUP } else { KEYBD_EVENT_FLAGS(0) },
                time: 0,
                dwExtraInfo: SELF_MARK,
            },
        },
    }
}

unsafe fn held(vk: VIRTUAL_KEY) -> bool {
    (GetAsyncKeyState(vk.0 as i32) as u16 & 0x8000) != 0
}

unsafe fn send_clean_ctrl_v() {
    let (c, s, a) = (held(VK_CONTROL), held(VK_SHIFT), held(VK_MENU));
    let mask = c && s;
    let mut v: Vec<INPUT> = Vec::new();
    if mask {
        v.push(key(VK_MASK, false));
        v.push(key(VK_MASK, true));
    }
    if a { v.push(key(VK_MENU.0, true)); }
    if s { v.push(key(VK_SHIFT.0, true)); }
    if c { v.push(key(VK_CONTROL.0, true)); }
    v.push(key(VK_CONTROL.0, false));
    v.push(key(VK_V as u16, false));
    v.push(key(VK_V as u16, true));
    v.push(key(VK_CONTROL.0, true));
    if c { v.push(key(VK_CONTROL.0, false)); }
    if s { v.push(key(VK_SHIFT.0, false)); }
    if a { v.push(key(VK_MENU.0, false)); }
    if mask {
        v.push(key(VK_MASK, false));
        v.push(key(VK_MASK, true));
    }
    SendInput(&v, std::mem::size_of::<INPUT>() as i32);
}

// ---------- 저수준 키보드 훅 ----------
unsafe extern "system" fn keyboard_proc(ncode: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if ncode >= 0 {
        let kb = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
        let vk = kb.vkCode;
        let msg = wparam.0 as u32;
        let injected = kb.dwExtraInfo == SELF_MARK; // 자기 주입만 거른다

        // 삼킨 키의 짝 keyup도 삼킨다
        if (msg == WM_KEYUP || msg == WM_SYSKEYUP) && vk != 0 && SUPPRESS_VK.load(Ordering::SeqCst) == vk {
            SUPPRESS_VK.store(0, Ordering::SeqCst);
            return LRESULT(1);
        }

        if (msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN) && !injected && vk == VK_V {
            let (c, s, a) = (held(VK_CONTROL), held(VK_SHIFT), held(VK_MENU));
            if c && s && !a {
                let has = QUEUE.lock().map(|q| q.has_next()).unwrap_or(false);
                if has {
                    if let Ok(mut t) = SWAP_T0.lock() { *t = now_ms(); }
                    // 무거운 일은 훅 밖에서 — 같은 스레드의 메시지 루프가 처리
                    let _ = PostThreadMessageW(HOOK_THREAD.load(Ordering::SeqCst), WM_DO_SWAP, WPARAM(0), LPARAM(0));
                    SUPPRESS_VK.store(vk, Ordering::SeqCst);
                    return LRESULT(1);
                }
                // 줄이 소진되면 개입하지 않는다
            } else if c && !s && !a {
                if let Ok(mut q) = QUEUE.lock() { q.mark_plain_paste(); }
            }
        }
    }
    CallNextHookEx(None, ncode, wparam, lparam)
}

// ---------- 클립보드 감시 창(메시지 전용) ----------
unsafe extern "system" fn listener_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if msg == WM_CLIPBOARDUPDATE {
        let seq = GetClipboardSequenceNumber();
        if seq != SELF_SEQ.load(Ordering::SeqCst) {
            if let Some(text) = read_clipboard_text() {
                let n = QUEUE.lock().map(|mut q| { q.add(text); q.items.len() }).unwrap_or(0);
                show_popup(n);
            }
        }
        return LRESULT(0);
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

fn show_popup(n: usize) {
    if let Some(app) = APP.get() {
        if let Some(win) = app.get_webview_window("popup") {
            let _ = win.emit("count", n);
            let _ = win.show();
        }
    }
}

fn do_swap() {
    let next = QUEUE.lock().ok().and_then(|mut q| q.next());
    if let Some(text) = next {
        unsafe {
            write_clipboard_text(&text);
            let t1 = now_ms();
            let t0 = SWAP_T0.lock().map(|t| *t).unwrap_or(0);
            log_line(&format!("SWAP {t0} {t1}"));
            send_clean_ctrl_v();
        }
    }
}

fn hook_thread(ready: mpsc::Sender<bool>) {
    unsafe {
        HOOK_THREAD.store(GetCurrentThreadId(), Ordering::SeqCst);
        let hmod = GetModuleHandleW(PCWSTR::null()).unwrap_or_default();
        let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_proc), HINSTANCE(hmod.0), 0);

        let class = w!("SpikeClipListener");
        let wc = WNDCLASSW { lpfnWndProc: Some(listener_proc), hInstance: HINSTANCE(hmod.0), lpszClassName: class, ..Default::default() };
        RegisterClassW(&wc);
        let hwnd = CreateWindowExW(WINDOW_EX_STYLE(0), class, w!(""), WINDOW_STYLE(0), 0, 0, 0, 0, HWND_MESSAGE, None, HINSTANCE(hmod.0), None);
        let listening = match hwnd {
            Ok(h) => AddClipboardFormatListener(h).is_ok(),
            Err(_) => false,
        };
        let _ = ready.send(hook.is_ok() && listening);

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, HWND::default(), 0, 0).as_bool() {
            if msg.hwnd.0.is_null() && msg.message == WM_DO_SWAP {
                do_swap();
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let _ = APP.set(app.handle().clone());

            // 작은 창을 오른쪽 아래로
            if let Some(win) = app.get_webview_window("popup") {
                if let Ok(Some(m)) = win.primary_monitor() {
                    let s = m.size();
                    let _ = win.set_position(PhysicalPosition::new(s.width as i32 - 200, s.height as i32 - 120));
                }
            }

            let icon = app.default_window_icon().cloned().expect("기본 아이콘");
            TrayIconBuilder::new().icon(icon).tooltip("spike-tauri").build(app)?;

            let (tx, rx) = mpsc::channel();
            std::thread::spawn(move || hook_thread(tx));
            let ok = rx.recv().unwrap_or(false);
            log_line(&format!("READY {} hook_ok={}", now_ms(), ok));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
