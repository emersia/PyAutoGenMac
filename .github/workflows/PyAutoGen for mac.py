import time
import os
import re
import ctypes
from ctypes import wintypes
import threading
import customtkinter as ctk
from tkinter import filedialog
from pynput import keyboard, mouse

# ==============================================================================
# CONFIGURATIONS & THEME ENVIRONMENT
# ==============================================================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Win32 Low-Level Constants for Trackpad Capture
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WH_MOUSE_LL = 14

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

# ==============================================================================
# PREMIUM STATE RECORDING ENGINE (LOGIC LAYER)
# ==============================================================================
class MacroRecorder:
    """Records raw hardware states (Down/Up/Move/Scroll) to mirror human interactions."""
    
    def __init__(self, on_toggle_callback):
        self.on_toggle_callback = on_toggle_callback
        self.is_recording = False
        self.start_time = None
        self.recorded_events = []
        self.currently_pressed = set()
        self.win32_hook = None
        
        self.KEY_MAPPING = {
            "cmd": "win", "cmd_l": "win", "cmd_r": "win",
            "alt_l": "alt", "alt_r": "alt",
            "ctrl_l": "ctrl", "ctrl_r": "ctrl",
            "shift_l": "shift", "shift_r": "shift"
        }
        
    def start(self, hotkey_char):
        self.hotkey_char = hotkey_char
        self.is_recording = True
        self.start_time = time.time()
        self.recorded_events.clear()
        self.currently_pressed.clear()
        self.start_win32_scroll_hook()

    def stop(self):
        self.is_recording = False
        self.stop_win32_scroll_hook()
        for remaining_key in list(self.currently_pressed):
            self.recorded_events.append(("key_up", remaining_key, time.time()))
        self.currently_pressed.clear()
        return self.recorded_events

    def map_key_name(self, key):
        try:
            key_name = key.char
            if key_name is None: return None
            if ord(key_name) < 32: 
                key_name = chr(ord(key_name) + 96)
            return key_name.lower()
        except AttributeError:
            raw_str = str(key).replace("Key.", "")
            return self.KEY_MAPPING.get(raw_str, raw_str).lower()

    def process_press(self, key):
        key_name = self.map_key_name(key)
        if not key_name or key_name in self.currently_pressed: 
            return 

        self.currently_pressed.add(key_name)
        self.recorded_events.append(("key_down", key_name, time.time()))

    def process_release(self, key):
        key_name = self.map_key_name(key)
        if not key_name: return

        if key_name in self.currently_pressed:
            self.currently_pressed.remove(key_name)
        self.recorded_events.append(("key_up", key_name, time.time()))

    def process_click(self, x, y, button, pressed):
        btn_name = str(button).replace("Button.", "").lower()
        if pressed:
            self.recorded_events.append(("mouse_down", (x, y, btn_name), time.time()))
        else:
            self.recorded_events.append(("mouse_up", (x, y, btn_name), time.time()))

    def process_move(self, x, y):
        self.recorded_events.append(("mouse_move", (x, y), time.time()))

    # --- WIN32 DIRECT KERNEL LISTENERS ---
    def start_win32_scroll_hook(self):
        """Spawns an isolated background thread to listen directly to OS message streams."""
        def hook_thread():
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            def hook_callback(nCode, wParam, lParam):
                if nCode >= 0 and self.is_recording:
                    if wParam in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                        data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        # Extract rotation delta direction
                        raw_delta = ctypes.c_short(data.mouseData >> 16).value
                        clicks = raw_delta / 120.0
                        
                        # Grab fresh real-time coordinates
                        x, y = data.pt.x, data.pt.y
                        
                        if wParam == WM_MOUSEWHEEL:
                            self.recorded_events.append(("mouse_scroll", (x, y, 0, clicks), time.time()))
                        else:
                            self.recorded_events.append(("mouse_scroll", (x, y, clicks, 0), time.time()))
                            
                return user32.CallNextHookEx(self.win32_hook, nCode, wParam, lParam)

            CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            self.pointer_callback = CMPFUNC(hook_callback)
            
            self.win32_hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, 
                self.pointer_callback, 
                kernel32.GetModuleHandleW(None), 
                0
            )
            
            msg = wintypes.MSG()
            while self.is_recording and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        self.t = threading.Thread(target=hook_thread)
        self.t.daemon = True
        self.t.start()

    def stop_win32_scroll_hook(self):
        if self.win32_hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self.win32_hook)
            self.win32_hook = None


