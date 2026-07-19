"""ENA 마스코트 — 입력 반응형 데스크탑 캐릭터 (+선택형 작업 타이머).

캐릭터별 파츠 폴더(extract_psd.py로 PSD에서 추출)를 조합해 움직인다:
  python mascot.py                      # 기본 캐릭터 (parts/ = 까만 고양이)
  python mascot.py --char parts_junsa   # 준사 (작업 타이머 포함)
  python mascot.py --preview            # 대표 포즈 PNG 저장 후 종료 (개발용)

동작:
- 키 입력            → 손이 어깨를 축으로 회전하며 키보드를 두드림 (어깨는 몸에 고정)
- 커서 이동/그리기    → 펜 쥔 오른손이 미니 타블렛 화면 위에서 커서를 따라다니고,
                       오른팔은 어깨 고정·손끝 추적으로 치즈스틱처럼 늘어남
- 타이핑만 할 때      → 펜 손·팔은 숨고 '오른팔-타자' 파츠가 나와 양손 타이핑
- 시선/유휴          → 눈동자 커서 추적, 숨쉬기, (마스크 구조가 있으면) 깜빡임
- 타이머(config)     → 캐릭터 위 캡슐 배지에 오늘 작업시간. 입력이 끊기면 휴식 전환.
                       작업일 경계 06:00, 상태는 주기 저장(강제종료 대비).

config.json 주요 키: scale, screen_quad, blink, trail_color, pen_tip,
  hard_alpha(외곽 픽셀 이분화 — 밝은 캐릭터의 검은 테두리 방지),
  timer({"enabled": true, "idle_sec": 60})
조작: 캐릭터 드래그 = 위치 이동, 우클릭 = 메뉴.
"""
import ctypes
import json
import math
import os
import random
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk
from pynput import keyboard, mouse

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

if getattr(sys, "frozen", False) and not os.path.exists(os.path.abspath(__file__)):
    # PyInstaller 번들 내부에서 임포트된 경우 (자동 업데이트로 받은 파일이면
    # __file__이 실제 디스크에 존재하므로 그 폴더를 기준으로 삼는다)
    HERE = sys._MEIPASS
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
TRANSPARENT = "#010203"          # 투명 키 색

KEY_ROT = (-7.0, 7.0)            # 타이핑 시 손 회전(어깨 축) 범위 (도)
PEN_KB_ROT = (-6.0, 6.0)
TIMER_H = 92                     # 타이머 카드 영역 높이 (게이지형 = 준사)
OY_CLOCK_COMPACT = 70            # 시계형 카드 접힘 (상태+시간 한 줄)
OY_CLOCK_OPEN = 182             # 시계형 카드 펼침 (시계 + 시간)

# 타이머 카드 팔레트 (준사 배색)
CARD_BORDER = "#f2b8c6"          # 소프트 핑크
CARD_NAVY = "#3a4a6b"
CARD_GRAY = "#9aa7bd"
CARD_TRACK = "#eef0f5"
CARD_FILL = "#f2a7b3"
DOT_ON, DOT_OFF = "#7ccf8f", "#cfcfcf"

# 환경설정 기본값 (캐릭터 폴더의 .settings.json에 저장)
DEFAULT_SETTINGS = {
    "goal_hours": 6.0,    # 목표 작업시간
    "idle_sec": 15.0,     # 휴식 전환(초)
    "show_timer": None,   # None = config 기본값 따름
    "trail": False,       # 타블렛 낙서 표시
    "topmost": True,      # 항상 위
    "scale_pct": 100,     # 캐릭터 크기(%)
    "work_apps_only": True,   # 작업 프로그램이 앞에 있을 때만 시간 측정
    "work_apps": "clipstudiopaint.exe, photoshop.exe, sai2.exe, krita.exe",
    "sleep_min": 10,      # 이 시간(분) 동안 무입력이면 수면 모드
    "shadow": True,       # 캐릭터 뒤 옅은 그림자
    "clock_open": False,  # 시계형 카드에서 시계 펼침 상태
    "sound": True,        # 타자 소리 (Mechvibes 팩)
    "sound_volume": 60,   # 타자 소리 볼륨 (0~100)
    "pen_volume": 30,     # 펜 긋는 소리 볼륨 (0~100)
    "sound_pack": "banana split lubed",
}
DOT_OTHER = "#f0b95e"     # 딴짓 중(작업앱 아님) 표시색


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class ShadowLayer:
    """캐릭터 창 뒤에 깔리는 진짜 반투명 그림자 (per-pixel alpha 레이어 창).

    색상키 투명창은 반투명을 표현할 수 없으므로, 그림자만 별도의
    UpdateLayeredWindow 창으로 그린다. 클릭은 통과(WS_EX_TRANSPARENT).
    """

    def __init__(self, root, image, offset=(7, 9)):
        self.offset = offset
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.update_idletasks()
        self.hwnd = int(self.top.wm_frame(), 16)
        u = ctypes.windll.user32
        GWL_EXSTYLE = -20
        ex = u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        # LAYERED | TRANSPARENT(클릭 통과) | TOOLWINDOW | NOACTIVATE
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                         ex | 0x80000 | 0x20 | 0x80 | 0x8000000)
        self._push(image)

    def _push(self, im):
        """BGRA 비트맵을 레이어 창에 업로드 (그림자는 검정이라 premultiply 불요)."""
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        w, h = im.size
        data = im.tobytes("raw", "BGRA")
        hdc = u.GetDC(0)
        mem = g.CreateCompatibleDC(hdc)
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = w, -h
        bmi.biPlanes, bmi.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        hbm = g.CreateDIBSection(hdc, ctypes.byref(bmi), 0,
                                 ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = g.SelectObject(mem, hbm)
        blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, alpha 채널 사용
        u.UpdateLayeredWindow(self.hwnd, hdc, ctypes.byref(_POINT(0, 0)),
                              ctypes.byref(_SIZE(w, h)), mem,
                              ctypes.byref(_POINT(0, 0)), 0,
                              ctypes.byref(blend), 2)  # ULW_ALPHA
        g.SelectObject(mem, old)
        g.DeleteObject(hbm)
        g.DeleteDC(mem)
        u.ReleaseDC(0, hdc)

    def set_image(self, image):
        """그림자 이미지 교체 (시계 토글로 크기가 바뀔 때)."""
        self._push(image)

    def place(self, x, y, owner_hwnd):
        """본체 창 바로 아래 z순서로, 오프셋만큼 밀린 위치에 배치."""
        SWP_NOSIZE, SWP_NOACTIVATE = 0x1, 0x10
        ctypes.windll.user32.SetWindowPos(
            self.hwnd, owner_hwnd, x + self.offset[0], y + self.offset[1],
            0, 0, SWP_NOSIZE | SWP_NOACTIVATE)


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
                ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
                ("cbSize", ctypes.c_uint16)]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
                ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_void_p)]


class SoundPack:
    """Mechvibes 사운드 팩(multi 타입: 키별 wav) 재생기.

    winmm waveOut API 직접 호출 — 외부 라이브러리 불필요, 어느 스레드에서든
    안전(메시지 펌프 불요), 재생마다 독립 장치라 동시 재생 가능.
    (MCI는 연 스레드에 묶여 리스너 스레드에서 멈추는 문제가 있어 사용 안 함)
    """

    def __init__(self, folder, volume=60):
        import threading
        import wave
        with open(os.path.join(folder, "config.json"), encoding="utf-8") as fp:
            cfg = json.load(fp)
        if cfg.get("key_define_type", "multi") != "multi":
            raise ValueError("single 타입 팩 미지원 — wav 분할형 팩을 사용하세요")
        names = []
        for v in cfg.get("defines", {}).values():
            if isinstance(v, str) and v and v not in names:
                names.append(v)
        self.sounds = []          # (WAVEFORMATEX, 버퍼, 길이)
        for name in names:
            path = os.path.join(folder, name)
            if not (name.lower().endswith(".wav") and os.path.exists(path)):
                continue
            with wave.open(path, "rb") as w:
                ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                data = w.readframes(w.getnframes())
            wfx = _WAVEFORMATEX(1, ch, fr, fr * ch * sw, ch * sw, sw * 8, 0)
            buf = ctypes.create_string_buffer(data, len(data))
            self.sounds.append((wfx, buf, len(data)))
        if not self.sounds:
            raise ValueError("재생 가능한 wav가 없음")
        self.volume = volume
        self._active = []         # (핸들, WAVEHDR) — 재생 끝나면 정리
        self._lock = threading.Lock()

    def play(self, key):
        wfx, buf, ln = self.sounds[hash(str(key)) % len(self.sounds)]
        wm = ctypes.windll.winmm
        h = ctypes.c_void_p()
        if wm.waveOutOpen(ctypes.byref(h), 0xFFFFFFFF, ctypes.byref(wfx), 0, 0, 0):
            return
        v = max(0, min(int(self.volume * 0xFFFF / 100), 0xFFFF))
        wm.waveOutSetVolume(h, v | (v << 16))
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = ln
        wm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        wm.waveOutWrite(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        with self._lock:
            self._active.append((h, hdr))
            self._reap_locked(limit=24)

    def reap(self):
        """재생이 끝난 장치 정리 (주기 호출용)."""
        with self._lock:
            self._reap_locked(limit=24)

    def _reap_locked(self, limit):
        wm = ctypes.windll.winmm
        keep = []
        for h, hdr in self._active:
            if hdr.dwFlags & 0x1:     # WHDR_DONE
                wm.waveOutUnprepareHeader(h, ctypes.byref(hdr),
                                          ctypes.sizeof(_WAVEHDR))
                wm.waveOutClose(h)
            else:
                keep.append((h, hdr))
        while len(keep) > limit:      # 안전판: 과도한 동시 재생 방지
            h, hdr = keep.pop(0)
            wm.waveOutReset(h)
            wm.waveOutUnprepareHeader(h, ctypes.byref(hdr),
                                      ctypes.sizeof(_WAVEHDR))
            wm.waveOutClose(h)
        self._active = keep

    def close(self):
        with self._lock:
            wm = ctypes.windll.winmm
            for h, hdr in self._active:
                wm.waveOutReset(h)
                wm.waveOutUnprepareHeader(h, ctypes.byref(hdr),
                                          ctypes.sizeof(_WAVEHDR))
                wm.waveOutClose(h)
            self._active = []


class PenSound:
    """펜 소리 하이브리드 — 짧은 선은 클립 한 번, 길게 이어지면 지속음.

    - start(): 옛 스크리블 클립(clip_*.wav) 하나를 한 번 재생 (한 번 '슥').
    - sustain(): 선이 계속되면(>SUSTAIN_DELAY) 그래뉼러 지속음(penbed.wav)을
      루프로 이어 붙여 '스으으윽'으로 지속.
    - stop(): 둘 다 정지. → 선 길이와 소리 길이가 맞는다.
    """
    _LOOP_FLAGS = 0x00000004 | 0x00000008     # WHDR_BEGINLOOP | WHDR_ENDLOOP
    SUSTAIN_DELAY = 0.35

    def __init__(self, folder, volume=35):
        import wave

        def load(path):
            with wave.open(path, "rb") as w:
                ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                data = w.readframes(w.getnframes())
            wfx = _WAVEFORMATEX(1, ch, fr, fr * ch * sw, ch * sw, sw * 8, 0)
            return (wfx, ctypes.create_string_buffer(data, len(data)),
                    len(data), ch * sw, fr)

        self.clips = []
        for f in sorted(os.listdir(folder)):
            if f.lower().startswith("clip") and f.lower().endswith(".wav"):
                wfx, buf, ln, _, _ = load(os.path.join(folder, f))
                self.clips.append((wfx, buf, ln))
        self.bed = None
        bp = os.path.join(folder, "penbed.wav")
        if os.path.exists(bp):
            wfx, buf, ln, fb, fr = load(bp)
            # 긴 지속음은 매번 임의 위치의 구간만 떼어 재생(전체 복사 회피)
            self.bed = (wfx, buf.raw, fb, ln // fb, fr)
        if not self.clips and self.bed is None:       # 폴백: 아무 wav나 클립으로
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith(".wav"):
                    wfx, buf, ln, _, _ = load(os.path.join(folder, f))
                    self.clips.append((wfx, buf, ln))
        if not self.clips and self.bed is None:
            raise ValueError("펜 소리 wav 없음")
        self.volume = volume
        self._clip = None         # (핸들, WAVEHDR, 버퍼)
        self._beddev = None
        self._t0 = 0.0
        self._bed_on = False

    def _open(self, wfx, buf, ln, loop):
        wm = ctypes.windll.winmm
        h = ctypes.c_void_p()
        if wm.waveOutOpen(ctypes.byref(h), 0xFFFFFFFF, ctypes.byref(wfx), 0, 0, 0):
            return None
        v = max(0, min(int(self.volume * 0xFFFF / 100), 0xFFFF))
        wm.waveOutSetVolume(h, v | (v << 16))
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = ln
        if loop:
            hdr.dwFlags = self._LOOP_FLAGS
            hdr.dwLoops = 0xFFFFFFF
        wm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        wm.waveOutWrite(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        return (h, hdr, buf)

    def start(self):
        """선 긋기 시작 — 클립 하나를 한 번 재생."""
        self.stop()
        if self.clips:
            self._clip = self._open(*random.choice(self.clips), loop=False)
        self._t0 = time.time()
        self._bed_on = False

    def sustain(self, now):
        """선이 계속되면 지속음 베드를 이어 붙인다 (한 번만)."""
        if self.bed is None or self._bed_on or now - self._t0 < self.SUSTAIN_DELAY:
            return
        wfx, pcm, fb, nframes, fr = self.bed
        # 긴 파일에서 임의 위치의 12초 구간만 떼어 루프 (전체 15MB 복사 회피)
        seg = min(int(12.0 * fr), nframes)
        start = random.randint(0, max(nframes - seg, 0))
        data = pcm[start * fb:(start + seg) * fb]
        buf = ctypes.create_string_buffer(data, len(data))
        self._beddev = self._open(wfx, buf, len(data), loop=True)
        self._bed_on = True

    def stop(self):
        for d in (self._clip, self._beddev):
            if d is not None:
                self._release(d)
        self._clip = None
        self._beddev = None
        self._bed_on = False

    @staticmethod
    def _release(dev):
        wm = ctypes.windll.winmm
        h, hdr, _buf = dev
        wm.waveOutReset(h)
        wm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        wm.waveOutClose(h)


ctypes.windll.user32.MonitorFromPoint.argtypes = [_POINT, ctypes.c_uint32]
ctypes.windll.user32.MonitorFromPoint.restype = ctypes.c_void_p


def cursor_pos():
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def idle_seconds():
    """마지막 입력(마우스·키보드·펜) 이후 경과 초."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
        return max(ctypes.windll.kernel32.GetTickCount() - info.dwTime, 0) / 1000.0
    except Exception:
        return 0.0


def foreground_process():
    """앞에 떠 있는 창의 프로세스 실행파일 이름 (소문자). 실패 시 ''."""
    try:
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = u.GetForegroundWindow()
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            k.CloseHandle(h)
    except Exception:
        pass
    return ""


def monitor_at(x, y):
    try:
        hmon = ctypes.windll.user32.MonitorFromPoint(_POINT(x, y), 2)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    u = ctypes.windll.user32
    return 0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1)


class Mascot:
    def __init__(self, char_dir="parts", preview=False, state_dir=None):
        self.char_arg = char_dir
        self.dir = os.path.join(HERE, char_dir)
        self.char = os.path.basename(char_dir)
        # 설정·타이머 기록 저장 위치 (자동 업데이트로 교체되지 않는 곳으로 분리 가능)
        self.state_dir = state_dir or self.dir
        os.makedirs(self.state_dir, exist_ok=True)
        with open(os.path.join(self.dir, "layout.json"), encoding="utf-8") as fp:
            self.layout = json.load(fp)
        with open(os.path.join(self.dir, "config.json"), encoding="utf-8") as fp:
            self.cfg = json.load(fp)

        # 사용자 환경설정 (config 기본값 위에 덮어씀)
        tcfg = self.cfg.get("timer") or {}
        self.us = dict(DEFAULT_SETTINGS)
        self.us["idle_sec"] = float(tcfg.get("idle_sec", self.us["idle_sec"]))
        self.settings_path = os.path.join(self.state_dir, ".settings.json")
        try:
            with open(self.settings_path, encoding="utf-8") as fp:
                self.us.update(json.load(fp))
        except Exception:
            pass

        s = self.s = float(self.cfg.get("scale", 1.0)) * self.us["scale_pct"] / 100.0
        self.timer_on = bool(tcfg.get("enabled")) \
            if self.us["show_timer"] is None else bool(self.us["show_timer"])
        self.idle_thr = float(self.us["idle_sec"])
        self._settings_win = None

        # 타이머 카드 테마 (캐릭터별 config의 card 섹션)
        cc = self.cfg.get("card") or {}
        self.card = {
            "bg": cc.get("bg", "#ffffff"), "border": cc.get("border", CARD_BORDER),
            "text": cc.get("text", CARD_NAVY), "sub": cc.get("sub", CARD_GRAY),
            "track": cc.get("track", CARD_TRACK), "fill": cc.get("fill", CARD_FILL),
            "deco": cc.get("deco", "panda"),
        }

        # 워크스페이스 워크타이머 연동 (config의 workspace_timer = 라이브 파일 경로)
        # 연동 모드 = 게이지 대신 시계 토글 카드. 비연동(준사) = 목표 게이지 카드.
        ws = self.cfg.get("workspace_timer")
        self.ws_path = os.path.normpath(os.path.join(HERE, ws)) if ws else None
        self._ws_data = None
        self._ws_read = 0.0
        self.has_clock = self.timer_on and self.ws_path is not None
        self.clock_open = bool(self.us.get("clock_open")) if self.has_clock else False

        self.oy = self._timer_oy()                  # 캐릭터 전체 y 오프셋
        cw, ch = self.layout["canvas"]
        self.cw_px, self.ch_px = round(cw * s), round(ch * s)
        self.W, self.H = self.cw_px, self.ch_px + self.oy

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.us["topmost"]))
        self.root.attributes("-transparentcolor", TRANSPARENT)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{self.W}x{self.H}+{sw - self.W - 50}+{sh - self.H - 70}")

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()

        self._f_status = tkfont.Font(family="Malgun Gothic", size=8)

        self._load_parts()

        # ── 상태 ──────────────────────────────────────────────────────────
        self.key_events = 0
        self._seen_keys = 0
        self.squash_until = 0.0
        self.mouse_pressed = False
        self.last_drag = 0.0
        self.last_pointer = 0.0
        self.last_key = 0.0
        self.tap_side = False
        self.key_ang_t = 0.0
        self.key_ang = 0.0
        self.left_down_until = 0.0
        self.pen_ang_t = 0.0
        self.pen_ang = 0.0
        self.pen_down_until = 0.0
        self.strokes = []
        self._new_stroke = True
        self.blink_until = 0.0
        self.next_blink = time.time() + random.uniform(2.5, 5.5)
        self._pen_xy = list(self.pen_base_tip)
        self._force = {}

        # ── 타이머 상태 ───────────────────────────────────────────────────
        self.work_secs = 0.0
        self._t_last = time.time()
        self._t_save = 0.0
        self._fg_checked = 0.0
        self._fg_work = False
        self.state_path = os.path.join(self.state_dir, ".timer_state.json")
        if self.timer_on and self.ws_path is None:
            self._timer_load()

        # ── 창 드래그 이동 / 카드 클릭 토글 / 우클릭 메뉴 ────────────────
        self._press = None
        self._dragged = False
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="환경설정", command=self.open_settings)
        if self.has_clock:
            menu.add_command(label="시계 펼치기 / 접기", command=self._toggle_clock)
        if self.timer_on and self.ws_path is None:
            menu.add_command(label="타이머 초기화", command=self._timer_reset)
        if self.ws_path is not None:
            menu.add_command(label="기본 타이머로 전환", command=self.close)
        menu.add_separator()
        menu.add_command(label="종료", command=self.close)
        self.canvas.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

        # ── 타자 소리 / 펜 소리 ──────────────────────────────────────────
        self.sndpack = None
        self.pensnd = None
        self._pen_playing = False
        self._pen_release_t = None
        self.sound_packs = self._list_packs()
        self._init_sound()

        # ── 전역 입력 리스너 ──────────────────────────────────────────────
        self._held = set()
        self._kb = keyboard.Listener(on_press=self._on_key,
                                     on_release=self._on_key_release)
        self._ms = mouse.Listener(on_click=self._on_click, on_move=self._on_move)
        self._kb.daemon = self._ms.daemon = True
        self._kb.start()
        self._ms.start()

        # ── 그림자 레이어 창 ─────────────────────────────────────────────
        self.root.update_idletasks()
        self._main_hwnd = int(self.root.wm_frame(), 16)
        self.shadow = None
        self._z_check = 0.0
        if self.shadow_img is not None:
            self.shadow = ShadowLayer(self.root, self.shadow_img)
            self.shadow.place(self.root.winfo_rootx(), self.root.winfo_rooty(),
                              self._main_hwnd)
        self._last_pos = None

        if preview:
            self.root.after(600, self._preview_shots)
        else:
            self.tick()

    # ── 파츠 로드 (모든 좌표는 표시 배율 + y 오프셋 적용) ─────────────────
    def _hard(self, im):
        """반투명 가장자리 픽셀 이분화 — 밝은 캐릭터의 검은 테두리 방지."""
        if not self.cfg.get("hard_alpha"):
            return im
        r, g, b, a = im.split()
        im = im.copy()
        im.putalpha(a.point(lambda v: 255 if v >= 60 else 0))
        return im

    def _load_parts(self):
        s = self.s

        def load_pil(name):
            im = Image.open(os.path.join(self.dir, f"{name}.png")).convert("RGBA")
            if s != 1.0:
                im = im.resize((max(1, round(im.width * s)),
                                max(1, round(im.height * s))), Image.LANCZOS)
            return self._hard(im)

        self.im = {}
        self.has = {}
        pil_cache = {}
        for name in ("body_open", "pupils", "body_mask", "lashes", "hair",
                     "eyes_closed", "desk", "arm_pen"):
            self.has[name] = os.path.exists(os.path.join(self.dir, f"{name}.png"))
            if self.has[name]:
                pil_cache[name] = load_pil(name)
                self.im[name] = ImageTk.PhotoImage(pil_cache[name])

        # 회전 손 파츠: 어깨(최상단) 앵커 기준으로 회전 — 어깨가 몸에서 안 떨어짐
        self.hop = {}
        for name in ("arm_key", "arm_right_typing"):
            im = load_pil(name)
            ab = im.split()[3].getbbox()
            top = ab[1]
            row = im.crop((0, top, im.width, min(top + 3, im.height))).split()[3].getbbox()
            anchor_x = (row[0] + row[2]) / 2 if row else im.width / 2
            m = max(6, round(im.height * 0.18))       # 회전 여유 패딩
            padded = Image.new("RGBA", (im.width + 2 * m, im.height + m), (0, 0, 0, 0))
            padded.paste(im, (m, 0))
            self.hop[name] = {"pil": padded, "anchor": (anchor_x + m, top),
                              "off": (-m, 0), "cache": {}}

        # 오른팔: 늘리기용
        self.arm_pil = load_pil("arm_right")
        self._arm_cache = {}
        # 왼손 위치 미세 보정 (캔버스 px, config의 arm_key_offset)
        ko = self.cfg.get("arm_key_offset", [0, 0])
        self.arm_key_off = (ko[0] * s, ko[1] * s)
        self._pil_cache = {n: pil_cache[n] for n in pil_cache}
        self._load_pil = load_pil

        self._bake_oy()                 # oy 의존 좌표 계산
        self._build_shadow_img()        # 그림자 이미지 생성

    def _timer_oy(self):
        """타이머 카드가 차지하는 캐릭터 위 여백."""
        if not self.timer_on:
            return 0
        if self.has_clock:
            return OY_CLOCK_OPEN if self.clock_open else OY_CLOCK_COMPACT
        return TIMER_H

    def _bake_oy(self):
        """oy(카드 높이)에 의존하는 좌표들 — 시계 토글로 oy가 바뀌면 다시 부른다."""
        s = self.s
        ar = self.layout["arm_right"]
        ax, ay = ar["pos"]
        self.arm_top = ((ax + ar["top"][0]) * s, (ay + ar["top"][1]) * s + self.oy)
        self.arm_bottom = ((ax + ar["bottom"][0]) * s,
                           (ay + ar["bottom"][1]) * s + self.oy)
        self._arm_nat = (self.arm_bottom[0] - self.arm_top[0],
                         self.arm_bottom[1] - self.arm_top[1])
        px, py = self.layout["arm_pen"]["pos"]
        tx, ty = self.cfg.get("pen_tip", self.layout["arm_pen"]["pen_tip"])
        self.pen_base_tip = ((px + tx) * s, (py + ty) * s + self.oy)
        self.quad = [(x * s, y * s + self.oy) for x, y in self.cfg["screen_quad"]]
        blink = self.cfg.get("blink")
        self.blink_cfg = None
        if blink and self.has["body_mask"]:
            r = blink["rect"]
            self.blink_cfg = ([r[0] * s, r[1] * s + self.oy,
                               r[2] * s, r[3] * s + self.oy], blink["color"])

    def _build_shadow_img(self):
        """캐릭터+카드 실루엣을 흐려 만든 반투명 그림자 이미지."""
        self.shadow_img = None
        if not self.us.get("shadow", True):
            return
        from PIL import ImageDraw, ImageFilter
        comp = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        for name in ("body_open", "lashes", "hair", "desk", "arm_pen"):
            if name in self._pil_cache:
                x, y = self._pos(name)
                comp.alpha_composite(self._pil_cache[name], (round(x), round(y)))
        for name in ("arm_right", "arm_key"):
            im = self._load_pil(name)
            x, y = self._pos(name)
            if name == "arm_key":
                x += self.arm_key_off[0]
                y += self.arm_key_off[1]
            comp.alpha_composite(im, (round(x), round(y)))
        if self.timer_on:
            d = ImageDraw.Draw(comp)
            cg = self._card_geom()
            cx0, cy0, cx1, cy1 = cg["x0"], cg["y0"], cg["x1"], cg["y1"]
            for ex in (cx0 + 26, cx1 - 26):        # 귀 실루엣
                d.ellipse([ex - 12, cy0 - 17, ex + 12, cy0 + 7], fill=(0, 0, 0, 255))
            d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=16, fill=(0, 0, 0, 255))
        a = comp.getchannel("A").filter(ImageFilter.GaussianBlur(7))
        a = a.point(lambda v: int(v * 0.30))
        self.shadow_img = Image.merge(
            "RGBA", (*Image.new("RGB", (self.W, self.H), (0, 0, 0)).split(), a))

    def _card_geom(self):
        """현재 타이머 카드의 위치·크기. 시계 펼침이면 세로 직사각형."""
        if self.has_clock and self.clock_open:
            w, h = 148, 150           # 세로가 살짝 더 긴 직사각형
        elif self.has_clock:
            w, h = 196, 40
        else:
            w, h = 200, 62
        x0 = (self.W - w) / 2
        y0 = 22
        return {"x0": x0, "y0": y0, "x1": x0 + w, "y1": y0 + h, "w": w, "h": h}

    def _resample(self):
        return Image.NEAREST if self.cfg.get("hard_alpha") else Image.BICUBIC

    def _rotated_hop(self, name, deg):
        """손 파츠를 어깨 앵커 기준으로 회전한 이미지 (1도 단위 캐시)."""
        h = self.hop[name]
        key = round(deg)
        if key not in h["cache"]:
            if len(h["cache"]) > 60:
                h["cache"].clear()
            im = h["pil"].rotate(deg, center=h["anchor"],
                                 resample=self._resample(), expand=False)
            h["cache"][key] = ImageTk.PhotoImage(self._hard(im))
        return h["cache"][key]

    # ── 타자 소리 ─────────────────────────────────────────────────────────
    def _list_packs(self):
        base = os.path.join(self.dir, "sounds")
        if not os.path.isdir(base):
            return []
        return sorted(d for d in os.listdir(base)
                      if os.path.exists(os.path.join(base, d, "config.json")))

    def _init_sound(self):
        if self.sndpack is not None:
            try:
                self.sndpack.close()
            except Exception:
                pass
            self.sndpack = None
        if self.pensnd is not None:
            try:
                self.pensnd.stop()
            except Exception:
                pass
            self.pensnd = None
        self._pen_playing = False
        self._pen_release_t = None
        if not (self.us.get("sound", True) and self.sound_packs):
            return
        name = str(self.us.get("sound_pack") or "")
        if name not in self.sound_packs:
            name = self.sound_packs[0]
        try:
            self.sndpack = SoundPack(os.path.join(self.dir, "sounds", name),
                                     volume=float(self.us.get("sound_volume", 60)))
        except Exception:
            self.sndpack = None
        pen_dir = os.path.join(self.dir, "sounds", "pen")
        if os.path.isdir(pen_dir):
            try:
                self.pensnd = PenSound(
                    pen_dir, volume=float(self.us.get("pen_volume", 30)))
            except Exception:
                self.pensnd = None

    # ── 입력 콜백 ─────────────────────────────────────────────────────────
    def _on_key(self, key):
        self.key_events += 1
        # 꾹 누르고 있을 때의 자동 반복은 소리 제외 — 최초 눌림만 소리
        k = str(key)
        first = k not in self._held
        self._held.add(k)
        sp = self.sndpack
        if first and sp is not None:
            try:
                sp.play(key)
            except Exception:
                pass

    def _on_key_release(self, key):
        self._held.discard(str(key))

    def _on_click(self, _x, _y, _button, pressed):
        self.mouse_pressed = pressed
        self.last_pointer = time.time()
        if not pressed:
            self._new_stroke = True

    def _on_move(self, _x, _y):
        now = time.time()
        self.last_pointer = now
        if self.mouse_pressed:
            self.last_drag = now

    def _on_press(self, e):
        self._press = (e.x, e.y, e.x_root, e.y_root)
        self._dragged = False

    def _on_drag(self, e):
        if self._press is None:
            return
        px, py, prx, pry = self._press
        if not self._dragged and abs(e.x_root - prx) + abs(e.y_root - pry) < 4:
            return
        self._dragged = True
        self.root.geometry(f"+{e.x_root - px}+{e.y_root - py}")

    def _on_release(self, e):
        if self._press is not None and not self._dragged and self.has_clock:
            px, py, _, _ = self._press
            g = self._card_geom()
            if g["x0"] <= px <= g["x1"] and g["y0"] - 17 <= py <= g["y1"]:
                self._toggle_clock()
        self._press = None

    def _toggle_clock(self):
        """시계 펼침/접힘 — 창 높이를 바꾸고(아래 고정) 좌표·그림자 재계산."""
        self.clock_open = not self.clock_open
        self.us["clock_open"] = self.clock_open
        self._save_settings()
        old_oy, old_H = self.oy, self.H
        old_x, old_y = self.root.winfo_x(), self.root.winfo_y()
        self.oy = self._timer_oy()
        self.H = self.ch_px + self.oy
        d = self.oy - old_oy
        self.canvas.config(height=self.H)
        self.root.geometry(f"{self.W}x{self.H}+{old_x}+{old_y - (self.H - old_H)}")
        self._pen_xy[1] += d                 # 좌표계가 d만큼 내려가므로 펜도 이동
        self._bake_oy()
        self._build_shadow_img()
        if self.shadow is not None and self.shadow_img is not None:
            self.shadow.set_image(self.shadow_img)

    def close(self):
        try:
            if self.timer_on and self.ws_path is None:
                self._timer_save()
            self._kb.stop()
            self._ms.stop()
        finally:
            self.root.destroy()

    # ── 타이머 ───────────────────────────────────────────────────────────
    def _timer_load(self):
        # 자동 초기화 없음 — 우클릭 '타이머 초기화'로만 리셋 (확정 방침)
        try:
            with open(self.state_path, encoding="utf-8") as fp:
                st = json.load(fp)
            self.work_secs = float(st.get("seconds", 0))
        except Exception:
            pass

    def _timer_save(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as fp:
                json.dump({"seconds": round(self.work_secs)}, fp)
        except Exception:
            pass

    def _timer_reset(self):
        self.work_secs = 0.0
        self._timer_save()

    def _fg_is_work(self, now):
        """앞 창이 작업 프로그램인지 (1초 캐시)."""
        if now - self._fg_checked > 1.0:
            self._fg_checked = now
            fg = foreground_process()
            apps = [a.strip().lower() for a in
                    str(self.us["work_apps"]).split(",") if a.strip()]
            self._fg_work = any(a == fg or a in fg for a in apps)
        return self._fg_work

    def _timer_tick(self, now, idle):
        """상태 반환: work(측정)/other(작업앱 아님)/idle(휴식)/off(연동 끊김)."""
        if self.ws_path is not None:
            # 워크스페이스 워크타이머 연동: 에이전트의 라이브 파일을 읽어 표시만 한다
            if now - self._ws_read > 1.0:
                self._ws_read = now
                try:
                    with open(self.ws_path, encoding="utf-8") as fp:
                        self._ws_data = json.load(fp)
                except Exception:
                    self._ws_data = None
            d = self._ws_data
            if not d or now - float(d.get("ts", 0)) > 8:
                return "off"          # 워크타이머가 꺼져 있음
            self.work_secs = float(d.get("total", 0))
            if d.get("active"):
                return "work"
            if d.get("idle") or idle >= self.idle_thr:
                return "idle"
            return "other"

        dt = min(max(now - self._t_last, 0.0), 2.0)
        self._t_last = now
        if idle >= self.idle_thr:
            state = "idle"
        elif self.us["work_apps_only"] and not self._fg_is_work(now):
            state = "other"
        else:
            state = "work"
            self.work_secs += dt
        if now - self._t_save > 30:
            self._t_save = now
            self._timer_save()
        return state

    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_deco(self, x0, y0, x1, y1):
        """카드 위 장식(귀 등) — 캐릭터 컨셉별."""
        c = self.canvas
        deco = self.card["deco"]
        if deco == "panda":
            for ex in (x0 + 26, x1 - 26):
                c.create_oval(ex - 12, y0 - 17, ex + 12, y0 + 7,
                              fill="#2b2b2b", outline="")
                c.create_oval(ex - 6, y0 - 11, ex + 6, y0 + 1,
                              fill="#4a4a4a", outline="")
        elif deco == "cat":
            for sign, ex in ((-1, x0 + 26), (1, x1 - 26)):
                c.create_polygon(ex - 13 * sign, y0 + 5, ex + 3 * sign, y0 - 17,
                                 ex + 13 * sign, y0 + 3,
                                 fill="#f5bdd2", outline="#d687ab", width=2)
                c.create_polygon(ex - 6 * sign, y0 + 2, ex + 3 * sign, y0 - 10,
                                 ex + 8 * sign, y0 + 1,
                                 fill="#eba0c0", outline="")
        elif deco == "rose":
            for ex in (x0 + 26, x1 - 26):
                c.create_oval(ex - 12, y0 - 17, ex + 12, y0 + 7,
                              fill="#f5bdd2", outline="#d687ab", width=2)
                c.create_arc(ex - 8, y0 - 13, ex + 8, y0 + 3, start=300,
                             extent=270, style="arc", outline="#d687ab", width=2)

    def _status_of(self, state, sleeping):
        if state == "off":
            return DOT_OFF, "타이머 꺼짐"
        if sleeping:
            return DOT_OFF, "자는 중"
        if state == "work":
            return DOT_ON, "작업중"
        if state == "other":
            return DOT_OTHER, "딴짓 중"
        return DOT_OFF, "쉬는 중"

    def _draw_clock(self, cx, cy, R, now):
        """아날로그 시계 + 작업한 시간을 시계 안쪽에 연한 분홍으로 채움."""
        c = self.canvas
        cd = self.card
        arc_fill = cd.get("arc", "#f7d3e6")
        # 바탕
        c.create_oval(cx - R, cy - R, cx + R, cy + R,
                      fill=cd["bg"], outline=cd["border"], width=2)
        # 작업한 시간 = 12시간 다이얼 위 파이 조각 (연속 구간으로 묶어 채움)
        act = (self._ws_data or {}).get("act") or []
        mods = sorted({(time.localtime(m * 60).tm_hour % 12) * 60
                       + time.localtime(m * 60).tm_min for m in act})
        if mods:
            Rf = R - 2.5
            runs, s0, p0 = [], mods[0], mods[0]
            for v in mods[1:]:
                if v == p0 + 1:
                    p0 = v
                else:
                    runs.append((s0, p0)); s0 = p0 = v
            runs.append((s0, p0))
            for a, b in runs:
                start = 90 - (b + 1) / 720 * 360     # tkinter arc: 0°=3시, 반시계+
                extent = ((b + 1) - a) / 720 * 360
                c.create_arc(cx - Rf, cy - Rf, cx + Rf, cy + Rf, start=start,
                             extent=extent, fill=arc_fill, outline="", style="pieslice")
        # 시각 눈금
        for i in range(12):
            a = math.radians(i * 30 - 90)
            big = i % 3 == 0
            r2 = R - (8 if big else 5)
            c.create_line(cx + (R - 3) * math.cos(a), cy + (R - 3) * math.sin(a),
                          cx + r2 * math.cos(a), cy + r2 * math.sin(a),
                          fill=cd["sub"], width=2 if big else 1)
        lt = time.localtime(now)
        hh = lt.tm_hour % 12 + lt.tm_min / 60
        mm = lt.tm_min + lt.tm_sec / 60

        def hand(frac, length, width, color):
            a = math.radians(frac * 360 - 90)
            c.create_line(cx, cy, cx + length * math.cos(a), cy + length * math.sin(a),
                          width=width, fill=color, capstyle="round")

        hand(hh / 12, R * 0.46, 3, cd["text"])
        hand(mm / 60, R * 0.66, 2, cd["text"])
        hand(lt.tm_sec / 60, R * 0.76, 1, cd["fill"])
        c.create_oval(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5, fill=cd["fill"], outline="")

    def _draw_timer(self, state, sleeping, now):
        c = self.canvas
        cd = self.card
        active = state == "work"
        dot, status = self._status_of(state, sleeping)
        t = int(self.work_secs)
        label = f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
        g = self._card_geom()
        x0, y0, x1, y1 = g["x0"], g["y0"], g["x1"], g["y1"]
        pad = 14

        self._draw_deco(x0, y0, x1, y1)
        self._rrect(x0 + 2, y0 + 3, x1 + 2, y1 + 3, 16, fill="#e3e6ee", outline="")
        self._rrect(x0, y0, x1, y1, 16, fill=cd["bg"], outline=cd["border"], width=2)

        def status_dot(px, py):
            pulse = 1.5 + math.sin(now * 4) * 1.5 if active else 0
            r = 5 + pulse * 0.5
            c.create_oval(px - r, py - r, px + r, py + r, fill=dot, outline="")

        if self.has_clock and self.clock_open:
            # 세로 카드: 상태(위) → 시계(가운데) → 시간(아래) — 모두 정중앙 정렬
            cxm = (x0 + x1) / 2
            tw = self._f_status.measure(status)
            gx = cxm - (16 + tw) / 2            # 점+간격+텍스트 그룹 중앙
            status_dot(gx + 5, y0 + 16)
            c.create_text(gx + 16, y0 + 16, anchor="w", text=status,
                          font=("Malgun Gothic", 8), fill=cd["sub"])
            R = 38
            clock_cy = y0 + 30 + R
            self._draw_clock(cxm, clock_cy, R, now)
            c.create_text(cxm, clock_cy + R + 18, text=label,
                          font=("Malgun Gothic", 14, "bold"), fill=cd["text"])
        elif self.has_clock:
            # 접힘: 상태 + 시간 한 줄 (게이지 없음)
            row = y0 + 20
            status_dot(x0 + pad + 5, row)
            c.create_text(x0 + pad + 16, row, anchor="w", text=status,
                          font=("Malgun Gothic", 8), fill=cd["sub"])
            c.create_text(x1 - pad, row, anchor="e", text=label,
                          font=("Malgun Gothic", 13, "bold"), fill=cd["text"])
        else:
            # 게이지형(준사): 상태+시간 윗줄 + 목표 진행바 아랫줄
            row1 = y0 + 20
            status_dot(x0 + pad + 5, row1)
            c.create_text(x0 + pad + 16, row1, anchor="w", text=status,
                          font=("Malgun Gothic", 8), fill=cd["sub"])
            c.create_text(x1 - pad, row1, anchor="e", text=label,
                          font=("Malgun Gothic", 13, "bold"), fill=cd["text"])
            goal = max(float(self.us["goal_hours"]), 0.5) * 3600
            frac = min(self.work_secs / goal, 1.0)
            row2 = y0 + 45
            bx0, bx1 = x0 + pad + 2, x1 - pad - 36
            c.create_line(bx0, row2, bx1, row2, width=6, capstyle="round",
                          fill=cd["track"])
            if frac > 0.01:
                c.create_line(bx0, row2, bx0 + (bx1 - bx0) * frac, row2,
                              width=6, capstyle="round",
                              fill="#7ccf8f" if frac >= 1.0 else cd["fill"])
            c.create_text(x1 - pad, row2, anchor="e", text=f"{int(frac * 100)}%",
                          font=("Malgun Gothic", 7, "bold"),
                          fill="#5aa86e" if frac >= 1.0 else cd["sub"])

    # ── 매 프레임 갱신 (~30fps) ──────────────────────────────────────────
    def tick(self):
        now = time.time()
        if self.key_events != self._seen_keys:
            self._seen_keys = self.key_events
            self.last_key = now
            self.squash_until = now + 0.10
            pen_typing = now - self.last_pointer > 2.0
            self.tap_side = (not self.tap_side) if pen_typing else False
            if pen_typing and self.tap_side:
                self.pen_ang_t = random.uniform(*PEN_KB_ROT)
                self.pen_down_until = now + 0.09
            else:
                self.key_ang_t = random.uniform(*KEY_ROT)
                self.left_down_until = now + 0.09
        if now >= self.next_blink:
            self.blink_until = now + 0.12
            self.next_blink = now + random.uniform(2.5, 5.5)
        # 그림자: 본체를 따라오고, 주기적으로 z순서(본체 바로 아래) 재고정
        if self.shadow is not None:
            pos = (self.root.winfo_rootx(), self.root.winfo_rooty())
            if pos != self._last_pos or now - self._z_check > 2.0:
                self._last_pos = pos
                self._z_check = now
                self.shadow.place(*pos, self._main_hwnd)
        # 끝난 타자 소리 장치 정리
        if self.sndpack is not None and now - getattr(self, "_snd_reap", 0) > 2.0:
            self._snd_reap = now
            try:
                self.sndpack.reap()
            except Exception:
                pass
        self.draw(now)
        self.root.after(33, self.tick)

    def _quad_xy(self, u, v):
        (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = self.quad
        top = (tlx + (trx - tlx) * u, tly + (try_ - tly) * u)
        bot = (blx + (brx - blx) * u, bly + (bry - bly) * u)
        return (top[0] + (bot[0] - top[0]) * v,
                top[1] + (bot[1] - top[1]) * v)

    def _pos(self, name):
        x, y = self.layout[name]["pos"]
        return x * self.s, y * self.s + self.oy

    def _stretched_arm(self, dx, dy):
        nx, ny = self._arm_nat
        nat_len = math.hypot(nx, ny)
        cur_len = max(math.hypot(dx, dy), 8.0)
        k = cur_len / max(nat_len, 1)
        deg = math.degrees(math.atan2(dx, dy) - math.atan2(nx, ny))
        key = (round(k * 25), round(deg))
        if key not in self._arm_cache:
            if len(self._arm_cache) > 200:
                self._arm_cache.clear()
            w, h = self.arm_pil.size
            im = self.arm_pil.resize((w, max(8, round(h * k))), Image.LANCZOS)
            im = im.rotate(deg, expand=True, resample=self._resample())
            self._arm_cache[key] = ImageTk.PhotoImage(self._hard(im))
        return self._arm_cache[key]

    def draw(self, now):
        c = self.canvas
        c.delete("all")
        f = self._force

        idle = idle_seconds()
        sleeping = idle > max(float(self.us["sleep_min"]), 1) * 60 or f.get("sleep", False)

        if sleeping:
            breathe = math.sin(now * 1.1) * 2.5     # 자는 동안은 느리고 깊게
        else:
            breathe = math.sin(now * 2.0) * 1.5
        squash = 3 if now < self.squash_until else 0
        yo = breathe + squash

        cx, cy = cursor_pos()
        wx = self.root.winfo_rootx() + self.W // 2
        wy = self.root.winfo_rooty() + self.H // 2
        pdx = max(-5, min(5, (cx - wx) / 60))
        pdy = max(-3, min(4, (cy - wy) / 90))

        pen_typing = (now - self.last_pointer > 2.0) and (now - self.last_key < 1.8)
        if "pen" in f or f.get("type"):
            pen_typing = bool(f.get("type"))

        blinking = (sleeping or now < self.blink_until or f.get("blink", False)) \
            and (self.blink_cfg is not None or self.has.get("eyes_closed"))

        if self.timer_on:
            self._draw_timer(self._timer_tick(now, idle), sleeping, now)

        # ── 몸 → 눈동자 → (마스크 몸) → 속눈썹 → 머리카락 ────────────────
        bx, by = self._pos("body_open")
        c.create_image(bx, by + yo, image=self.im["body_open"], anchor="nw")
        if not blinking:
            ex, ey = self._pos("pupils")
            c.create_image(ex + pdx, ey + yo + pdy, image=self.im["pupils"], anchor="nw")
        elif self.blink_cfg is not None:
            (x0, y0, x1, y1), color = self.blink_cfg
            c.create_rectangle(x0, y0 + yo, x1, y1 + yo, fill=color, outline="")
        # 눈동자 위 덮개들 — PSD 스택 순서(layout의 overlays) 그대로
        overlays = self.layout.get("overlays") or \
            ["body_mask", "lashes", "eyes_closed", "hair"]
        for name in overlays:
            if name == "eyes_closed":
                if not (blinking and self.has.get("eyes_closed")):
                    continue
            elif not self.has.get(name):
                continue
            ox, oy_ = self._pos(name)
            c.create_image(ox, oy_ + yo, image=self.im[name], anchor="nw")

        # 수면 모드: 머리 옆에 둥실거리는 zzZ
        if sleeping:
            bw = self.layout["body_open"]["size"][0] * self.s
            zx, zy = bx + bw * 0.86, by + yo + 26
            for i, (dx, dy, size, color) in enumerate((
                    (0, 24, 10, "#aab7cc"),
                    (14, 6, 13, "#93a4c2"),
                    (30, -14, 16, "#7c90b5"))):
                bob = math.sin(now * 1.6 + i * 0.9) * 3
                c.create_text(zx + dx, zy + dy + bob, text="z" if i == 0 else "Z",
                              font=("Malgun Gothic", size, "bold"), fill=color)

        # ── 책상 (+옵션: 화면 낙서) ──────────────────────────────────────
        c.create_image(*self._pos("desk"), image=self.im["desk"], anchor="nw")
        if self.us["trail"]:
            if self.strokes and now - self.last_drag > 12:
                self.strokes = []
            for st in self.strokes:
                if len(st) >= 2:
                    c.create_line(*[v for p in st for v in p],
                                  fill=self.cfg.get("trail_color", "#8fd0ff"),
                                  width=2, smooth=True)
                elif st:
                    px, py = st[0]
                    c.create_oval(px - 1, py - 1, px + 1, py + 1,
                                  fill=self.cfg.get("trail_color", "#8fd0ff"),
                                  outline="")
        else:
            self.strokes = []

        # ── 오른손/오른팔: 펜 추적 또는 타이핑 파츠(어깨 축 회전) ────────
        if pen_typing and "pen" not in f:
            # 양손 타이핑: 왼손을 먼저(아래), 오른팔-타자를 나중(위) 그림
            self._draw_left(now, f)
            self.pen_ang += (self.pen_ang_t - self.pen_ang) * 0.5
            bob = 4 if now < self.pen_down_until else 0
            tx_, ty_ = self._pos("arm_right_typing")
            offx, offy = self.hop["arm_right_typing"]["off"]
            c.create_image(tx_ + offx, ty_ + offy + bob,
                           image=self._rotated_hop("arm_right_typing", self.pen_ang),
                           anchor="nw")
        else:
            if "pen" in f:
                target = self._quad_xy(*f["pen"])
                drawing = True
            else:
                ml, mt, mr, mb = monitor_at(cx, cy)
                u = min(1.0, max(0.0, (cx - ml) / max(mr - ml, 1)))
                v = min(1.0, max(0.0, (cy - mt) / max(mb - mt, 1)))
                target = self._quad_xy(u, v)
                drawing = self.mouse_pressed
            self._pen_xy[0] += (target[0] - self._pen_xy[0]) * 0.55
            self._pen_xy[1] += (target[1] - self._pen_xy[1]) * 0.55
            tx, ty = self._pen_xy
            if drawing:
                if self._new_stroke or not self.strokes:
                    self.strokes.append([])
                    self._new_stroke = False
                self.strokes[-1].append((tx, ty))
                while sum(len(st) for st in self.strokes) > 300:
                    self.strokes.pop(0)
            px, py = self._pos("arm_pen")
            btx, bty = self.pen_base_tip
            ddx, ddy = tx - btx, ty - bty
            sx, sy = self.arm_top[0], self.arm_top[1] + yo * 0.5
            hx_, hy_ = self.arm_bottom[0] + ddx, self.arm_bottom[1] + ddy
            arm_img = self._stretched_arm(hx_ - sx, hy_ - sy)
            c.create_image((sx + hx_) / 2, (sy + hy_) / 2,
                           image=arm_img, anchor="center")
            c.create_image(px + ddx, py + ddy,
                           image=self.im["arm_pen"], anchor="nw")
            self._draw_left(now, f)
            # 연필 사각거림: 시작=클립 한 번, 길게 이어지면=지속음, 떼면 정지
            if self.pensnd is not None and "pen" not in f:
                if drawing:
                    self._pen_release_t = None
                    if not self._pen_playing:
                        self.pensnd.start()
                        self._pen_playing = True
                    else:
                        self.pensnd.sustain(now)
                elif self._pen_playing:
                    # 펜압 흔들림으로 잠깐 떨어지는 것은 무시(70ms 유예)
                    if self._pen_release_t is None:
                        self._pen_release_t = now
                    elif now - self._pen_release_t > 0.07:
                        self.pensnd.stop()
                        self._pen_playing = False

    def _draw_left(self, now, f):
        """왼손(키보드): 어깨 축 회전으로 키를 옮겨가며 타이핑."""
        if now - self.last_key > 2.5:
            self.key_ang_t = 0.0
        self.key_ang += (self.key_ang_t - self.key_ang) * 0.5
        kx, ky = self._pos("arm_key")
        kx += self.arm_key_off[0]
        ky += self.arm_key_off[1]
        offx, offy = self.hop["arm_key"]["off"]
        down = now < self.left_down_until or f.get("type")
        self.canvas.create_image(kx + offx, ky + offy + (4 if down else 0),
                                 image=self._rotated_hop("arm_key", self.key_ang),
                                 anchor="nw")

    # ── 환경설정 창 ──────────────────────────────────────────────────────
    def open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        BG, FG = "#fff7f9", CARD_NAVY
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("환경설정")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.geometry(f"+{self.root.winfo_rootx() - 40}+{self.root.winfo_rooty() + 40}")

        frame = tk.Frame(win, bg=BG, padx=18, pady=14)
        frame.pack()
        tk.Label(frame, text=f"🐼 {self.cfg.get('name', self.char)} 설정",
                 bg=BG, fg=FG, font=("Malgun Gothic", 11, "bold")
                 ).grid(row=0, column=0, columnspan=2, pady=(0, 10))

        def row(r, label):
            tk.Label(frame, text=label, bg=BG, fg=FG,
                     font=("Malgun Gothic", 9)).grid(
                row=r, column=0, sticky="w", pady=3, padx=(0, 12))

        v_goal = tk.DoubleVar(value=float(self.us["goal_hours"]))
        v_idle = tk.DoubleVar(value=float(self.us["idle_sec"]))
        v_sleep = tk.IntVar(value=int(self.us["sleep_min"]))
        v_timer = tk.BooleanVar(value=bool(self.timer_on))
        v_trail = tk.BooleanVar(value=bool(self.us["trail"]))
        v_top = tk.BooleanVar(value=bool(self.us["topmost"]))
        v_scale = tk.IntVar(value=int(self.us["scale_pct"]))
        v_wonly = tk.BooleanVar(value=bool(self.us["work_apps_only"]))
        v_apps = tk.StringVar(value=str(self.us["work_apps"]))
        v_shadow = tk.BooleanVar(value=bool(self.us.get("shadow", True)))
        v_sound = tk.BooleanVar(value=bool(self.us.get("sound", True)))
        v_vol = tk.IntVar(value=int(self.us.get("sound_volume", 60)))
        v_pen = tk.IntVar(value=int(self.us.get("pen_volume", 30)))
        cur_pack = str(self.us.get("sound_pack") or "")
        if cur_pack not in self.sound_packs and self.sound_packs:
            cur_pack = self.sound_packs[0]
        v_pack = tk.StringVar(value=cur_pack)

        row(1, "목표 작업시간 (시간)")
        tk.Spinbox(frame, from_=0.5, to=16, increment=0.5, width=6,
                   textvariable=v_goal).grid(row=1, column=1, sticky="w")
        row(2, "휴식 전환 (초)")
        tk.Spinbox(frame, from_=5, to=600, increment=5, width=6,
                   textvariable=v_idle).grid(row=2, column=1, sticky="w")
        row(3, "잠들기 (분)")
        tk.Spinbox(frame, from_=1, to=120, increment=1, width=6,
                   textvariable=v_sleep).grid(row=3, column=1, sticky="w")
        row(4, "캐릭터 크기 (%)")
        tk.Spinbox(frame, from_=50, to=200, increment=10, width=6,
                   textvariable=v_scale).grid(row=4, column=1, sticky="w")
        row(5, "타자 소리 볼륨 (%)")
        tk.Spinbox(frame, from_=0, to=100, increment=5, width=6,
                   textvariable=v_vol).grid(row=5, column=1, sticky="w")
        row(6, "펜 소리 볼륨 (%)")
        tk.Spinbox(frame, from_=0, to=100, increment=5, width=6,
                   textvariable=v_pen).grid(row=6, column=1, sticky="w")
        for r, (label, var) in enumerate([("작업 타이머 표시", v_timer),
                                          ("작업 프로그램에서만 시간 측정", v_wonly),
                                          ("타자 소리 (Mechvibes 팩)", v_sound),
                                          ("캐릭터 그림자", v_shadow),
                                          ("타블렛 낙서 표시", v_trail),
                                          ("항상 위에 표시", v_top)], start=7):
            tk.Checkbutton(frame, text=label, variable=var, bg=BG, fg=FG,
                           activebackground=BG, font=("Malgun Gothic", 9)
                           ).grid(row=r, column=0, columnspan=2, sticky="w")
        if self.sound_packs:
            row(13, "타자 소리 팩")
            om = tk.OptionMenu(frame, v_pack, *self.sound_packs)
            om.configure(bg="#ffffff", font=("Malgun Gothic", 8),
                         relief="flat", highlightthickness=1)
            om.grid(row=14, column=0, columnspan=2, sticky="we", pady=(0, 2))
        row(15, "작업 프로그램 (쉼표 구분)")
        tk.Entry(frame, textvariable=v_apps, width=26,
                 font=("Malgun Gothic", 8)).grid(row=16, column=0, columnspan=2,
                                                 sticky="we", pady=(0, 2))

        info = tk.Label(frame, text="크기·타이머·그림자 변경은 저장 시 재시작됩니다",
                        bg=BG, fg="#b0a3ab", font=("Malgun Gothic", 8))
        info.grid(row=17, column=0, columnspan=2, pady=(8, 2))

        def save():
            try:
                new = {"goal_hours": float(v_goal.get()),
                       "idle_sec": max(float(v_idle.get()), 5.0),
                       "sleep_min": max(1, int(v_sleep.get())),
                       "show_timer": bool(v_timer.get()),
                       "trail": bool(v_trail.get()),
                       "topmost": bool(v_top.get()),
                       "scale_pct": max(50, min(200, int(v_scale.get()))),
                       "work_apps_only": bool(v_wonly.get()),
                       "work_apps": v_apps.get().strip(),
                       "shadow": bool(v_shadow.get()),
                       "sound": bool(v_sound.get()),
                       "sound_volume": max(0, min(100, int(v_vol.get()))),
                       "pen_volume": max(0, min(100, int(v_pen.get()))),
                       "sound_pack": v_pack.get()}
            except Exception:
                return
            need_restart = (new["scale_pct"] != self.us["scale_pct"]
                            or new["show_timer"] != self.timer_on
                            or new["shadow"] != bool(self.us.get("shadow", True)))
            self.us.update(new)
            self._save_settings()
            # 즉시 반영 가능한 항목
            self.idle_thr = self.us["idle_sec"]
            self.root.attributes("-topmost", bool(self.us["topmost"]))
            self._init_sound()
            win.destroy()
            if need_restart:
                self._restart()

        tk.Button(frame, text="저장", command=save, width=10,
                  bg=CARD_BORDER, fg="#5b3a44", relief="flat",
                  font=("Malgun Gothic", 9, "bold")).grid(
            row=18, column=0, columnspan=2, pady=(6, 0))

    def _save_settings(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fp:
                json.dump(self.us, fp, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _restart(self):
        import subprocess
        if self.timer_on:
            self._timer_save()
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__),
                              "--char", self.char_arg])
        self.close()

    # ── 프리뷰 ───────────────────────────────────────────────────────────
    def _preview_shots(self):
        from PIL import ImageGrab
        shots = [
            (f"preview_{self.char}_idle.png", {}),
            (f"preview_{self.char}_typing.png", {"type": True}),
            (f"preview_{self.char}_pen.png", {"pen": (0.35, 0.45)}),
            (f"preview_{self.char}_pen_corner.png", {"pen": (0.02, 0.95)}),
            (f"preview_{self.char}_blink.png", {"blink": True}),
            (f"preview_{self.char}_sleep.png", {"sleep": True}),
        ]
        for name, force in shots:
            self._force = force
            if force.get("type"):
                self.key_ang = 5.0
                self.pen_ang = -4.0
            if "pen" in force:
                self._pen_xy = list(self._quad_xy(*force["pen"]))
                self.strokes = [[self._quad_xy(0.25 + 0.15 * i, 0.35 + 0.12 * (i % 2))
                                 for i in range(5)]]
            self.draw(time.time())
            self.root.update()
            time.sleep(0.15)
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + self.W, y + self.H)).save(
                os.path.join(HERE, name))
            print("saved", name)
        self.close()

    def run(self):
        self.root.mainloop()


def _arg(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


if __name__ == "__main__":
    Mascot(char_dir=_arg("--char", "parts"),
           preview="--preview" in sys.argv).run()