# ==============================================================================
# GRAPHICAL INTERFACE ENGINE (PRESENTATION LAYER)
# ==============================================================================
class PyAutoGenApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.hotkey_char = "`"
        self.current_mode = "smart" 
        self.is_rebinding = False
        
        self.global_save_dir = os.path.dirname(os.path.abspath(__file__))
        self.custom_save_dir = os.path.dirname(os.path.abspath(__file__))
        self.email_address = "emersiaevanoelvos@gmail.com"
        
        self.recorder = MacroRecorder(self.toggle_recording)
        
        self.title("PyAutoGen")
        self.geometry("760x560")
        self.resizable(False, False)
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.build_sidebar_navigation()
        self.build_content_frames()
        self.initialize_hardware_hooks()
        self.show_dashboard()

    def build_sidebar_navigation(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=190, corner_radius=0, fg_color="#111215")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(5, weight=1)
        
        self.logo = ctk.CTkLabel(self.sidebar_frame, text="PyAutoGen", font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"), text_color="#24a0ed")
        self.logo.grid(row=0, column=0, padx=25, pady=(30, 20))
        
        self.nav_dash = ctk.CTkButton(self.sidebar_frame, text="💻 Dashboard", font=ctk.CTkFont(size=13, weight="bold"), height=40, corner_radius=8, fg_color="#1f538d", command=self.show_dashboard)
        self.nav_dash.grid(row=1, column=0, padx=15, pady=6, sticky="ew")
        
        self.nav_custom = ctk.CTkButton(self.sidebar_frame, text="⚡ Custom Script", font=ctk.CTkFont(size=13, weight="bold"), height=40, corner_radius=8, fg_color="transparent", text_color="#a0a5b5", hover_color="#1c1e24", command=self.show_custom_script)
        self.nav_custom.grid(row=2, column=0, padx=15, pady=6, sticky="ew")
        
        self.nav_settings = ctk.CTkButton(self.sidebar_frame, text="⚙️ Settings", font=ctk.CTkFont(size=13, weight="bold"), height=40, corner_radius=8, fg_color="transparent", text_color="#a0a5b5", hover_color="#1c1e24", command=self.show_settings)
        self.nav_settings.grid(row=3, column=0, padx=15, pady=6, sticky="ew")
        
        self.nav_about = ctk.CTkButton(self.sidebar_frame, text="ℹ️ About App", font=ctk.CTkFont(size=13, weight="bold"), height=40, corner_radius=8, fg_color="transparent", text_color="#a0a5b5", hover_color="#1c1e24", command=self.show_about)
        self.nav_about.grid(row=4, column=0, padx=15, pady=6, sticky="ew")

    def build_content_frames(self):
        self.dash_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.custom_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.settings_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.about_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        
        self.render_dashboard_layout()
        self.render_custom_script_layout()
        self.render_settings_layout()
        self.render_about_layout()
    
    def render_dashboard_layout(self):
        card = ctk.CTkFrame(self.dash_frame, fg_color="#16191f", corner_radius=16, border_width=1, border_color="#252932")
        card.pack(expand=True, fill="both", padx=40, pady=40)
        self.status_badge = ctk.CTkLabel(card, text="🟢 Ready to Record", font=ctk.CTkFont(size=14, weight="bold"), text_color="#2ecc71", fg_color="#1e2721", corner_radius=20, height=32, width=150)
        self.status_badge.pack(pady=(40, 10))
        self.record_btn = ctk.CTkButton(card, text="◉ Start Recording", font=ctk.CTkFont(size=16, weight="bold"), fg_color="#24a0ed", hover_color="#007cc7", height=54, width=300, corner_radius=27, command=self.toggle_recording)
        self.record_btn.pack(pady=20)
        self.tip_lbl = ctk.CTkLabel(card, text=f"Tip: Press '{self.hotkey_char}' anywhere on your system to start or stop.", text_color="#7f8c8d", font=ctk.CTkFont(size=12))
        self.tip_lbl.pack(side="bottom", pady=30)

    def render_custom_script_layout(self):
        card = ctk.CTkFrame(self.custom_frame, fg_color="#16191f", corner_radius=16, border_width=1, border_color="#252932")
        card.pack(expand=True, fill="both", padx=40, pady=40)
        title = ctk.CTkLabel(card, text="Custom Loop Automation", font=ctk.CTkFont(size=20, weight="bold"), text_color="#ffffff")
        title.pack(anchor="w", padx=35, pady=(25, 15))
        
        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill="x", padx=35, pady=6)
        lbl1 = ctk.CTkLabel(r1, text="Repeat Count (n Times):", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl1.pack(side="left")
        self.loop_entry = ctk.CTkEntry(r1, width=70, font=ctk.CTkFont(size=13, weight="bold"), fg_color="#111215", border_color="#252932", justify="center")
        self.loop_entry.pack(side="right")
        self.loop_entry.insert(0, "5") 
        
        r2 = ctk.CTkFrame(card, fg_color="transparent")
        r2.pack(fill="x", padx=35, pady=6)
        lbl2 = ctk.CTkLabel(r2, text="Script File Name:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl2.pack(side="left")
        self.name_entry = ctk.CTkEntry(r2, width=180, font=ctk.CTkFont(size=12), fg_color="#111215", border_color="#252932")
        self.name_entry.pack(side="right")
        self.name_entry.insert(0, "custom_macro.py")

        r3 = ctk.CTkFrame(card, fg_color="transparent")
        r3.pack(fill="x", padx=35, pady=6)
        lbl3 = ctk.CTkLabel(r3, text="Save Script Destination:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl3.pack(anchor="w", pady=(0, 4))
        wrapper = ctk.CTkFrame(r3, fg_color="transparent")
        wrapper.pack(fill="x")
        self.custom_path_entry = ctk.CTkEntry(wrapper, font=ctk.CTkFont(size=11), fg_color="#111215", border_color="#252932", height=32, text_color="#a0a5b5")
        self.custom_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.custom_path_entry.insert(0, self.custom_save_dir)
        self.custom_path_entry.configure(state="readonly")
        self.custom_browse_btn = ctk.CTkButton(wrapper, text="📁 Browse", font=ctk.CTkFont(size=12, weight="bold"), width=75, height=32, fg_color="#252932", hover_color="#313742", border_width=1, border_color="#3c4250", command=self.browse_custom_save_location)
        self.custom_browse_btn.pack(side="right")

        self.custom_status_badge = ctk.CTkLabel(card, text="🟢 Ready to Record Loop", font=ctk.CTkFont(size=13, weight="bold"), text_color="#2ecc71", fg_color="#1e2721", corner_radius=16, height=30, width=190)
        self.custom_status_badge.pack(pady=(20, 8))
        self.custom_record_btn = ctk.CTkButton(card, text="◉ Start Loop Recording", font=ctk.CTkFont(size=15, weight="bold"), fg_color="#24a0ed", hover_color="#007cc7", height=48, width=280, corner_radius=24, command=self.toggle_recording)
        self.custom_record_btn.pack(pady=5)
        self.custom_tip_lbl = ctk.CTkLabel(card, text=f"Tip: Press '{self.hotkey_char}' anywhere to start or stop recording.", text_color="#7f8c8d", font=ctk.CTkFont(size=11))
        self.custom_tip_lbl.pack(side="bottom", pady=15)

    def render_settings_layout(self):
        card = ctk.CTkFrame(self.settings_frame, fg_color="#16191f", corner_radius=16, border_width=1, border_color="#252932")
        card.pack(expand=True, fill="both", padx=40, pady=40)
        title = ctk.CTkLabel(card, text="Configuration Panel", font=ctk.CTkFont(size=20, weight="bold"), text_color="#ffffff")
        title.pack(anchor="w", padx=35, pady=(25, 15))
        
        s1 = ctk.CTkFrame(card, fg_color="transparent")
        s1.pack(fill="x", padx=35, pady=8)
        lbl_s1 = ctk.CTkLabel(s1, text="Shortcut Activation Key:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl_s1.pack(side="left")
        self.hk_btn = ctk.CTkButton(s1, text=f"[ {self.hotkey_char} ]  Change Key", font=ctk.CTkFont(size=12, weight="bold"), width=140, height=32, fg_color="#252932", hover_color="#313742", border_width=1, border_color="#3c4250", command=self.start_hotkey_rebind)
        self.hk_btn.pack(side="right")
        
        s2 = ctk.CTkFrame(card, fg_color="transparent")
        s2.pack(fill="x", padx=35, pady=8)
        lbl_s2 = ctk.CTkLabel(s2, text="Capture Settings:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl_s2.pack(side="left")
        self.tracking_mode = ctk.StringVar(value="smart")
        self.radio_path = ctk.CTkRadioButton(s2, text="✍️ Precision", font=ctk.CTkFont(size=12), variable=self.tracking_mode, value="path", text_color="#d1d4dc")
        self.radio_path.pack(side="right", padx=5)
        self.radio_smart = ctk.CTkRadioButton(s2, text="✨ Smart Engine", font=ctk.CTkFont(size=12), variable=self.tracking_mode, value="smart", text_color="#d1d4dc")
        self.radio_smart.pack(side="right", padx=5)

        s3 = ctk.CTkFrame(card, fg_color="transparent")
        s3.pack(fill="x", padx=35, pady=8)
        lbl_s3 = ctk.CTkLabel(s3, text="Speed Control:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl_s3.pack(side="left")
        self.delay_profile = ctk.StringVar(value="human")
        self.radio_zero = ctk.CTkRadioButton(s3, text="⚡ Turbo (Instant)", font=ctk.CTkFont(size=12), variable=self.delay_profile, value="zero", text_color="#d1d4dc")
        self.radio_zero.pack(side="right", padx=5)
        self.radio_human = ctk.CTkRadioButton(s3, text="⏳ Natural Pacing", font=ctk.CTkFont(size=12), variable=self.delay_profile, value="human", text_color="#d1d4dc")
        self.radio_human.pack(side="right", padx=5)

        s4 = ctk.CTkFrame(card, fg_color="transparent")
        s4.pack(fill="x", padx=35, pady=(12, 5))
        lbl_s4 = ctk.CTkLabel(s4, text="Dashboard Default Folder Target:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#b0b5c0")
        lbl_s4.pack(anchor="w", pady=(0, 5))
        wrapper = ctk.CTkFrame(s4, fg_color="transparent")
        wrapper.pack(fill="x")
        self.path_entry = ctk.CTkEntry(wrapper, font=ctk.CTkFont(size=11), fg_color="#111215", border_color="#252932", height=32, text_color="#a0a5b5")
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.path_entry.insert(0, self.global_save_dir)
        self.path_entry.configure(state="readonly")
        self.browse_btn = ctk.CTkButton(wrapper, text="📁 Browse", font=ctk.CTkFont(size=12, weight="bold"), width=80, height=32, fg_color="#252932", hover_color="#313742", border_width=1, border_color="#3c4250", command=self.browse_global_save_location)
        self.browse_btn.pack(side="right")

        container = ctk.CTkFrame(card, fg_color="transparent")
        container.pack(fill="x", side="bottom", padx=35, pady=(0, 20))
        self.reset_btn = ctk.CTkButton(container, text="↩ Reset to Defaults", font=ctk.CTkFont(size=11, weight="bold"), width=140, height=30, fg_color="transparent", hover_color="#2c1e1e", text_color="#ff4d4d", border_width=1, border_color="#d9534f", command=self.reset_to_defaults)
        self.reset_btn.pack(side="right")

    def render_about_layout(self):
        card = ctk.CTkFrame(self.about_frame, fg_color="#16191f", corner_radius=16, border_width=1, border_color="#252932")
        card.pack(expand=True, fill="both", padx=40, pady=40)
        title = ctk.CTkLabel(card, text="About PyAutoGen", font=ctk.CTkFont(size=22, weight="bold"), text_color="#24a0ed")
        title.pack(anchor="w", padx=35, pady=(30, 5))
        ver = ctk.CTkLabel(card, text="🚀 Build Version 1.0.0 (Stable)", font=ctk.CTkFont(size=12, weight="bold"), text_color="#7f8c8d")
        ver.pack(anchor="w", padx=35, pady=(0, 20))
        
        body = (
            "PyAutoGen is an intelligent, background macro-recording studio built specifically "
            "for Python developers. By capturing global peripheral inputs and system interactions "
            "in real time, it compiles your human actions directly into production-ready "
            "PyAutoGUI source code scripts automatically."
        )
        body_lbl = ctk.CTkLabel(card, text=body, font=ctk.CTkFont(size=13), text_color="#d1d4dc", justify="left", wraplength=440)
        body_lbl.pack(anchor="w", padx=35, pady=10)
        
        f_lbl = ctk.CTkLabel(card, text="Founder & Lead Architect:", font=ctk.CTkFont(size=13, weight="bold"), text_color="#ffffff")
        f_lbl.pack(anchor="w", padx=35, pady=(20, 2))
        name_lbl = ctk.CTkLabel(card, text="Emersia", font=ctk.CTkFont(size=15, weight="bold"), text_color="#2ecc71")
        name_lbl.pack(anchor="w", padx=35, pady=(0, 10))
        
        self.email_btn = ctk.CTkButton(card, text=f"✉  {self.email_address}", font=ctk.CTkFont(size=12), width=230, height=28, fg_color="#1c1e24", hover_color="#252932", text_color="#24a0ed", border_width=1, border_color="#252932", command=self.copy_email_to_clipboard)
        self.email_btn.pack(anchor="w", padx=35, pady=(0, 20))
        
        thanks = ctk.CTkFrame(card, fg_color="#111215", corner_radius=8, border_width=1, border_color="#252932")
        thanks.pack(fill="x", padx=35, pady=(5, 20))
        thanks_lbl = ctk.CTkLabel(thanks, text="✨ Special thanks to the open-source PyAutoGUI and CustomTkinter project teams!", font=ctk.CTkFont(size=11, slant="italic"), text_color="#a0a5b5")
        thanks_lbl.pack(padx=15, pady=12)

    # --- ROUTING SELECTION HANDLERS ---

    def show_dashboard(self):
        self.current_mode = "smart"
        self.custom_frame.grid_forget()
        self.settings_frame.grid_forget()
        self.about_frame.grid_forget() 
        self.dash_frame.grid(row=0, column=1, sticky="nsew")
        self.nav_dash.configure(fg_color="#1f538d", text_color="white")
        self.nav_custom.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_settings.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_about.configure(fg_color="transparent", text_color="#a0a5b5")

    def show_custom_script(self):
        self.current_mode = "custom"
        self.dash_frame.grid_forget()
        self.settings_frame.grid_forget()
        self.about_frame.grid_forget()
        self.custom_frame.grid(row=0, column=1, sticky="nsew")
        self.nav_custom.configure(fg_color="#1f538d", text_color="white")
        self.nav_dash.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_settings.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_about.configure(fg_color="transparent", text_color="#a0a5b5")

    def show_settings(self):
        self.dash_frame.grid_forget()
        self.custom_frame.grid_forget()
        self.about_frame.grid_forget()
        self.settings_frame.grid(row=0, column=1, sticky="nsew")
        self.nav_settings.configure(fg_color="#1f538d", text_color="white")
        self.nav_dash.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_custom.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_about.configure(fg_color="transparent", text_color="#a0a5b5")

    def show_about(self):
        self.dash_frame.grid_forget()
        self.custom_frame.grid_forget()
        self.settings_frame.grid_forget()
        self.about_frame.grid(row=0, column=1, sticky="nsew")
        self.nav_about.configure(fg_color="#1f538d", text_color="white")
        self.nav_dash.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_custom.configure(fg_color="transparent", text_color="#a0a5b5")
        self.nav_settings.configure(fg_color="transparent", text_color="#a0a5b5")

    def browse_global_save_location(self):
        d = filedialog.askdirectory(initialdir=self.global_save_dir, title="Select Folder")
        if d:
            self.global_save_dir = d
            self.path_entry.configure(state="normal")
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, self.global_save_dir)
            self.path_entry.configure(state="readonly")

    def browse_custom_save_location(self):
        d = filedialog.askdirectory(initialdir=self.custom_save_dir, title="Select Destination")
        if d:
            self.custom_save_dir = d
            self.custom_path_entry.configure(state="normal")
            self.custom_path_entry.delete(0, "end")
            self.custom_path_entry.insert(0, self.custom_save_dir)
            self.custom_path_entry.configure(state="readonly")

    def start_hotkey_rebind(self):
        self.is_rebinding = True
        self.hk_btn.configure(text="👉 Listening for Key...", fg_color="#d9534f", border_width=0)

    def copy_email_to_clipboard(self):
        self.clipboard_clear()
        self.clipboard_append(self.email_address)
        self.email_btn.configure(text="✔ Email Copied!", text_color="#2ecc71", fg_color="#1e2721")
        self.after(2000, lambda: self.email_btn.configure(text=f"✉  {self.email_address}", text_color="#24a0ed", fg_color="#1c1e24"))

    def reset_to_defaults(self):
        self.hotkey_char = "`"
        self.tracking_mode.set("smart")
        self.delay_profile.set("human")
        self.global_save_dir = os.path.dirname(os.path.abspath(__file__))
        self.custom_save_dir = os.path.dirname(os.path.abspath(__file__))
        
        self.path_entry.configure(state="normal")
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, self.global_save_dir)
        self.path_entry.configure(state="readonly")
        
        self.custom_path_entry.configure(state="normal")
        self.custom_path_entry.delete(0, "end")
        self.custom_path_entry.insert(0, self.custom_save_dir)
        self.custom_path_entry.configure(state="readonly")
        
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, "custom_macro.py")
        self.loop_entry.delete(0, "end")
        self.loop_entry.insert(0, "5")
        
        self.hk_btn.configure(text=f"[ {self.hotkey_char} ]  Change Key")
        self.tip_lbl.configure(text=f"Tip: Press '{self.hotkey_char}' anywhere on your system to start or stop.")
        self.custom_tip_lbl.configure(text=f"Tip: Press '{self.hotkey_char}' anywhere on your system to start or stop.")

    # --- STATE AND ENGINE SYNCHRONIZATION RUNNERS ---

    def toggle_recording(self):
        if not self.recorder.is_recording:
            if self.current_mode == "custom":
                try:
                    self.loop_count = int(self.loop_entry.get())
                    if self.loop_count < 1: self.loop_count = 1
                except ValueError:
                    self.loop_count = 1
                    self.loop_entry.delete(0, "end")
                    self.loop_entry.insert(0, "1")

            self.recorder.start(self.hotkey_char)
            self.record_btn.configure(text="⬜ Stop Recording", fg_color="#d9534f", hover_color="#c9302c")
            self.custom_record_btn.configure(text="⬜ Stop Loop Recording", fg_color="#d9534f", hover_color="#c9302c")
            self.status_badge.configure(text="🔴 Recording Active", text_color="#ff4d4d", fg_color="#2c1e1e")
            self.custom_status_badge.configure(text="🔴 Recording Active Loop", text_color="#ff4d4d", fg_color="#2c1e1e")
        else:
            self.recorder.stop()
            self.record_btn.configure(text="◉ Start Recording", fg_color="#24a0ed", hover_color="#007cc7")
            self.custom_record_btn.configure(text="◉ Start Loop Recording", fg_color="#24a0ed", hover_color="#007cc7")
            self.status_badge.configure(text="⏳ Packaging Code...", text_color="#f39c12", fg_color="#2c251e")
            self.custom_status_badge.configure(text="⏳ Packaging Loop...", text_color="#f39c12", fg_color="#2c251e")
            self.update_idletasks()
            self.save_recorded_code()

    # --- HARDWARE SYSTEM HOOK INTERFACES ---

    def initialize_hardware_hooks(self):
        self.k_listener = keyboard.Listener(
            on_press=self.intercept_press, 
            on_release=lambda k: self.recorder.process_release(k) if not self.is_rebinding else None
        )
        self.m_listener = mouse.Listener(
            on_click=lambda x, y, b, p: self.recorder.process_click(x, y, b, p) if self.recorder.is_recording else None,
            on_move=lambda x, y: self.recorder.process_move(x, y) if self.recorder.is_recording else None,
            on_scroll=lambda x, y, dx, dy: self.recorder.process_scroll(x, y, dx, dy) if self.recorder.is_recording else None
        )
        self.k_listener.daemon = True
        self.m_listener.daemon = True
        self.k_listener.start()
        self.m_listener.start()

    def intercept_press(self, key):
        if self.is_rebinding:
            try: self.hotkey_char = key.char
            except AttributeError: self.hotkey_char = str(key).replace("Key.", "")
            self.is_rebinding = False
            self.hk_btn.configure(text=f"[ {self.hotkey_char} ]  Change Key", fg_color="#252932", border_width=1)
            self.tip_lbl.configure(text=f"Tip: Press '{self.hotkey_char}' anywhere on your system to start or stop.")
            self.custom_tip_lbl.configure(text=f"Tip: Press '{self.hotkey_char}' anywhere on your system to start or stop.")
            return

        is_trigger = False
        try:
            if key.char == self.hotkey_char: is_trigger = True
        except AttributeError:
            if str(key).replace("Key.", "") == self.hotkey_char: is_trigger = True

        if is_trigger:
            self.after(0, self.toggle_recording)
        elif self.recorder.is_recording:
            self.recorder.process_press(key)

    # --- AUTOMATION CODE COMPILATION ENGINE ---

    def save_recorded_code(self):
        events = self.recorder.recorded_events
        if not events:
            self.status_badge.configure(text="🟢 Ready to Record", text_color="#2ecc71", fg_color="#1e2721")
            self.custom_status_badge.configure(text="🟢 Ready to Record Loop", text_color="#2ecc71", fg_color="#1e2721")
            return

        if self.current_mode == "custom":
            raw_name = self.name_entry.get().strip() or "custom_macro.py"
            sanitized_name = re.sub(r'[\\/*?:"<>|]', "", raw_name)
            if not sanitized_name.lower().endswith(".py"): sanitized_name += ".py"
            filename = os.path.join(self.custom_save_dir, sanitized_name)
        else:
            filename = os.path.join(self.global_save_dir, "generated_macro.py")
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write("import pyautogui\nimport time\n\n")
            f.write(f"# Auto-generated by PyAutoGen (Developed by Emersia)\n\n")
            
            f.write("print('\\n⏳ Starting in 3 seconds... Click on your target window!')\n")
            for i in range(3, 0, -1):
                f.write(f"print('{i}...')\ntime.sleep(1.0)\n")
            f.write("print('🚀 Macro started! Running your recorded steps...\\n')\n\n")
            
            if self.current_mode == "custom":
                f.write(f"print('🔄 Custom Loop Profile: Running macro sequence {self.loop_count} times.')\n")
                f.write(f"for iteration in range({self.loop_count}):\n")
                f.write(f"    print(f'▶️ Executing iteration {{iteration + 1}} of {self.loop_count}...')\n")
                ind = "    "
            else:
                ind = ""
            
            last_time = self.recorder.start_time
            
            for event_type, data, timestamp in events:
                delay = timestamp - last_time
                
                if self.delay_profile.get() == "human" and delay > 0.02:
                    f.write(f"{ind}time.sleep({round(delay, 2)})\n")
                
                if event_type == "key_down":
                    f.write(f"{ind}pyautogui.keyDown('{data}')\n")
                elif event_type == "key_up":
                    f.write(f"{ind}pyautogui.keyUp('{data}')\n")
                elif event_type == "mouse_down":
                    f.write(f"{ind}pyautogui.moveTo({data[0]}, {data[1]})\n")
                    f.write(f"{ind}pyautogui.mouseDown(button='{data[2]}')\n")
                elif event_type == "mouse_up":
                    f.write(f"{ind}pyautogui.moveTo({data[0]}, {data[1]})\n")
                    f.write(f"{ind}pyautogui.mouseUp(button='{data[2]}')\n")
                elif event_type == "mouse_move" and self.tracking_mode.get() == "path":
                    f.write(f"{ind}pyautogui.moveTo({data[0]}, {data[1]})\n")
                elif event_type == "mouse_scroll":
                    f.write(f"{ind}pyautogui.moveTo({data[0]}, {data[1]})\n")
                    if data[3] != 0: 
                        # Scale direct kernel integers down to balanced PyAutoGUI metrics
                        f.write(f"{ind}pyautogui.scroll({int(data[3] * 120)})\n")
                    if data[2] != 0: 
                        f.write(f"{ind}pyautogui.hscroll({int(data[2] * 120)})\n")
                    
                last_time = timestamp
                
            if self.current_mode == "custom":
                f.write("    time.sleep(1.0) # 1 second padding shift between loop iterations\n")

            f.write("\nprint('\\n✅ Macro execution completed successfully!')\n")
                
        display_name = os.path.basename(filename)
        self.status_badge.configure(text=f"✨ Saved {display_name}!", text_color="#3498db", fg_color="#1a252f")
        self.custom_status_badge.configure(text=f"✨ Saved {display_name}!", text_color="#3498db", fg_color="#1a252f")


if __name__ == "__main__":
    app = PyAutoGenApp()
    app.mainloop()