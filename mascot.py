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

from PIL import Image, ImageTk

if sys.platform == "darwin":
    # 맥에서는 pynput을 쓰지 않는다. pynput의 맥 리스너는 별도 스레드에서
    # HIToolbox의 TSMGetInputSourceProperty를 호출하는데, macOS 26부터 이 API가
    # 메인 큐 밖 호출을 금지해 앱이 즉사한다 (퀸시 크래시 로그로 확인).
    keyboard = mouse = None
else:
    from pynput import keyboard, mouse

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

_MAC_CG = None
if IS_MAC:                       # 커서·유휴 시간을 얻는 macOS 프레임워크
    try:
        from ctypes import util as _cutil
        _MAC_CG = ctypes.cdll.LoadLibrary(_cutil.find_library("CoreGraphics"))
        _MAC_CG.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
        _MAC_CG.CGEventSourceSecondsSinceLastEventType.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32]
    except Exception:
        _MAC_CG = None

if getattr(sys, "frozen", False) and not os.path.exists(os.path.abspath(__file__)):
    # PyInstaller 번들 내부에서 임포트된 경우 (자동 업데이트로 받은 파일이면
    # __file__이 실제 디스크에 존재하므로 그 폴더를 기준으로 삼는다)
    HERE = sys._MEIPASS
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
TRANSPARENT = "#010203"          # 투명 키 색

KEY_ROT = (-7.0, 7.0)            # 타이핑 시 손 회전(어깨 축) 범위 (도)
PEN_KB_ROT = (-6.0, 6.0)
SHADOW_PAD = 16                  # 그림자 이미지 여백 (가장자리 파츠 잘림 방지)
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
    "autostart": True,    # 윈도우 시작 시 자동 실행 (exe로 배포된 경우만 적용)
    "sound": True,        # 타자 소리 (Mechvibes 팩)
    "sound_volume": 60,   # 타자 소리 볼륨 (0~100)
    "pen_volume": 10,     # 펜 긋는 소리 볼륨 (0~100)
    "sound_pack": "banana split lubed",
    "skin": "기본",        # 패션 슬롯 이름
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


# 샘플 폭(바이트)별 정수 타입 — 볼륨을 샘플에 곱할 때 사용
_SAMPLE_CTYPE = {1: ctypes.c_int8, 2: ctypes.c_int16, 4: ctypes.c_int32}


def _scaled_buffer(data, gain, sampwidth):
    """PCM 바이트에 gain을 곱한 재생 버퍼. 비트 심도(16/32bit)에 맞춰 스케일.

    waveOutSetVolume이 드라이버에 무시될 수 있어 샘플 값 자체를 조절한다.
    """
    buf = ctypes.create_string_buffer(data, len(data))
    ct = _SAMPLE_CTYPE.get(sampwidth)
    if gain < 0.999 and ct is not None and len(data) >= sampwidth:
        n = len(data) // sampwidth
        arr = (ct * n).from_buffer(buf)
        for i in range(n):
            arr[i] = int(arr[i] * gain)
    return buf


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
        self.raw = []             # (WAVEFORMATEX, 원본PCM, 샘플폭)
        for name in names:
            path = os.path.join(folder, name)
            if not (name.lower().endswith(".wav") and os.path.exists(path)):
                continue
            with wave.open(path, "rb") as w:
                ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                data = w.readframes(w.getnframes())
            wfx = _WAVEFORMATEX(1, ch, fr, fr * ch * sw, ch * sw, sw * 8, 0)
            self.raw.append((wfx, data, sw))
        if not self.raw:
            raise ValueError("재생 가능한 wav가 없음")
        self._active = []         # (핸들, WAVEHDR) — 재생 끝나면 정리
        self._lock = threading.Lock()
        self.set_volume(volume)

    def set_volume(self, volume):
        """볼륨(0~100)을 샘플에 곱해 재생용 버퍼 준비 (드라이버 볼륨 무시 대비)."""
        self.volume = max(0.0, min(float(volume), 100.0))
        gain = self.volume / 100.0
        self.sounds = []          # (WAVEFORMATEX, 버퍼, 길이)
        if gain <= 0.0:
            return
        for wfx, data, sw in self.raw:
            self.sounds.append((wfx, _scaled_buffer(data, gain, sw), len(data)))

    def play(self, key):
        if not self.sounds:
            return
        wfx, buf, ln = self.sounds[hash(str(key)) % len(self.sounds)]
        wm = ctypes.windll.winmm
        h = ctypes.c_void_p()
        if wm.waveOutOpen(ctypes.byref(h), 0xFFFFFFFF, ctypes.byref(wfx), 0, 0, 0):
            return
        wm.waveOutSetVolume(h, 0xFFFFFFFF)   # 앱/장치 볼륨 고정 해제 (실볼륨은 샘플로)
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
    """선을 긋기 시작할 때 스크리블 클립 하나를 랜덤 재생 (한 번 '슥').

    짧은 선이든 긴 선이든 스트로크마다 클립 하나. 지속음(bed) 없음.
    볼륨은 waveOutSetVolume이 드라이버에 무시될 수 있어(장치별 볼륨 미지원)
    샘플 값 자체에 곱해 확실히 적용한다. 0이면 아예 재생하지 않는다.
    """

    def __init__(self, folder, volume=35):
        import wave
        self.raw = []             # (WAVEFORMATEX, 원본PCM, 샘플폭)
        names = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".wav")]
        clips = [f for f in names if f.lower().startswith("clip")] or names
        for f in clips:
            with wave.open(os.path.join(folder, f), "rb") as w:
                ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
                data = w.readframes(w.getnframes())
            wfx = _WAVEFORMATEX(1, ch, fr, fr * ch * sw, ch * sw, sw * 8, 0)
            self.raw.append((wfx, data, sw))
        if not self.raw:
            raise ValueError("펜 소리 wav 없음")
        self.set_volume(volume)
        self._cur = None          # (핸들, WAVEHDR, 버퍼)

    def set_volume(self, volume):
        """볼륨(0~100)을 샘플에 곱해 재생용 버퍼를 준비."""
        self.volume = max(0.0, min(float(volume), 100.0))
        gain = self.volume / 100.0
        self.clips = []           # (WAVEFORMATEX, 버퍼, 길이)
        if gain <= 0.0:
            return                # 무음이면 버퍼 안 만듦 → play()가 그냥 반환
        for wfx, data, sw in self.raw:
            self.clips.append((wfx, _scaled_buffer(data, gain, sw), len(data)))

    def play(self):
        """랜덤 클립 하나 재생 (선 긋기 시작 시). 볼륨 0이면 무음."""
        if not self.clips:
            return
        wm = ctypes.windll.winmm
        if self._cur is not None:
            self._release(self._cur)
            self._cur = None
        wfx, buf, ln = random.choice(self.clips)
        h = ctypes.c_void_p()
        if wm.waveOutOpen(ctypes.byref(h), 0xFFFFFFFF, ctypes.byref(wfx), 0, 0, 0):
            return
        wm.waveOutSetVolume(h, 0xFFFFFFFF)   # 앱/장치 볼륨 고정 해제 (실볼륨은 샘플로)
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = ln
        wm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        wm.waveOutWrite(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        self._cur = (h, hdr, buf)

    def stop(self):
        if self._cur is not None:
            self._release(self._cur)
            self._cur = None

    @staticmethod
    def _release(dev):
        wm = ctypes.windll.winmm
        h, hdr, _buf = dev
        wm.waveOutReset(h)
        wm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        wm.waveOutClose(h)


class _MacSoundPool:
    """macOS 소리 재생 — NSSound 사본을 돌려가며 겹쳐 재생한다.

    winmm(waveOut)은 윈도우 전용이라 맥에서는 AppKit의 NSSound를 쓴다.
    같은 NSSound를 연속 호출하면 이어붙지 않고 다시 시작되므로, 파일마다
    사본을 몇 개 두고 번갈아 재생해 타자처럼 빠른 연타도 겹치게 한다.
    """

    COPIES = 3

    def __init__(self, paths, volume):
        from AppKit import NSSound
        self.pool = []
        for p in paths:
            row = []
            for _ in range(self.COPIES):
                snd = NSSound.alloc().initWithContentsOfFile_byReference_(p, True)
                if snd is not None:
                    row.append(snd)
            if row:
                self.pool.append(row)
        if not self.pool:
            raise ValueError("재생 가능한 wav가 없음")
        self._turn = [0] * len(self.pool)
        self.set_volume(volume)

    def set_volume(self, volume):
        self.volume = max(0.0, min(float(volume), 100.0))
        g = self.volume / 100.0
        for row in self.pool:
            for snd in row:
                try:
                    snd.setVolume_(g)
                except Exception:
                    pass

    def _fire(self, idx):
        if self.volume <= 0 or not self.pool:
            return
        row = self.pool[idx % len(self.pool)]
        snd = row[self._turn[idx % len(self.pool)] % len(row)]
        self._turn[idx % len(self.pool)] += 1
        try:
            if snd.isPlaying():
                snd.stop()
            snd.play()
        except Exception:
            pass

    def _all_stop(self):
        for row in self.pool:
            for snd in row:
                try:
                    snd.stop()
                except Exception:
                    pass


class MacSoundPack(_MacSoundPool):
    """맥용 Mechvibes 팩 재생기 (SoundPack과 같은 인터페이스)."""

    def __init__(self, folder, volume=60):
        with open(os.path.join(folder, "config.json"), encoding="utf-8") as fp:
            cfg = json.load(fp)
        if cfg.get("key_define_type", "multi") != "multi":
            raise ValueError("single 타입 팩 미지원")
        names, paths = [], []
        for v in cfg.get("defines", {}).values():
            if isinstance(v, str) and v and v not in names:
                names.append(v)
        for name in names:
            p = os.path.join(folder, name)
            if name.lower().endswith(".wav") and os.path.exists(p):
                paths.append(p)
        super().__init__(paths, volume)

    def play(self, key):
        self._fire(hash(str(key)) % max(len(self.pool), 1))

    def reap(self):
        pass                      # NSSound는 스스로 정리된다

    def close(self):
        self._all_stop()


class MacPenSound(_MacSoundPool):
    """맥용 펜 긋는 소리 (PenSound와 같은 인터페이스)."""

    def __init__(self, folder, volume=35):
        names = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".wav")]
        clips = [f for f in names if f.lower().startswith("clip")] or names
        paths = [os.path.join(folder, f) for f in clips]
        if not paths:
            raise ValueError("펜 소리 wav 없음")
        super().__init__(paths, volume)

    def play(self):
        self._fire(random.randrange(len(self.pool)))

    def stop(self):
        self._all_stop()


if IS_MAC:                        # 맥에서는 같은 이름으로 맥 구현을 쓴다
    SoundPack, PenSound = MacSoundPack, MacPenSound


if IS_WIN:
    ctypes.windll.user32.MonitorFromPoint.argtypes = [_POINT, ctypes.c_uint32]
    ctypes.windll.user32.MonitorFromPoint.restype = ctypes.c_void_p

_TK_ROOT = None                  # 맥에서 커서·화면 크기를 Tk로 얻기 위한 참조


class MacInput:
    """맥 입력 감지 — 리스너 스레드 대신 CoreGraphics 카운터를 매 프레임 읽는다.

    운영체제가 세어 둔 이벤트 개수를 그냥 조회하는 방식이라
      · 백그라운드 스레드가 없고 (크래시 원인 제거)
      · 손쉬운 사용 권한이 필요 없으며
      · 어느 스레드에서 불러도 안전하다.
    어떤 키가 눌렸는지는 알 수 없지만, 이 프로그램은 '몇 번 눌렸는가'만 쓴다.
    """

    HID = 1                      # kCGEventSourceStateHIDSystemState (실제 하드웨어)
    KEY_DOWN = 10
    MOVED, L_DRAG, R_DRAG = 5, 6, 7

    def __init__(self):
        cg = _MAC_CG
        if cg is None:
            raise RuntimeError("CoreGraphics 없음")
        cg.CGEventSourceCounterForEventType.restype = ctypes.c_uint32
        cg.CGEventSourceCounterForEventType.argtypes = [ctypes.c_uint32,
                                                        ctypes.c_uint32]
        cg.CGEventSourceButtonState.restype = ctypes.c_bool
        cg.CGEventSourceButtonState.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.cg = cg
        self.keys = self._count(self.KEY_DOWN)
        self.moves = self._moves()

    def _count(self, ev):
        return int(self.cg.CGEventSourceCounterForEventType(self.HID, ev))

    def _moves(self):
        return sum(self._count(e) for e in (self.MOVED, self.L_DRAG, self.R_DRAG))

    def read(self):
        """(눌린 키 수, 커서 움직임 수, 왼쪽 버튼 눌림) — 지난 호출 이후 변화량."""
        keys, moves = self._count(self.KEY_DOWN), self._moves()
        dk, self.keys = max(keys - self.keys, 0), keys
        dm, self.moves = max(moves - self.moves, 0), moves
        return dk, dm, bool(self.cg.CGEventSourceButtonState(self.HID, 0))


def cursor_pos():
    if IS_WIN:
        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    if _TK_ROOT is not None:      # 맥: Tk가 전역 커서 좌표를 알려준다
        try:
            return _TK_ROOT.winfo_pointerxy()
        except Exception:
            pass
    return 0, 0


def idle_seconds():
    """마지막 입력(마우스·키보드·펜) 이후 경과 초."""
    try:
        if IS_WIN:
            info = _LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
            return max(ctypes.windll.kernel32.GetTickCount() - info.dwTime, 0) / 1000.0
        if _MAC_CG is not None:
            # kCGEventSourceStateCombinedSessionState=0, kCGAnyInputEventType=0xFFFFFFFF
            return float(_MAC_CG.CGEventSourceSecondsSinceLastEventType(
                0, 0xFFFFFFFF))
    except Exception:
        pass
    return 0.0


def foreground_process():
    """앞에 떠 있는 창의 프로세스 실행파일 이름 (소문자). 실패 시 ''."""
    try:
        if IS_WIN:
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            hwnd = u.GetForegroundWindow()
            pid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFO
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = ctypes.c_ulong(260)
                if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return os.path.basename(buf.value).lower()
            finally:
                k.CloseHandle(h)
        elif IS_MAC:
            return _mac_front_app()
    except Exception:
        pass
    return ""


def _mac_front_app():
    """맨 앞 앱 이름 (소문자). PyObjC가 있으면 그걸로, 없으면 빈 문자열."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        name = app.localizedName() or app.bundleIdentifier() or ""
        return str(name).lower()
    except Exception:
        return ""


def monitor_at(x, y):
    if IS_WIN:
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
    if _TK_ROOT is not None:      # 맥: 주 화면 기준 (다중 모니터는 추후)
        try:
            return (0, 0, _TK_ROOT.winfo_screenwidth(),
                    _TK_ROOT.winfo_screenheight())
        except Exception:
            pass
    return 0, 0, 1920, 1080


UPDATE_REPOS = {                 # 선물 캐릭터 자동 업데이트 배포 레포
    "parts_junsa": "rlfqjxm0-create/junsa-mascot",
    "parts_dog": "rlfqjxm0-create/dog-mascot",
    "parts_quincy": "rlfqjxm0-create/quincy-mascot",
    "parts_dororong_gift": "rlfqjxm0-create/dororong-mascot",
}


UPDATE_FLAG = ".updated"          # 업데이트 알림 신호 파일


def mark_updated(state_dir, restart):
    """업데이트 사실을 남긴다. restart=True면 껐다 켜야 반영되는 경우."""
    try:
        with open(os.path.join(state_dir, UPDATE_FLAG), "w",
                  encoding="utf-8") as fp:
            json.dump({"restart": bool(restart)}, fp)
    except Exception:
        pass


def _take_update_flag(state_dir):
    """신호를 읽고 지운다 — 한 번만 알리기 위해."""
    p = os.path.join(state_dir, UPDATE_FLAG)
    if not os.path.exists(p):
        return None
    restart = False
    try:
        with open(p, encoding="utf-8") as fp:
            restart = bool(json.load(fp).get("restart"))
    except Exception:
        pass
    try:
        os.remove(p)
    except Exception:
        pass
    return ("업데이트 됐어요! 껐다 켜주세요" if restart
            else "새 버전으로 업데이트 됐어요!")


def _parts_broken(char_dir):
    """layout.json과 실제 PNG가 어긋나 있는지 = 업데이트가 중간에 끊긴 상태.

    기본 파츠뿐 아니라 패션 슬롯(skins/*) 폴더도 함께 확인한다.
    """
    dirs = [char_dir]
    skins = os.path.join(char_dir, "skins")
    if os.path.isdir(skins):
        dirs += [os.path.join(skins, d) for d in os.listdir(skins)
                 if os.path.isdir(os.path.join(skins, d))]
    for d in dirs:
        try:
            with open(os.path.join(d, "layout.json"), encoding="utf-8") as fp:
                layout = json.load(fp)
        except Exception:
            return True
        for name, info in layout.items():
            if not isinstance(info, dict) or "size" not in info:
                continue
            p = os.path.join(d, f"{name}.png")
            if not os.path.exists(p):
                return True
            try:
                with Image.open(p) as im:
                    if list(im.size) != list(info["size"]):
                        return True
            except Exception:
                return True
    return False


def repair_parts(char_dir, state_dir=None):
    """파츠가 섞여 있으면 배포 레포에서 다시 받아 맞춘다 (선물 exe 전용).

    자동 업데이트가 파일 하나씩 덮어쓰는 방식이라, 도중에 네트워크가 끊기면
    새 PNG + 옛 layout.json 처럼 섞인 상태로 남아 캐릭터가 깨져 보인다.
    실행할 때마다 정합성을 확인하고, 어긋나 있으면 여기서 복구한다.
    """
    repo = UPDATE_REPOS.get(os.path.basename(char_dir))
    if not (repo and getattr(sys, "frozen", False)):
        return                              # 개발 환경에서는 건드리지 않는다
    base_dir = os.path.dirname(char_dir)
    done = os.path.exists(os.path.join(base_dir, "version.json"))
    if done and not _parts_broken(char_dir):
        return                              # 정상 — 네트워크 접근 없음
    import hashlib
    import urllib.parse
    import urllib.request
    base = base_dir

    def fetch(rel):
        # 공백이 든 음원 폴더 경로 때문에 URL 인코딩이 필요하다
        url = (f"https://raw.githubusercontent.com/{repo}/main/"
               f"{urllib.parse.quote(rel, safe='/')}")
        req = urllib.request.Request(url, headers={"User-Agent": "mascot-repair"})
        for i in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.read()
            except Exception:
                if i == 2:
                    raise
                time.sleep(1.0)

    changed = []
    try:
        man = json.loads(fetch("version.json").decode("utf-8"))
        for rel, want in man.get("files", {}).items():
            p = os.path.join(base, rel.replace("/", os.sep))
            try:
                with open(p, "rb") as fp:
                    if hashlib.sha256(fp.read()).hexdigest() == want:
                        continue
            except Exception:
                pass
            data = fetch(rel)
            if hashlib.sha256(data).hexdigest() != want:
                return                      # 내려받은 게 손상 — 다음 실행에 재시도
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fp:
                fp.write(data)
            changed.append(rel)
        with open(os.path.join(base, "version.json"), "w", encoding="utf-8") as fp:
            json.dump(man, fp)
        if changed:
            # mascot.py는 이미 메모리에 올라와 있어 껐다 켜야 반영된다
            mark_updated(state_dir or char_dir, "mascot.py" in changed)
    except Exception:
        pass                                # 오프라인이면 있는 그대로 실행


class Mascot:
    def __init__(self, char_dir="parts", preview=False, state_dir=None):
        self.char_arg = char_dir
        self.dir = os.path.join(HERE, char_dir)
        self.char = os.path.basename(char_dir)
        # 설정·타이머 기록 저장 위치 (자동 업데이트로 교체되지 않는 곳으로 분리 가능)
        self.state_dir = state_dir or self.dir
        os.makedirs(self.state_dir, exist_ok=True)
        # 업데이트가 끊겨 파츠가 섞였으면 복구 (알림 신호도 여기서 남는다)
        repair_parts(self.dir, self.state_dir)
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
        self._sanitize_settings()

        # 패션(스킨) 슬롯 — 파츠만 다른 폴더에서 읽고 설정·기록은 그대로 공유
        self.skins = self.cfg.get("skins") or [{"name": "기본", "dir": ""}]
        self.skin_names = [s.get("name") or f"슬롯 {i + 1}"
                           for i, s in enumerate(self.skins)]
        want = self.us.get("skin")
        idx = self.skin_names.index(want) if want in self.skin_names else 0
        sub = (self.skins[idx].get("dir") or "").strip()
        self.skin_name = self.skin_names[idx]
        self.parts_dir = os.path.join(self.dir, *sub.split("/")) if sub else self.dir
        if not os.path.exists(os.path.join(self.parts_dir, "layout.json")):
            self.parts_dir, self.skin_name = self.dir, self.skin_names[0]
        self.us["skin"] = self.skin_name
        with open(os.path.join(self.parts_dir, "layout.json"), encoding="utf-8") as fp:
            self.layout = json.load(fp)

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
            # 설정·브리핑 창 배경 (캐릭터 테마에 맞춰 바꿀 수 있게)
            "panel": cc.get("panel", "#fffdfe"),
            "soft": cc.get("soft", "#fbf3f7"),
            "line": cc.get("line", "#f0e6ec"),
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
        globals()["_TK_ROOT"] = self.root      # 커서·화면 크기 조회용
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.us["topmost"]))
        # 투명 배경: 윈도우는 색상키, 맥은 Tk의 진짜 투명 속성
        bg = TRANSPARENT
        if IS_MAC:
            bg = self._setup_mac_window()
        else:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        self.canvas_bg = bg
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{self.W}x{self.H}+{sw - self.W - 50}+{sh - self.H - 70}")

        kw = {"bg": bg} if bg else {}
        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                highlightthickness=0, **kw)
        self.canvas.pack()
        if IS_MAC:                            # 제목 표시줄 제거 후 위치 재적용
            self._mac_borderless()
            self.root.geometry(
                f"{self.W}x{self.H}+{sw - self.W - 50}+{sh - self.H - 70}")

        self._tw_cache = {}          # 상태 텍스트 폭 캐시 (캔버스로 측정)

        # ── 귀여운 이벤트 (선물 캐릭터 전용 — config의 "fun") ────────────
        self.fun = bool(self.cfg.get("fun"))
        self.bubble = None           # (텍스트, 사라질 시각)
        self.particles = []          # 폭죽 조각 [x, y, vx, vy, 색, 수명]
        self.hat_until = 0.0         # 고깔모자 표시 종료 시각
        self.smile_until = 0.0       # 웃는 표정 종료 시각
        self.celebrate_until = 0.0   # 축하 연출 종료 시각
        self._fail = {}              # 구역별 실패 횟수 (3회면 그 구역만 끔)
        # 기록 갱신 축하 — '오늘'의 기준은 시각이 아니라 한 세션
        # (작업 시작 ~ '작업 종료' 버튼). 종료하면 새 세션으로 다시 센다.
        self.rec = {"strokes": [], "focus": 0.0}
        self._rec_prev_run = 0.0
        self._rec_armed = True       # 이번 집중 구간에서 아직 축하 안 함
        self._rec_next = 0.0         # 축하 쿨다운 (연달아 뜨지 않게)
        self._update_msg = _take_update_flag(self.state_dir)
        self.shadow_img_type = None  # 타자 자세용 그림자 (깃펜 없음)
        self._shadow_base = None
        self._shadow_typing = False
        self._pen_draw = None        # 펜 손을 머리 뒤에 그릴 때 쓰는 임시 보관
        self._pet_drawn = []         # 이번 프레임에 그린 반려동물 (그림자용)
        self._pet_sh_cache = {}
        self._pet_sh_on = False
        self._pet_sh_t = 0.0
        self.click_bounce = 0.0      # 클릭 반응 튀어오름 종료 시각
        self.pet_t0 = 0.0            # 반려동물 등장 시작(0=쉬는 중)
        _now = time.time()
        self.next_talk = _now + random.uniform(90, 200)
        self.next_pet = _now + random.uniform(30, 80)   # 첫 인사는 좀 이르게
        # 하루 브리핑용 집계
        self.stat = {"work": 0.0, "other": 0.0, "idle": 0.0, "keys": 0,
                     "strokes": 0, "best": 0.0, "_run": 0.0,
                     "first": 0.0, "last": 0.0}

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
        self._key_times = {}          # 키별 마지막 눌림 시각 (다이얼 연타 감지)
        self._kb = self._ms = self._macin = None
        if keyboard is not None:                  # 윈도우: 전역 후킹 리스너
            self._kb = keyboard.Listener(on_press=self._on_key,
                                         on_release=self._on_key_release)
            self._ms = mouse.Listener(on_click=self._on_click,
                                      on_move=self._on_move)
            self._kb.daemon = self._ms.daemon = True
            self._kb.start()
            self._ms.start()
        else:                                     # 맥: 매 프레임 카운터 폴링
            try:
                self._macin = MacInput()
            except Exception:
                self._macin = None

        # ── 그림자 레이어 창 ─────────────────────────────────────────────
        self.root.update_idletasks()
        self._main_hwnd = int(self.root.wm_frame(), 16) if IS_WIN else 0
        self.shadow = None
        self._z_check = 0.0
        if self.shadow_img is not None and IS_WIN:
            # 그림자 이미지가 P만큼 여백을 두므로, 창을 (offset - P)에 놓아 정렬
            self.shadow = ShadowLayer(self.root, self.shadow_img,
                                      offset=(7 - SHADOW_PAD, 9 - SHADOW_PAD))
            self.shadow.place(self.root.winfo_rootx(), self.root.winfo_rooty(),
                              self._main_hwnd)
        self._last_pos = None

        self._apply_autostart()          # exe 배포본이면 시작프로그램 등록

        if os.environ.get("MASCOT_DEBUG") == "1":
            self.root.after(4000, self._dump_debug)

        if preview:
            self.root.after(600, self._preview_shots)
        else:
            self.tick()

    # ── 파츠 로드 (모든 좌표는 표시 배율 + y 오프셋 적용) ─────────────────
    def _hard(self, im):
        """반투명 가장자리 픽셀 이분화 — 색상키 투명의 어두운 테두리(fringe) 방지.

        밝은 캐릭터가 어두운 배경에서 회색 테두리가 지는 문제를, 가장자리 알파를
        50% 기준으로 켜고 끄는 이분화로 없앤다(부드럽진 않지만 테두리가 안 생김).
        """
        if not self.cfg.get("hard_alpha"):
            return im
        from PIL import ImageChops, ImageFilter
        im = self._avoid_key(im)
        a = im.getchannel("A")
        solid = a.point(lambda v: 255 if v >= 128 else 0)
        # 반투명하게 그려진 '내부 선'(옅은 음영 등)은 살린다 — 주변이 대부분
        # 불투명하면 실루엣 안쪽이라는 뜻. 알파 0인 진짜 빈틈은 건드리지 않는다.
        near = solid.filter(ImageFilter.GaussianBlur(2))
        inner = ImageChops.multiply(
            a.point(lambda v: 255 if 0 < v < 128 else 0),
            near.point(lambda v: 255 if v >= 150 else 0))
        im = im.copy()
        im.putalpha(self._fill_holes(ImageChops.lighter(solid, inner)))
        return im

    @staticmethod
    def _avoid_key(im):
        """투명 색상키와 똑같은 색의 픽셀을 1만큼 비껴 놓는다.

        창 투명화는 이 색을 통째로 뚫으므로, 그림 안에 우연히 같은 색이 있으면
        그 점만 배경이 비쳐 흰 점처럼 보인다(퀸시 얼굴 흰 점 사건).
        """
        from PIL import ImageChops
        kr, kg, kb = (int(TRANSPARENT[i:i + 2], 16) for i in (1, 3, 5))
        r, g, b, al = im.split()
        eq = ImageChops.multiply(
            ImageChops.multiply(r.point(lambda v: 255 if v == kr else 0),
                                g.point(lambda v: 255 if v == kg else 0)),
            b.point(lambda v: 255 if v == kb else 0))
        if not eq.getbbox():
            return im
        bump = eq.point(lambda v: 1 if v else 0)
        nb = ImageChops.add(b, bump) if kb < 255 else ImageChops.subtract(b, bump)
        return Image.merge("RGBA", (r, g, nb, al))

    @staticmethod
    def _fill_holes(solid):
        """실루엣 '안쪽'의 투명 구멍만 메운다.

        얼굴의 옅은 음영선처럼 반투명하게 그려진 내부 선은 이분화하면 구멍이
        되어, 밝은 배경에서 흰 점·선으로 비쳐 보인다(퀸시 사건). 바깥과
        이어지지 않은 투명 영역만 채우므로 실루엣 모양은 그대로 유지된다.
        """
        from PIL import ImageChops, ImageDraw
        w, h = solid.size
        pad = Image.new("L", (w + 2, h + 2), 0)
        pad.paste(solid, (1, 1))
        ImageDraw.floodfill(pad, (0, 0), 128)        # 바깥 투명 영역만 표시
        holes = pad.point(lambda v: 255 if v == 0 else 0).crop((1, 1, w + 1, h + 1))
        return ImageChops.lighter(solid, holes)

    def _load_parts(self):
        s = self.s

        def load_pil(name):
            im = Image.open(os.path.join(self.parts_dir,
                                         f"{name}.png")).convert("RGBA")
            if s != 1.0:
                im = im.resize((max(1, round(im.width * s)),
                                max(1, round(im.height * s))), Image.LANCZOS)
            return self._hard(im)

        self.im = {}
        self.has = {}
        pil_cache = {}
        for name in ("body_open", "pupils", "body_mask", "lashes", "hair",
                     "eyes_closed", "head", "desk", "arm_pen",
                     "smile", "pet1", "pet2", "scarf"):
            # 파일과 layout 위치가 둘 다 있어야 사용 (자동업데이트 섞임 대비)
            self.has[name] = (os.path.exists(os.path.join(self.parts_dir,
                                                          f"{name}.png"))
                              and name in self.layout)
            if self.has[name]:
                pil_cache[name] = load_pil(name)
                self.im[name] = ImageTk.PhotoImage(pil_cache[name])

        # 타이머 카드 가로 중심 = 책상 내용의 중심 (캔버스 중심이 아니라)
        self.card_cx = self.W / 2
        self._desk_top = self.H * 0.6        # 반려동물이 올라오는 기준선
        if "desk" in pil_cache:
            bb = pil_cache["desk"].split()[3].getbbox()
            if bb:
                self.card_cx = (bb[0] + bb[2]) / 2
                self._desk_top = bb[1]

        self._build_pet_mask(pil_cache)
        self._load_hat(pil_cache)

        # 잘 때 머리를 기울이는 축 = 목 (머리 가로 중심 · 몸통 윗선)
        self._tilt_cache = {}
        self._tilt_max = 0.0
        base = "head" if self.has.get("head") else "body_open"
        base_im = pil_cache.get(base)
        hb = base_im.split()[3].getbbox() if base_im is not None else None
        hx, hy = self.layout.get(base, {}).get("pos", (0, 0))
        # 머리(없으면 몸통) 실루엣 상자 — zzZ 위치·기울임 축의 기준
        self._head_box = ((hx * s + hb[0], hy * s + hb[1],
                           hx * s + hb[2], hy * s + hb[3]) if hb else
                          (0, 0, self.W, self.H))
        if self.has.get("head"):
            self._neck = ((self._head_box[0] + self._head_box[2]) / 2,
                          self.layout["body_open"]["pos"][1] * s + 6)

        # 회전 손 파츠: 어깨(최상단) 앵커 기준으로 회전 — 어깨가 몸에서 안 떨어짐
        self.hop = {}
        for name in ("arm_key", "arm_right_typing"):
            try:
                im = load_pil(name)
            except Exception:
                continue
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
        try:
            self.arm_pil = load_pil("arm_right")
        except Exception:
            self.arm_pil = None
        self._arm_cache = {}
        # 왼손 위치 미세 보정 (캔버스 px, config의 arm_key_offset)
        ko = self.cfg.get("arm_key_offset", [0, 0])
        self.arm_key_off = (ko[0] * s, ko[1] * s)
        self._pil_cache = {n: pil_cache[n] for n in pil_cache}
        self._load_pil = load_pil

        if self.has.get("head"):
            self._build_tilt_base()     # 잘 때 기울이는 머리 한 장 + 최대 각도

        self._bake_oy()                 # oy 의존 좌표 계산
        self._build_shadow_img()        # 그림자 이미지 생성

    def _load_hat(self, pil_cache):
        """축하용 고깔모자 — 머리 폭에 맞춰 줄이고 살짝 기울여 둔다."""
        path = os.path.join(self.parts_dir, "hat.png")
        if not os.path.exists(path):        # 스킨 폴더에 없으면 기본에서
            path = os.path.join(self.dir, "hat.png")
        self.hat_anchor = (0, 0)
        if not os.path.exists(path):
            self.has["hat"] = False
            return
        base = "head" if self.has.get("head") else "body_open"
        if base not in pil_cache:
            self.has["hat"] = False
            return
        bb = pil_cache[base].split()[3].getbbox()
        head_w = (bb[2] - bb[0]) if bb else self.W
        im = Image.open(path).convert("RGBA")
        k = head_w * float(self.cfg.get("hat_scale", 0.24)) / max(im.width, 1)
        im = im.resize((max(8, round(im.width * k)), max(8, round(im.height * k))),
                       Image.LANCZOS)
        im = im.rotate(14, expand=True, resample=self._resample())
        self.im["hat"] = ImageTk.PhotoImage(self._hard(im))
        self.has["hat"] = True

    TILT_PAD = 70                    # 회전 여유 (잘려나가지 않게 캔버스를 넓혀 합성)

    def _build_tilt_base(self):
        """머리+얼굴 파츠를 한 장으로 합쳐 두고, 창을 안 벗어나는 최대 각도를 구한다."""
        p = self.TILT_PAD
        layer = Image.new("RGBA", (self.W + 2 * p, self.H + 2 * p), (0, 0, 0, 0))

        def paste(name):
            x, y = self.layout[name]["pos"]
            layer.alpha_composite(self._pil_cache[name],
                                  (round(x * self.s) + p, round(y * self.s) + p))

        paste("head")
        for name in (self.layout.get("overlays") or ["eyes_closed", "hair"]):
            if name in ("body_mask", "head") or not self.has.get(name):
                continue
            paste(name)
        self._tilt_base = layer
        self._tilt_max = 0.0
        # 실제로 돌려 보고, 창 밖으로 8px 이내로만 밀리는 최대 각도를 고른다
        for deg in (8, 7, 6, 5, 4, 3, 2):
            if abs(self._tilt_fit(self._rot_head(-deg))) <= 8:
                self._tilt_max = float(deg)
                break

    def _rot_head(self, deg):
        p = self.TILT_PAD
        return self._tilt_base.rotate(deg, center=(self._neck[0] + p,
                                                   self._neck[1] + p),
                                      resample=self._resample())

    def _tilt_fit(self, im):
        """돌린 머리가 창 안에 들어오도록 좌우로 밀어야 할 픽셀 수."""
        p = self.TILT_PAD
        bb = im.split()[3].getbbox()
        if not bb:
            return 0
        return max((p + 2) - bb[0], 0) - max(bb[2] - (p + self.W - 2), 0)

    def _sleep_head(self, deg):
        """잘 때 기울어진 머리 — (이미지, 창 안으로 미는 보정값), 1도 단위 캐시."""
        key = round(deg)
        hit = self._tilt_cache.get(key)
        if hit is not None:
            return hit
        if len(self._tilt_cache) > 24:
            self._tilt_cache.clear()
        layer = self._rot_head(key)
        dx = max(-12, min(12, self._tilt_fit(layer)))
        hit = (ImageTk.PhotoImage(self._hard(layer)), dx)
        self._tilt_cache[key] = hit
        return hit

    def _tilt_xy(self, x, y, deg):
        """목을 축으로 deg만큼 돈 뒤의 좌표 (콧방울 따라가기용)."""
        a = math.radians(deg)
        nx, ny = self._neck
        dx, dy = x - nx, y - ny
        return (nx + dx * math.cos(a) - dy * math.sin(a),
                ny + dx * math.sin(a) + dy * math.cos(a))

    def _draw_snot(self, now, yo, deg, tdx=0):
        """자는 동안 코에서 부풀었다 꺼지는 콧방울."""
        nose = self.cfg.get("nose")
        if not nose:
            return
        t = now % 5.2
        if t < 3.8:
            r = 2.0 + 11.0 * (t / 3.8) ** 1.6
        elif t < 4.05:
            r = 13.0 * (1 - (t - 3.8) / 0.25)      # 픽 하고 꺼짐
        else:
            return
        if r < 1.5:
            return
        x, y = nose[0] * self.s, nose[1] * self.s
        x, y = self._tilt_xy(x, y, -deg)           # 캔버스 좌표는 회전 방향 반대
        x += tdx
        y += self.oy + yo
        c = self.canvas
        cx, cy = x + r * 0.15, y + r * 0.85
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill="#dfeeff", outline="#8dbfe4", width=2)
        c.create_oval(cx - r * 0.55, cy - r * 0.6, cx - r * 0.05, cy - r * 0.1,
                      fill="#ffffff", outline="")

    def _build_pet_mask(self, pil_cache):
        """반려동물이 '책상 뒤'에 있도록, 열마다 책상 윗선 위쪽만 남기는 마스크."""
        self._pet_cache = {}
        self._pet_hide = {}
        self.pet_cast = []
        if not (self.has.get("pet1") or self.has.get("pet2")):
            self._pet_mask = None
            return
        if "desk" not in pil_cache:
            self._pet_mask = None
            return
        desk = pil_cache["desk"]
        W, H = desk.size
        alpha = desk.split()[3].point(lambda v: 255 if v > 40 else 0)
        tops = []
        for x in range(W):                       # 열별 책상 최상단 행
            bb = alpha.crop((x, 0, x + 1, H)).getbbox()
            tops.append(bb[1] if bb else None)
        last = H
        for x in range(W):                       # 책상이 없는 열은 이웃 값으로
            if tops[x] is None:
                tops[x] = last
            else:
                last = tops[x]
        last = H
        for x in range(W - 1, -1, -1):
            if tops[x] == H:
                tops[x] = last
            else:
                last = tops[x]
        mask = Image.new("L", (W, H), 0)
        col = Image.new("L", (1, H), 255)
        for x, t in enumerate(tops):
            if t > 0:
                mask.paste(col.crop((0, 0, 1, t)), (x, 0))
        self._pet_mask = mask
        self._pet_xy = {}
        k = float(self.cfg.get("pet_scale", 1.3))
        for name in ("pet1", "pet2"):
            if not self.has.get(name):
                continue
            small = pil_cache[name]
            rot = float(self.cfg.get("pet_rot", 0))
            if k != 1.0 or rot:                  # 원본에서 다시 줄여야 안 뭉갠다
                src = Image.open(os.path.join(self.parts_dir,
                                              f"{name}.png")).convert("RGBA")
                big = src.resize((max(1, round(src.width * self.s * k)),
                                  max(1, round(src.height * self.s * k))),
                                 Image.LANCZOS)
                if rot:                          # 캐릭터 뒤에서 안 가리게 기울이기
                    big = big.rotate(rot, expand=True, resample=self._resample())
                bb = big.split()[3].getbbox()    # 회전으로 생긴 빈 여백은 잘라낸다
                if bb:
                    big = big.crop(bb)
                pil_cache[name] = big = self._hard(big)
            else:
                big = small
            px, py = self.layout[name]["pos"]
            px, py = px * self.s, py * self.s
            # 원래 실루엣의 밑변 중심을 기준으로 커지고 기울어지게
            sb = small.split()[3].getbbox() or (0, 0, small.width, small.height)
            ax, ay = px + (sb[0] + sb[2]) / 2, py + sb[3]
            px = round(ax - big.width / 2) + int(self.cfg.get("pet_dx", 0))
            py = round(ay - big.height)
            px = max(2, min(px, self.W - big.width - 2))   # 창 밖으로 안 나가게
            self._pet_xy[name] = (px, py)
            # 모든 열에서 책상 윗선 아래로 내려가면 완전히 사라진다
            need = max(tops[min(max(px + j, 0), W - 1)]
                       for j in range(big.width)) - py
            self._pet_hide[name] = max(need + 4, 10)


    def _timer_oy(self):
        """타이머 카드가 차지하는 캐릭터 위 여백."""
        if not self.timer_on:
            return 0
        if self.has_clock:
            return OY_CLOCK_OPEN if self.clock_open else OY_CLOCK_COMPACT
        extra = int(self.cfg.get("card_top", 22)) - 22        # 장식 여유 (토끼 귀)
        return TIMER_H + (26 if self.cfg.get("fun") else 0) + extra

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
        """캐릭터+카드 실루엣을 흐려 만든 반투명 그림자 이미지.

        가장자리 파츠(귀 등)의 그림자가 잘리지 않도록 여백(P)을 두고 그린다.
        """
        self.shadow_img = self.shadow_img_type = None
        if not self.us.get("shadow", True):
            return
        for typing in (False, True):
            self._compose_shadow(typing)
        self._shadow_base = self.shadow_img
        self._shadow_typing = False

    def _compose_shadow(self, typing):
        """그림자 실루엣 한 벌. typing이면 펜 손 대신 타자 팔로 그린다."""
        from PIL import ImageDraw, ImageFilter
        P = SHADOW_PAD
        comp = Image.new("RGBA", (self.W + 2 * P, self.H + 2 * P), (0, 0, 0, 0))
        parts = ["body_open", "scarf", "lashes", "hair", "head", "desk"]
        if not typing:
            parts.append("arm_pen")
        for name in parts:
            if name in self._pil_cache:
                x, y = self._pos(name)
                comp.alpha_composite(self._pil_cache[name], (round(x) + P, round(y) + P))
        arms = ["arm_key", "arm_right_typing" if typing else "arm_right"]
        for name in arms:
            try:
                im = self._load_pil(name)
            except Exception:
                continue
            x, y = self._pos(name)
            if name == "arm_key":
                x += self.arm_key_off[0]
                y += self.arm_key_off[1]
            comp.alpha_composite(im, (round(x) + P, round(y) + P))
        if self.timer_on:
            d = ImageDraw.Draw(comp)
            cg = self._card_geom()
            cx0, cy0 = cg["x0"] + P, cg["y0"] + P
            cx1, cy1 = cg["x1"] + P, cg["y1"] + P
            for ex in (cx0 + 26, cx1 - 26):        # 귀 실루엣
                d.ellipse([ex - 12, cy0 - 17, ex + 12, cy0 + 7], fill=(0, 0, 0, 255))
            d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=16, fill=(0, 0, 0, 255))
        a = comp.getchannel("A").filter(ImageFilter.GaussianBlur(7))
        a = a.point(lambda v: int(v * 0.30))
        black = Image.new("RGB", comp.size, (0, 0, 0))
        img = Image.merge("RGBA", (*black.split(), a))
        if typing:
            self.shadow_img_type = img
        else:
            self.shadow_img = img

    def _card_geom(self):
        """현재 타이머 카드의 위치·크기. 시계 펼침이면 세로 직사각형."""
        if self.has_clock and self.clock_open:
            w, h = 148, 150           # 세로가 살짝 더 긴 직사각형
        elif self.has_clock:
            w, h = 196, 40
        else:
            w, h = 200, (88 if self.cfg.get("fun") else 62)
        x0 = getattr(self, "card_cx", self.W / 2) - w / 2
        y0 = float(self.cfg.get("card_top", 22))
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
        """타자 소리 팩 목록. 캐릭터 sounds/ + 공용 '타이핑 음원/' 폴더를 함께 스캔.

        사용자가 ena-mascot/타이핑 음원/ 에 (압축 푼) Mechvibes 팩 폴더를 넣으면
        자동으로 목록에 추가된다. pack 이름 → 폴더 경로를 self._pack_paths에 저장.
        """
        self._pack_paths = {}
        for base in (os.path.join(self.dir, "sounds"),
                     os.path.join(HERE, "타이핑 음원")):
            if not os.path.isdir(base):
                continue
            for d in os.listdir(base):
                p = os.path.join(base, d)
                if d != "pen" and os.path.exists(os.path.join(p, "config.json")):
                    self._pack_paths.setdefault(d, p)   # 먼저 찾은 것 우선
        return sorted(self._pack_paths)

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
        pack_dir = getattr(self, "_pack_paths", {}).get(
            name, os.path.join(self.dir, "sounds", name))
        try:
            self.sndpack = SoundPack(
                pack_dir, volume=float(self.us.get("sound_volume", 60)))
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
        now = time.time()
        k = str(key)
        first = k not in self._held           # 꾹 누름(자동 반복)은 최초만
        self._held.add(k)
        # 투어박스 등 다이얼: 같은 키를 사람 타이핑보다 빠르게(90ms 이내) 연타 →
        # 소리 억제 (브러시 크기·화면 회전 돌릴 때 키보드 소리 안 나게)
        dial = (now - self._key_times.get(k, 0)) < 0.09
        self._key_times[k] = now
        if first and not dial:
            self.stat["keys"] = self.stat.get("keys", 0) + 1
        sp = self.sndpack
        if first and not dial and sp is not None:
            try:
                sp.play(key)
            except Exception:
                pass

    def _poll_mac_input(self):
        """맥: 리스너 콜백 대신 카운터 변화를 읽어 같은 상태를 만든다."""
        mi = self._macin
        if mi is None:
            return
        dk, dm, pressed = mi.read()
        now = time.time()
        if dk:
            self.key_events += dk
            self.stat["keys"] = self.stat.get("keys", 0) + dk
            sp = self.sndpack
            if sp is not None:
                try:
                    sp.play(self.key_events)      # 한 프레임에 한 번만
                except Exception:
                    pass
        if dm:
            self.last_pointer = now
            if self.mouse_pressed:
                self.last_drag = now
        if pressed != self.mouse_pressed:
            self.mouse_pressed = pressed
            self.last_pointer = now
            if not pressed:
                self._new_stroke = True

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
        if self._press is not None and not self._dragged:
            px, py, _, _ = self._press
            g = self._card_geom()
            on_card = (g["x0"] <= px <= g["x1"] and g["y0"] - 17 <= py <= g["y1"])
            btn = getattr(self, "_end_btn", None)
            if self.fun and btn and btn[0] <= px <= btn[2] and btn[1] <= py <= btn[3]:
                self._celebrate()                      # 작업 종료 버튼
            elif self.has_clock and on_card:
                self._toggle_clock()
            elif self.fun and not on_card and py > self.oy:
                self._on_poke()                        # 캐릭터를 콕 찌름
        self._press = None

    def _on_poke(self):
        """캐릭터 클릭 반응 — 콩 튀고 한마디. (반응 파츠는 나중에 교체 가능)"""
        now = time.time()
        self.click_bounce = now + 0.45
        self.squash_until = now + 0.12
        self._say(random.choice(self._click_pool()), 2.2)

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
            if self._kb is not None:
                self._kb.stop()
            if self._ms is not None:
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
            saved = st.get("stat")
            if isinstance(saved, dict):
                self.stat.update({k: saved.get(k, v) for k, v in self.stat.items()})
            r = st.get("rec")
            if isinstance(r, dict):      # 세션이 이어지면 축하 기록도 이어받는다
                self.rec["strokes"] = [int(v) for v in r.get("strokes", [])
                                       if isinstance(v, (int, float))]
                self.rec["focus"] = float(r.get("focus", 0) or 0)
        except Exception:
            pass

    def _timer_save(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as fp:
                json.dump({"seconds": round(self.work_secs),
                           "stat": self.stat, "rec": self.rec}, fp)
        except Exception:
            pass

    def _reset_records(self):
        """새 세션 — 기록 갱신 축하를 처음부터 다시 센다."""
        self.rec = {"strokes": [], "focus": 0.0}
        self._rec_prev_run = 0.0
        self._rec_armed = True

    def _timer_reset(self):
        self.work_secs = 0.0
        self._reset_records()
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
        # 하루 브리핑용 집계 (작업/딴짓/휴식 시간, 최장 집중 구간, 시작·마지막)
        s = self.stat
        s[state] = s.get(state, 0.0) + dt
        if state == "work":
            s["_run"] = s.get("_run", 0.0) + dt
            s["best"] = max(s.get("best", 0.0), s["_run"])
            if not s.get("first"):
                s["first"] = now
            s["last"] = now
        else:
            s["_run"] = 0.0
        if now - self._t_save > 30:
            self._t_save = now
            self._timer_save()
        return state

    def _text_w(self, text):
        """상태 텍스트 폭(px) — 캔버스로 측정·캐시 (tkinter.font 의존 제거)."""
        w = self._tw_cache.get(text)
        if w is None:
            t = self.canvas.create_text(-2000, -2000, text=text, anchor="nw",
                                        font=("Malgun Gothic", 8))
            bb = self.canvas.bbox(t)
            w = (bb[2] - bb[0]) if bb else len(text) * 11
            self.canvas.delete(t)
            self._tw_cache[text] = w
        return w

    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    @staticmethod
    def _ear_pts(cx, cy, droop, k=1.0):
        """카드 위 토끼 귀 윤곽 (베지에 척추 + 폭). droop=1이면 옆으로 접힌다."""
        p0 = (cx, cy)
        if droop:
            p1, p2 = (cx + 1, cy - 28), (cx + 24, cy - 16)
        else:
            p1, p2 = (cx - 4, cy - 26), (cx + 1, cy - 33)
        left, right, cap, N = [], [], [], 8
        for i in range(N + 1):
            t = i / N
            u = 1 - t
            x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
            y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
            dx = 2 * u * (p1[0] - p0[0]) + 2 * t * (p2[0] - p1[0])
            dy = 2 * u * (p1[1] - p0[1]) + 2 * t * (p2[1] - p1[1])
            n = math.hypot(dx, dy) or 1.0
            tx, ty = dx / n, dy / n
            nx, ny = -ty, tx
            w = (1.8 + 4.6 * math.sin(math.pi * (0.18 + 0.74 * t))) * k
            left.append((x + nx * w, y + ny * w))
            right.append((x - nx * w, y - ny * w))
            if i == N:                       # 귀 끝은 반원으로 둥글게
                for j in range(1, 5):
                    th = math.pi * j / 5
                    ct, st = math.cos(th), math.sin(th)
                    cap.append((x + (ct * nx + st * tx) * w,
                                y + (ct * ny + st * ty) * w))
        return [v for p in left + cap + right[::-1] for v in p]

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
        elif deco == "dog":
            # 접힌 검은 강아지 귀 — 카드 위 모서리에서 바깥으로 늘어짐
            for sign, ex in ((-1, x0 + 18), (1, x1 - 18)):
                c.create_oval(ex - 15, y0 - 15, ex + 9, y0 + 28,
                              fill="#2b2b2b", outline="")
                c.create_oval(ex - 9, y0 - 7, ex + 3, y0 + 14,
                              fill="#4a4a4a", outline="")
        elif deco == "rabbit":
            base = self.card.get("bg", "#ffffff")
            inner = self.card.get("track", "#c9d3e6")
            for sign, ex in ((-1, x0 + 26), (1, x1 - 34)):
                droop = 1 if sign > 0 else 0        # 오른쪽 귀는 접혀 내려감
                c.create_polygon(self._ear_pts(ex, y0 + 10, droop),
                                 smooth=True, fill=base,
                                 outline=self.card["border"], width=2)
                c.create_polygon(self._ear_pts(ex, y0 + 8, droop, 0.42),
                                 smooth=True, fill=inner, outline="")
        elif deco == "scarf":
            navy, silver = "#2f3f66", "#dfe5f0"
            self._rrect(x0 + 14, y0 - 15, x1 - 14, y0 + 7, 9, fill=navy, outline="")
            span = (x1 - x0 - 76) / 3
            for i in range(4):
                sx = x0 + 44 + i * span
                c.create_line(sx, y0 - 13, sx - 7, y0 + 5, fill=silver, width=3)
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
        """아날로그 시계 + 작업한 시간을 방사형 선으로(기존 '작업 흔적' 방식).

        작업한 분마다 중심→가장자리 방향으로 선 하나(오전=연한색/오후=진한색).
        연속 작업이면 부채꼴처럼 촘촘히 채워지고, 안 한 시간대는 비어 있다.
        """
        c = self.canvas
        cd = self.card
        am_col = cd.get("arc_am", "#f4c9dd")     # 오전 = 연한 분홍
        pm_col = cd.get("arc_pm", "#e493bd")     # 오후 = 진한 분홍
        # 바탕
        c.create_oval(cx - R, cy - R, cx + R, cy + R,
                      fill=cd["bg"], outline=cd["border"], width=2)
        # 작업한 분 = 방사형 선 (12시간 다이얼 위치)
        Rf = R - 3
        seen = set()
        for m in ((self._ws_data or {}).get("act") or []):
            lt = time.localtime(m * 60)
            pos = (lt.tm_hour % 12) * 60 + lt.tm_min
            key = (pos, lt.tm_hour < 12)
            if key in seen:
                continue
            seen.add(key)
            a = math.radians(pos / 720 * 360 - 90)
            col = am_col if lt.tm_hour < 12 else pm_col
            c.create_line(cx, cy, cx + Rf * math.cos(a), cy + Rf * math.sin(a),
                          fill=col, width=1)
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

    # ── 귀여운 이벤트: 말풍선 · 혼잣말 · 클릭 반응 · 반려동물 · 축하 ──────
    PET_RISE, PET_HOLD, PET_FALL = 0.5, 6.0, 0.5
    TALK = [
        "히히", "바보!", "배고파요", "조금만 더 힘내자!", "뭐 좀 먹고 할까...",
        "야옹", "싫어 그 가느다란 꼬리", "사탄 참 좋다", "가즈아", "야르",
        "졸려", "심심해", "오늘도 화이팅!", "집중! 집중!", "손이 멈췄다?",
        "그림 그리자!", "저장했지?", "Ctrl+S!", "커피 한 잔?", "조금만 더!",
        "쉬엄쉬엄 하자.", "손목 괜찮아?", "한 장만 더!", "끝내고 놀자!",
        "영혼을 바쳐라.", "몰?루", "오늘도 평화롭다.", "좋은 하루!",
        "기분 최고!", "운세 좋음!", "행운 냥!", "행복 충전!", "산책은 싫어.",
        "창밖이 궁금해.", "햇빛이다!", "꾸벅...", "후암~", "멍...", "어라?",
        "오?", "흠...", "비밀이야.", "쉿!", "냥냥펀치!", "히힛!",
        "간식은 언제?",
    ]
    CLICK_TALK = TALK

    def _say(self, text, secs=4.0):
        self.bubble = (text, time.time() + secs)

    def _talk_pool(self, state):
        return self.cfg.get("talk") or self.TALK

    def _click_pool(self):
        return self.cfg.get("click_talk") or self._talk_pool(None)

    def _fun_tick(self, now, state, sleeping):
        """혼잣말·반려동물 스케줄과 폭죽 물리 (매 프레임)."""
        if not self.fun:
            return
        if self.bubble and now > self.bubble[1]:
            self.bubble = None
        if self._update_msg and self.bubble is None and not sleeping:
            self._say(self._update_msg, 12.0)     # 업데이트 알림 (시작 후 한 번)
            self._update_msg = None
            self.next_talk = now + 120
        self._rec_tick(now, state)
        if (self.bubble is None and now >= self.next_talk
                and not sleeping and now > self.celebrate_until):
            self._say(random.choice(self._talk_pool(state)))
            self.next_talk = now + random.uniform(150, 420)
        # 반려동물 등장/퇴장
        total = self.PET_RISE + self.PET_HOLD + self.PET_FALL
        if self.pet_t0 == 0.0 and now >= self.next_pet and not sleeping:
            self.pet_cast = self._pick_pets()
            self.pet_t0 = now if self.pet_cast else 0.0
            if not self.pet_cast:
                self.next_pet = now + 999999
        elif self.pet_t0 and now - self.pet_t0 > total + 0.4:
            self.pet_t0 = 0.0
            self.next_pet = now + random.uniform(240, 600)
        # 폭죽 입자 (중력 + 수명)
        if self.particles:
            alive = []
            for p in self.particles:
                p[0] += p[2]
                p[1] += p[3]
                p[3] += 0.35
                p[5] -= 1
                if p[5] > 0 and p[1] < self.H + 30:
                    alive.append(p)
            self.particles = alive

    STROKE_MARKS = (300, 1000, 3000, 10000)   # 그린 획수 축하 지점
    FOCUS_MIN = 20 * 60                       # 최장 집중은 20분부터 인정
    FOCUS_STEP = 60                           # 최소 이만큼은 넘겨야 '갱신'

    def _rec_tick(self, now, state):
        """기록 갱신 축하 — 그린 획수 돌파 · 이번 세션 최장 집중 갱신."""
        if not self.fun or self.bubble is not None or now < self.celebrate_until:
            return
        if now < self._rec_next or state != "work":
            return                            # 작업 중일 때만, 그리고 쿨다운 뒤
        run = float(self.stat.get("_run", 0.0))
        if run < self._rec_prev_run:          # 집중이 끊겼다 → 다음 구간 준비
            self._rec_armed = True
        self._rec_prev_run = run

        strokes = int(self.stat.get("strokes", 0))
        for mark in self.STROKE_MARKS:
            if strokes >= mark and mark not in self.rec["strokes"]:
                self.rec["strokes"].append(mark)
                self._cheer(f"{mark:,}획 돌파!")
                return
        if (self._rec_armed and run >= self.FOCUS_MIN
                and run > self.rec["focus"] + self.FOCUS_STEP):
            self.rec["focus"] = run
            self._rec_armed = False           # 이 구간에서는 한 번만
            self._cheer(f"최장 집중 갱신! {int(run // 60)}분째")

    def _cheer(self, text):
        """작업 종료보다 약한 축하 — 말풍선 + 폭죽 조금 (팝업 없음)."""
        now = time.time()
        self._rec_next = now + 90             # 연달아 뜨지 않게
        self._say(text, 4.5)
        if self.has.get("smile"):
            self.smile_until = now + 3.0
        cols = ["#ff9ec4", "#ffd479", "#9ad7ff", "#b8e986", "#c9a7ff"]
        for _ in range(14):
            ang = random.uniform(-2.6, -0.55)
            spd = random.uniform(3.0, 6.5)
            self.particles.append([self.card_cx + random.uniform(-45, 45),
                                   self.oy + 46,
                                   math.cos(ang) * spd, math.sin(ang) * spd,
                                   random.choice(cols), random.randint(35, 60)])
        self._timer_save()

    def _pick_pets(self):
        """이번에 나올 반려동물 배역 — 2마리면 한 마리씩 또는 둘 다."""
        names = [n for n in ("pet1", "pet2") if self.has.get(n)]
        if len(names) < 2:
            return [(n, 0.0) for n in names]
        if self.cfg.get("pet_variants"):      # 같은 동물의 다른 포즈 — 하나만
            return [(random.choice(names), 0.0)]
        pick = random.choice([[names[0]], [names[1]], names])
        return [(n, i * 0.35) for i, n in enumerate(pick)]

    def _pet_img(self, name, dy):
        """책상 윗선 아래는 잘라낸 반려동물 이미지 (내려간 만큼 가려짐)."""
        key = (name, int(dy))
        hit = self._pet_cache.get(key)
        if hit is None:
            if len(self._pet_cache) > 120:
                self._pet_cache.clear()
            pil = self._pil_cache[name]
            x0, y0 = self._pet_xy[name]
            y0 = y0 + dy
            region = self._pet_mask.crop((x0, y0, x0 + pil.width, y0 + pil.height))
            blank = Image.new("RGBA", pil.size, (0, 0, 0, 0))
            hit = ImageTk.PhotoImage(Image.composite(pil, blank, region))
            self._pet_cache[key] = hit
        return hit

    PET_BLUR = 16                    # 반려동물 그림자 블러 여백

    def _pet_shadow_pil(self, name, dy):
        """반려동물 그림자(책상선까지 잘린 실루엣을 흐린 것) — dy별 캐시."""
        key = (name, int(dy))
        hit = self._pet_sh_cache.get(key)
        if hit is None:
            from PIL import ImageFilter
            if len(self._pet_sh_cache) > 40:
                self._pet_sh_cache.clear()
            pil = self._pil_cache[name]
            x0, y0 = self._pet_xy[name]
            region = self._pet_mask.crop((x0, y0 + dy, x0 + pil.width,
                                          y0 + dy + pil.height))
            blank = Image.new("RGBA", pil.size, (0, 0, 0, 0))
            cut = Image.composite(pil, blank, region)
            b = self.PET_BLUR
            pad = Image.new("L", (pil.width + 2 * b, pil.height + 2 * b), 0)
            pad.paste(cut.getchannel("A"), (b, b))
            a = pad.filter(ImageFilter.GaussianBlur(7)).point(lambda v: int(v * 0.30))
            black = Image.new("RGB", pad.size, (0, 0, 0))
            hit = Image.merge("RGBA", (*black.split(), a))
            self._pet_sh_cache[key] = hit
        return hit

    def _update_pet_shadow(self):
        """반려동물이 나와 있는 동안만 그림자 창을 갱신 (약 15fps로 제한)."""
        if self.shadow is None or self.shadow_img is None:
            return
        drawn = self._pet_drawn
        now = time.time()
        if not drawn:
            if self._pet_sh_on:                  # 원래 그림자로 되돌린다
                self.shadow.set_image(self._shadow_base or self.shadow_img)
                self._pet_sh_on = False
            return
        if now - self._pet_sh_t < 0.065:
            return
        self._pet_sh_t = now
        comp = (self._shadow_base or self.shadow_img).copy()
        b, sp = self.PET_BLUR, SHADOW_PAD
        for name, dy, x, y in drawn:
            comp.alpha_composite(self._pet_shadow_pil(name, dy),
                                 (round(x) + sp - b, round(y) + sp - b))
        self.shadow.set_image(comp)
        self._pet_sh_on = True

    def _draw_pet(self, now):
        """책상 뒤에서 뿅 — 올라와 빤히 보다가 쏙 들어간다."""
        if not (self.fun and self.pet_t0):
            return
        c = self.canvas
        for name, delay in self.pet_cast:
            t = now - self.pet_t0 - delay
            if t < 0:
                continue
            if t < self.PET_RISE:
                f = t / self.PET_RISE
            elif t < self.PET_RISE + self.PET_HOLD:
                f = 1.0
            else:
                f = max(0.0, 1.0 - (t - self.PET_RISE - self.PET_HOLD) / self.PET_FALL)
            if f <= 0:
                continue
            f = f * f * (3 - 2 * f)                     # 부드럽게
            x, y = self._pet_xy[name]
            y += self.oy
            if f >= 1.0:                                # 빤히 보는 동안 살짝 들썩
                bob = math.sin((now + delay * 3) * 2.4) * 2.0
                c.create_image(x, y - bob, image=self._pet_img(name, 0),
                               anchor="nw")
                self._pet_drawn.append((name, 0, x, y - bob))
            else:
                dy = round(self._pet_hide.get(name, 0) * (1 - f) / 3) * 3
                c.create_image(x, y + dy, image=self._pet_img(name, dy),
                               anchor="nw")
                self._pet_drawn.append((name, dy, x, y + dy))


    def _draw_hat(self, yo):
        """축하용 고깔모자 (hat.png 있으면 사용, 없으면 임시 도형)."""
        if not (self.fun and time.time() < self.hat_until):
            return
        c = self.canvas
        name = "head" if self.has.get("head") else "body_open"
        if name not in self._pil_cache:
            return
        top = self._pos(name)[1] + yo
        bb = self._pil_cache[name].split()[3].getbbox()
        if bb:                          # 이미지 여백 제외한 실제 머리 꼭대기
            top += bb[1]
        dx, dy = self.cfg.get("hat_pos", [-44, 44])
        hx = self.card_cx + dx          # 살짝 비껴 씌워 말풍선을 안 가리게
        hat = self.im.get("hat")
        if hat is not None:
            c.create_image(hx, top + dy, image=hat, anchor="s")
            return
        c.create_polygon(hx - 19, top + 30, hx, top - 6, hx + 19, top + 30,
                         fill="#ffb3c9", outline="#e07a9c", width=2)
        c.create_oval(hx - 6, top - 16, hx + 6, top - 4,
                      fill="#fff0a8", outline="#e0b84a", width=2)

    def _draw_particles(self):
        c = self.canvas
        for x, y, _vx, _vy, col, _life in self.particles:
            c.create_rectangle(x - 3, y - 2, x + 3, y + 2, fill=col, outline="")

    @staticmethod
    def _bubble_pts(x0, y0, x1, y1, r, tx, tw, th):
        """둥근 사각형 + 아래쪽 V자 꼬리를 한 붓으로 이은 점 목록."""
        pts = []

        def arc(cx, cy, a0, a1, steps=6):
            for i in range(steps + 1):
                a = math.radians(a0 + (a1 - a0) * i / steps)
                pts.extend((cx + math.cos(a) * r, cy + math.sin(a) * r))

        arc(x1 - r, y0 + r, -90, 0)                 # 우상
        arc(x1 - r, y1 - r, 0, 90)                  # 우하
        pts.extend((tx + tw / 2, y1))               # 꼬리 시작
        pts.extend((tx - tw * 0.18, y1 + th))       # 꼬리 끝
        pts.extend((tx - tw / 2, y1))
        arc(x0 + r, y1 - r, 90, 180)                # 좌하
        arc(x0 + r, y0 + r, 180, 270)               # 좌상
        return pts

    def _draw_bubble(self, yo):
        """머리 위 말풍선 — 둥근 모서리 + 아래 V자 꼬리."""
        if not (self.fun and self.bubble):
            return
        text = self.bubble[0]
        c, cd = self.canvas, self.card
        w = max(self._text_w(text) + 34, 74)
        h = 36
        cx = self.card_cx
        if time.time() < self.hat_until:      # 고깔모자를 가리지 않게 옆으로
            cx += 42
        cx = min(max(cx, w / 2 + 4), self.W - w / 2 - 4)   # 창 밖으로 안 나가게
        top = self._pos("head" if self.has.get("head") else "body_open")[1] + yo
        # 카드와 겹치지 않게 카드 아래로 (머리 위쪽에 걸침)
        card_bottom = self._card_geom()["y1"] if self.timer_on else self.oy
        by = max(top + 10, card_bottom + 40)
        x0, x1 = cx - w / 2, cx + w / 2
        pts = self._bubble_pts(x0, by - h, x1, by, 13, cx + 4, 17, 13)
        c.create_polygon([p + 2 for p in pts], fill="#e6e2e8", outline="")
        c.create_polygon(pts, fill="#ffffff", outline=cd["border"], width=2)
        c.create_text(cx, by - h / 2, text=text,
                      font=("Malgun Gothic", 9), fill=cd["text"])


    def _celebrate(self):
        """작업 종료 — 고깔모자 + 폭죽 + 축하 말풍선, 잠시 뒤 브리핑."""
        now = time.time()
        self.celebrate_until = now + 4.0
        self.hat_until = now + 14.0
        self.smile_until = now + 5.0            # 말풍선이 떠 있는 동안 웃는 얼굴
        self._reset_records()                   # 작업 종료 = 이번 '오늘'의 끝
        self._say("수고하셨습니다!", 5.0)
        cols = ["#ff9ec4", "#ffd479", "#9ad7ff", "#b8e986", "#c9a7ff", "#ffa9a9"]
        for _ in range(48):
            ang = random.uniform(-2.7, -0.45)
            spd = random.uniform(3.5, 8.5)
            self.particles.append([self.card_cx + random.uniform(-70, 70),
                                   self.oy + 46,
                                   math.cos(ang) * spd, math.sin(ang) * spd,
                                   random.choice(cols), random.randint(45, 85)])
        self.root.after(1500, self._open_briefing)

    def _open_briefing(self):
        """오늘의 작업 브리핑 팝업."""
        if getattr(self, "_brief_win", None) is not None \
                and self._brief_win.winfo_exists():
            self._brief_win.lift()
            return
        cd = self.card
        s = self.stat
        total = int(self.work_secs)
        goal = max(float(self.us.get("goal_hours", 6)), 0.5) * 3600
        pct = min(int(total / goal * 100), 999)

        def hm(sec):
            sec = int(sec)
            return f"{sec // 3600}시간 {sec % 3600 // 60}분" if sec >= 3600 \
                else f"{sec // 60}분"

        def clock(ts):
            return time.strftime("%H:%M", time.localtime(ts)) if ts else "-"

        rows = [("총 작업 시간", hm(total)),
                ("목표 달성", f"{pct}%  (목표 {self.us.get('goal_hours')}h)"),
                ("최장 집중", hm(s.get("best", 0))),
                ("시작 · 마지막", f"{clock(s.get('first'))} – {clock(s.get('last'))}"),
                ("딴짓 / 휴식", f"{hm(s.get('other', 0))} / {hm(s.get('idle', 0))}"),
                ("키 입력", f"{int(s.get('keys', 0)):,}회"),
                ("그린 획", f"{int(s.get('strokes', 0)):,}획")]

        W, PAD, ROW = 350, 22, 34
        HEAD_H = 78
        body_h = ROW * len(rows) + 20
        H = 22 + HEAD_H + 22 + body_h + 26 + 42 + 24
        win = tk.Toplevel(self.root)
        self._brief_win = win
        win.title("오늘의 작업")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg=cd["panel"])
        cv = tk.Canvas(win, width=W, height=H, bg=cd["panel"],
                       highlightthickness=0)
        cv.pack()

        def rr(x0, y0, x1, y1, r, **kw):
            pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                   x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
            return cv.create_polygon(pts, smooth=True, **kw)

        y = 22
        rr(PAD, y, W - PAD, y + HEAD_H, 18, fill=cd["soft"],
           outline=cd["border"], width=2)
        cv.create_text(W / 2, y + 30, text="오늘도 수고하셨어요!",
                       font=("Malgun Gothic", 12, "bold"), fill=cd["text"])
        cv.create_text(W / 2, y + 54, text=hm(total) + " 작업했어요",
                       font=("Malgun Gothic", 9), fill=cd["sub"])
        y += HEAD_H + 22

        rr(PAD, y, W - PAD, y + body_h, 16, fill="#ffffff",
           outline=cd["line"], width=1)
        ry = y + 10 + ROW / 2
        for i, (k, v) in enumerate(rows):
            if i:
                cv.create_line(PAD + 18, ry - ROW / 2, W - PAD - 18, ry - ROW / 2,
                               fill=cd["line"])
            cv.create_text(PAD + 18, ry, anchor="w", text=k,
                           font=("Malgun Gothic", 9), fill=cd["sub"])
            cv.create_text(W - PAD - 18, ry, anchor="e", text=v,
                           font=("Malgun Gothic", 9, "bold"), fill=cd["text"])
            ry += ROW
        y += body_h + 26

        def reset_and_close():
            self.work_secs = 0.0
            for k in ("work", "other", "idle", "best", "_run", "first", "last"):
                self.stat[k] = 0.0
            self.stat["keys"] = self.stat["strokes"] = 0
            self._reset_records()
            self._timer_save()
            win.destroy()

        gap = 12
        bw = (W - PAD * 2 - gap) / 2
        b1 = (PAD, y, PAD + bw, y + 42)
        b2 = (PAD + bw + gap, y, W - PAD, y + 42)
        rr(*b1, 16, fill="#f4f1f5", outline="")
        cv.create_text((b1[0] + b1[2]) / 2, y + 21, text="새로 시작",
                       font=("Malgun Gothic", 10, "bold"), fill=cd["sub"])
        rr(*b2, 16, fill=cd["fill"], outline="")
        cv.create_text((b2[0] + b2[2]) / 2, y + 21, text="닫기",
                       font=("Malgun Gothic", 10, "bold"), fill="#ffffff")

        def on_click(e):
            if b1[0] <= e.x <= b1[2] and b1[1] <= e.y <= b1[3]:
                reset_and_close()
            elif b2[0] <= e.x <= b2[2] and b2[1] <= e.y <= b2[3]:
                win.destroy()
        cv.bind("<Button-1>", on_click)
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = min(max(self.root.winfo_rootx() - 40, 10), max(sw - W - 10, 10))
        py = min(max(self.root.winfo_rooty() - 20, 10), max(sh - H - 60, 10))
        win.geometry(f"+{int(px)}+{int(py)}")


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
            tw = self._text_w(status)
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
            if self.fun:                      # 작업 종료 버튼
                bw = 104
                bx = (x0 + x1) / 2
                by = y1 - 22
                r = (bx - bw / 2, by - 11, bx + bw / 2, by + 11)
                self._rrect(*r, 11, fill=cd["fill"], outline="")
                c.create_text(bx, by, text="작업 종료",
                              font=("Malgun Gothic", 8, "bold"), fill="#ffffff")
                self._end_btn = r

    # ── 매 프레임 갱신 (~30fps) ──────────────────────────────────────────
    def _log_error(self, where):
        """한 프레임이 터져도 프로그램은 계속 돌게 — 원인은 파일로 남긴다."""
        self._err_count = getattr(self, "_err_count", 0) + 1
        if self._err_count > 20:
            return
        try:
            import traceback
            with open(os.path.join(self.state_dir, ".error.log"), "a",
                      encoding="utf-8") as fp:
                fp.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {where}\n")
                fp.write(f"char={self.char} frozen={getattr(sys, 'frozen', False)} "
                         f"scale={self.s:.3f} oy={self.oy} WH={self.W}x{self.H}\n")
                fp.write(f"timer_on={self.timer_on} fun={self.fun} "
                         f"pets={list(getattr(self, '_pet_hide', {}))} "
                         f"has={sorted(k for k, v in self.has.items() if v)}\n")
                fp.write(f"settings={self.us}\n")
                traceback.print_exc(file=fp)
        except Exception:
            pass

    def _put(self, name, x, y, anchor="nw"):
        """파츠 이미지 그리기. 파일이 없으면 조용히 건너뛴다(업데이트 끊김 대비)."""
        im = self.im.get(name)
        if im is None:
            return False
        self.canvas.create_image(x, y, image=im, anchor=anchor)
        return True

    def _safe(self, where, fn, *args):
        """부분 실패가 화면 전체를 지우지 못하게 — 3번 터지면 그 구역만 끈다."""
        if self._fail.get(where, 0) >= 3:
            return
        try:
            fn(*args)
        except Exception:
            self._fail[where] = self._fail.get(where, 0) + 1
            self._log_error(where)

    def tick(self):
        # 다음 프레임을 먼저 예약한다 — 중간에 예외가 나도 루프가 죽지 않게
        self.root.after(33, self.tick)
        try:
            self._tick_body()
        except Exception:
            self._log_error("tick")
            try:
                self.draw(time.time())      # 지워진 화면을 다시 채운다
            except Exception:
                self._log_error("redraw")

    def _tick_body(self):
        now = time.time()
        if self._macin is not None:
            self._safe("mac_input", self._poll_mac_input)
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
        if self.fun and now < self.click_bounce:      # 클릭 반응: 콩 하고 튐
            t = (self.click_bounce - now) / 0.45
            yo -= math.sin(t * math.pi) * 7

        cx, cy = cursor_pos()
        wx = self.root.winfo_rootx() + self.W // 2
        wy = self.root.winfo_rooty() + self.H // 2
        pdx = max(-5, min(5, (cx - wx) / 60))
        pdy = max(-3, min(4, (cy - wy) / 90))

        pen_typing = (now - self.last_pointer > 2.0) and (now - self.last_key < 1.8)
        if "pen" in f or f.get("type"):
            pen_typing = bool(f.get("type"))
        # 타자 칠 때는 깃펜이 사라지므로 그 자리의 그림자도 같이 없앤다
        if (self.shadow is not None and self.shadow_img_type is not None
                and pen_typing != self._shadow_typing):
            self._shadow_typing = pen_typing
            self._shadow_base = self.shadow_img_type if pen_typing else self.shadow_img
            if not self._pet_sh_on:
                self.shadow.set_image(self._shadow_base)

        blinking = (sleeping or now < self.blink_until or f.get("blink", False)) \
            and (self.blink_cfg is not None or self.has.get("eyes_closed"))
        smiling = bool(self.has.get("smile")
                       and (now < self.smile_until or f.get("smile", False)))
        if smiling:
            blinking = False

        self._pet_drawn = []
        try:
            state = self._timer_tick(now, idle) if self.timer_on else "idle"
        except Exception:
            state, _ = "idle", self._log_error("timer_tick")
        # 아래는 모두 구역 격리 — 하나가 터져도 캐릭터 본체는 그려진다
        self._safe("fun_tick", self._fun_tick, now, state, sleeping)
        if self.timer_on:
            self._safe("timer", self._draw_timer, state, sleeping, now)

        # ── 몸 (+머리 없는 캐릭터는 여기서 얼굴까지) ─────────────────────
        # 개는 머리를 팔 위에 그려야 어깨가 안 튀어나오므로, 얼굴을 팔 뒤로 미룬다.
        head_early = bool(self.cfg.get("arms_over_head") and self.has.get("head"))
        bx, by = self._pos("body_open")
        self._safe("body", self._put, "body_open", bx, by + yo)
        if not self.has.get("head"):
            self._safe("face", self._draw_face, yo, pdx, pdy, blinking, smiling)
        elif head_early:                # 준사: 책상·팔이 머리 위 (PSD 순서)
            self._safe("head", self._draw_head, now, yo, pdx, pdy,
                       blinking, smiling, sleeping)

        # 반려동물은 책상 바로 앞(=책상에 가려지게) 그린다
        if not self.cfg.get("pet_front"):
            self._safe("pet", self._draw_pet, now)

        # ── 책상 (+옵션: 화면 낙서) ──────────────────────────────────────
        dx_, dy_ = self._pos("desk")
        self._safe("desk", self._put, "desk", dx_, dy_)
        if self.us.get("trail"):
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

        # 앞으로 나오는 반려동물: 얼굴 위 · 팔 아래 (책상선 마스크는 그대로)
        if self.cfg.get("pet_front"):
            self._safe("pet", self._draw_pet, now)

        self._safe("arms", self._draw_arms, now, f, yo, pen_typing, cx, cy)

        if self.has.get("scarf"):       # 목도리 — 팔 위, 머리 아래
            sx, sy = self._pos("scarf")
            self._safe("scarf", self._put, "scarf", sx, sy + yo)

        # ── 머리(팔 위) + 얼굴 — 개처럼 머리를 분리한 캐릭터 ──────────────
        # 머리를 팔보다 위에 그려 어깨가 머리 밖으로 튀어나오지 않게 한다.
        if self.has.get("head") and not head_early:
            self._safe("head", self._draw_head, now, yo, pdx, pdy,
                       blinking, smiling, sleeping)
        if self.cfg.get("pen_over_head"):     # 퀸시: 깃펜이 맨 위 레이어
            self._safe("pen_hand", self._draw_pen_hand)

        # 수면 모드: 머리 위쪽에 둥실거리는 zzZ (머리보다 위에 그린다)
        if sleeping:
            hx0, hy0, hx1, hy1 = self._head_box
            zx = min(hx1 - 14, self.W - 42)
            zy = hy0 + self.oy + yo + 10
            for i, (dx, dy, size, color) in enumerate((
                    (0, 22, 10, "#aab7cc"),
                    (13, 4, 13, "#93a4c2"),
                    (28, -16, 16, "#7c90b5"))):
                bob = math.sin(now * 1.6 + i * 0.9) * 3
                c.create_text(zx + dx, zy + dy + bob, text="z" if i == 0 else "Z",
                              font=("Malgun Gothic", size, "bold"), fill=color)

        # ── 귀여운 연출: 고깔모자 → 폭죽 → 말풍선 (맨 위) ────────────────
        if self.fun:
            self._safe("hat", self._draw_hat, yo)
            self._safe("particles", self._draw_particles)
            self._safe("bubble", self._draw_bubble, yo)
        self._safe("pet_shadow", self._update_pet_shadow)

    def _draw_arms(self, now, f, yo, pen_typing, cx, cy):
        """펜 추적 팔 또는 타이핑 팔 (환경 의존 코드가 많아 따로 격리)."""
        c = self.canvas
        # ── 오른손/오른팔: 펜 추적 또는 타이핑 파츠(어깨 축 회전) ────────
        if self.arm_pil is None or "arm_key" not in self.hop:
            return                      # 팔 파츠가 없으면 팔만 생략
        if pen_typing and "pen" not in f and "arm_right_typing" in self.hop:
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
            if drawing and not getattr(self, "_stroke_prev", False):
                self.stat["strokes"] = self.stat.get("strokes", 0) + 1
            self._stroke_prev = drawing
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
            self._pen_draw = (px + ddx, py + ddy)
            if not self.cfg.get("pen_over_head"):
                self._draw_pen_hand()
            self._draw_left(now, f)
            # 연필 사각거림: 스트로크마다 클립 한 번 (짧은/긴 선 동일)
            if self.pensnd is not None and "pen" not in f:
                if drawing:
                    self._pen_release_t = None
                    if not self._pen_playing:
                        self.pensnd.play()
                        self._pen_playing = True
                elif self._pen_playing:
                    # 펜압 흔들림으로 잠깐 떨어지는 것은 무시(70ms 유예)
                    if self._pen_release_t is None:
                        self._pen_release_t = now
                    elif now - self._pen_release_t > 0.07:
                        self._pen_playing = False


    def _draw_pen_hand(self):
        """펜 쥔 손. 퀸시처럼 펜이 맨 위 레이어인 캐릭터는 머리를 그린 뒤 호출.

        늘어나는 오른팔은 목도리 아래로 들어가야 하므로 여기서 그리지 않는다.
        """
        d = self._pen_draw
        if not d:
            return
        px, py = d
        self._put("arm_pen", px, py)
        self._pen_draw = None

    def _draw_head(self, now, yo, pdx, pdy, blinking, smiling, sleeping):
        """머리 + 얼굴 (자는 중이면 목을 축으로 기울인 합성본)."""
        c = self.canvas
        if sleeping and self._tilt_max >= 2:       # 꾸벅 — 살짝 기울여 잔다
            m = self._tilt_max
            tilt = -(m * 0.78 + m * 0.22 * math.sin(now * 0.55))
            p = self.TILT_PAD
            img, tdx = self._sleep_head(tilt)
            c.create_image(tdx - p, self.oy - p + yo, anchor="nw", image=img)
            self._draw_snot(now, yo, tilt, tdx)
        else:
            hx, hy = self._pos("head")
            self._put("head", hx, hy + yo)
            self._draw_face(yo, pdx, pdy, blinking, smiling)

    def _draw_face(self, yo, pdx, pdy, blinking, smiling=False):
        """눈동자(시선) 또는 감은 눈/웃는 얼굴 + 눈 위 덮개들."""
        c = self.canvas
        if smiling:                       # 웃는 표정 파츠가 눈을 대신한다
            drawn = False
            for name in (self.layout.get("overlays") or []):
                if name in ("body_mask", "lashes"):
                    continue
                if name == "eyes_closed":
                    sx, sy = self._pos("smile")
                    self._put("smile", sx, sy + yo)
                    drawn = True
                    continue
                if not self.has.get(name) or name == "head":
                    continue
                ox, oy_ = self._pos(name)
                self._put(name, ox, oy_ + yo)
            if not drawn:
                sx, sy = self._pos("smile")
                self._put("smile", sx, sy + yo)
            return
        if not blinking:
            ex, ey = self._pos("pupils")
            self._put("pupils", ex + pdx, ey + yo + pdy)
        elif self.blink_cfg is not None:
            (x0, y0, x1, y1), color = self.blink_cfg
            c.create_rectangle(x0, y0 + yo, x1, y1 + yo, fill=color, outline="")
        overlays = self.layout.get("overlays") or \
            ["body_mask", "lashes", "eyes_closed", "hair"]
        for name in overlays:
            if name == "head":
                continue                # 머리는 별도 처리
            if name == "eyes_closed":
                if not (blinking and self.has.get("eyes_closed")):
                    continue
            elif not self.has.get(name):
                continue
            ox, oy_ = self._pos(name)
            self._put(name, ox, oy_ + yo)

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
        """캔버스로 직접 그린 설정 창 — 그룹 카드 · 토글 · 스테퍼 · 슬라이더."""
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        cd = self.card
        PANEL, SOFT, LINE = cd["panel"], cd["soft"], cd["line"]
        W, PAD, ROW, IN = 372, 20, 40, 18
        FONT = "Malgun Gothic"
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title(f"{self.cfg.get('name', self.char)} 설정")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg=PANEL)

        st = dict(self.us)
        st["show_timer"] = bool(self.timer_on)
        if st.get("sound_pack") not in self.sound_packs and self.sound_packs:
            st["sound_pack"] = self.sound_packs[0]

        cv = tk.Canvas(win, width=W, height=640, bg=PANEL, highlightthickness=0)
        cv.pack()
        apps_var = tk.StringVar(value=str(st.get("work_apps", "")))
        apps_entry = tk.Entry(win, textvariable=apps_var, font=(FONT, 8),
                              relief="flat", bg="#ffffff", fg=cd["text"],
                              highlightthickness=0, borderwidth=0)
        hits, sliders = [], []
        RX = W - PAD - IN            # 오른쪽 컨트롤 기준선
        LX = PAD + IN                # 왼쪽 라벨 기준선

        def rrect(x0, y0, x1, y1, r, **kw):
            pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                   x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
            return cv.create_polygon(pts, smooth=True, **kw)

        def header(y):
            """캐릭터 귀 + 이름 헤더."""
            hx0, hx1 = PAD, W - PAD
            deco = cd.get("deco")
            if deco == "scarf":                 # 퀸시: 귀 대신 목도리 띠
                rrect(hx0 + 20, y - 6, hx1 - 20, y + 22, 10,
                      fill=cd["border"], outline="")
                span = (hx1 - hx0 - 96) / 4
                for i in range(5):
                    sx = hx0 + 56 + i * span
                    cv.create_line(sx, y - 3, sx - 9, y + 20,
                                   fill="#dfe5f0", width=4)
                rrect(hx0, y + 10, hx1, y + 62, 18, fill=SOFT,
                      outline=cd["border"], width=2)
                cv.create_text(W / 2, y + 36,
                               text=f"{self.cfg.get('name', self.char)} 설정",
                               font=(FONT, 12, "bold"), fill=cd["text"])
                return y + 78
            ec = {"cat": "#f5bdd2", "rose": "#f5bdd2"}.get(deco, "#2b2b2b")
            for ex in (hx0 + 34, hx1 - 34):
                if deco == "cat":
                    cv.create_polygon(ex - 13, y + 18, ex + 2, y - 8, ex + 13, y + 17,
                                      fill=ec, outline=cd["border"], width=2)
                else:
                    cv.create_oval(ex - 13, y - 8, ex + 13, y + 18, fill=ec, outline="")
            rrect(hx0, y + 10, hx1, y + 62, 18, fill=SOFT,
                  outline=cd["border"], width=2)
            cv.create_text(W / 2, y + 36, text=f"{self.cfg.get('name', self.char)} 설정",
                           font=(FONT, 12, "bold"), fill=cd["text"])
            return y + 78

        def group(y, title, rows):
            """제목 + 흰 카드 안에 행들을 균등 배치."""
            cv.create_oval(PAD + 3, y - 4, PAD + 11, y + 4,
                           fill=cd["fill"], outline="")
            cv.create_text(PAD + 18, y, anchor="w", text=title,
                           font=(FONT, 9, "bold"), fill=cd["fill"])
            y += 16
            h = ROW * len(rows) + 14
            rrect(PAD, y, W - PAD, y + h, 16, fill="#ffffff",
                  outline=LINE, width=1)
            ry = y + 7 + ROW / 2
            for fn in rows:
                fn(ry)
                ry += ROW
            return y + h + 20

        def label(y, text):
            cv.create_text(LX, y, anchor="w", text=text,
                           font=(FONT, 9), fill=cd["text"])

        def toggle(y, text, key):
            label(y, text)
            on = bool(st.get(key))
            x1, x0 = RX, RX - 46
            rrect(x0, y - 11, x1, y + 11, 11,
                  fill=cd["fill"] if on else "#e2e0e6", outline="")
            kx = x1 - 12 if on else x0 + 12
            cv.create_oval(kx - 8.5, y - 8.5, kx + 8.5, y + 8.5,
                           fill="#ffffff", outline="")

            def flip(k=key):
                st[k] = not bool(st.get(k))
            hits.append((x0 - 6, y - 16, x1 + 6, y + 16, flip))

        def stepper(y, text, key, lo, hi, step, suffix=""):
            label(y, text)
            val = float(st.get(key, lo))
            for sign, cx in ((1, RX - 13), (-1, RX - 99)):
                cv.create_oval(cx - 13, y - 13, cx + 13, y + 13,
                               fill=SOFT, outline=cd["border"], width=1)
                cv.create_line(cx - 5, y, cx + 5, y, width=2,
                               capstyle="round", fill=cd["text"])
                if sign > 0:
                    cv.create_line(cx, y - 5, cx, y + 5, width=2,
                                   capstyle="round", fill=cd["text"])

                def bump(s=sign, k=key, lo=lo, hi=hi, stp=step):
                    v = float(st.get(k, lo)) + s * stp
                    st[k] = max(lo, min(hi, round(v, 2)))
                hits.append((cx - 15, y - 15, cx + 15, y + 15, bump))
            cv.create_text(RX - 56, y, text=f"{val:g}{suffix}",
                           font=(FONT, 9, "bold"), fill=cd["text"])

        def slider(y, text, key, lo, hi):
            label(y, text)
            val = float(st.get(key, lo))
            sx0, sx1 = RX - 148, RX - 46
            cv.create_line(sx0, y, sx1, y, width=6, capstyle="round", fill="#efedf1")
            frac = (val - lo) / max(hi - lo, 1)
            if frac > 0.01:
                cv.create_line(sx0, y, sx0 + (sx1 - sx0) * frac, y, width=6,
                               capstyle="round", fill=cd["fill"])
            kx = sx0 + (sx1 - sx0) * frac
            cv.create_oval(kx - 9, y - 9, kx + 9, y + 9, fill="#ffffff",
                           outline=cd["fill"], width=2)
            cv.create_text(RX, y, anchor="e", text=f"{val:g}",
                           font=(FONT, 9, "bold"), fill=cd["text"])
            sliders.append((sx0, sx1, y, key, lo, hi))

        def chevron(cx, y, sign):
            """sign -1이면 ‹, +1이면 › 모양."""
            for dy in (-5, 5):
                cv.create_line(cx - sign * 3, y + dy, cx + sign * 3, y,
                               width=2, capstyle="round", fill=cd["fill"])

        def picker(y, text, key, options):
            label(y, text)
            if not options:
                cv.create_text(RX, y, anchor="e", text="(없음)",
                               font=(FONT, 8), fill=cd["sub"])
                return
            cur = st.get(key, options[0])
            idx = options.index(cur) if cur in options else 0
            bx0, bx1 = RX - 176, RX
            rrect(bx0, y - 14, bx1, y + 14, 14, fill=SOFT,
                  outline=cd["border"], width=1)
            name = options[idx]
            if len(name) > 16:
                name = name[:15] + "…"
            cv.create_text((bx0 + bx1) / 2, y, text=name,
                           font=(FONT, 8), fill=cd["text"])
            for sign, cx in ((-1, bx0 + 15), (1, bx1 - 15)):
                chevron(cx, y, sign)

                def cyc(s=sign, k=key, o=options):
                    i = (o.index(st.get(k, o[0])) if st.get(k) in o else 0)
                    st[k] = o[(i + s) % len(o)]
                hits.append((cx - 13, y - 14, cx + 13, y + 14, cyc))

        def draw():
            cv.delete("all")
            hits.clear()
            sliders.clear()
            y = header(24)
            y = group(y, "타이머", [
                lambda ry: stepper(ry, "목표 작업시간", "goal_hours", 0.5, 16, 0.5, "h"),
                lambda ry: stepper(ry, "휴식 전환", "idle_sec", 5, 600, 5, "초"),
                lambda ry: stepper(ry, "잠들기", "sleep_min", 1, 120, 1, "분"),
                lambda ry: toggle(ry, "작업 타이머 표시", "show_timer"),
                lambda ry: toggle(ry, "작업 프로그램에서만 측정", "work_apps_only"),
            ])
            y = group(y, "소리", [
                lambda ry: slider(ry, "타자 소리 볼륨", "sound_volume", 0, 100),
                lambda ry: slider(ry, "펜 소리 볼륨", "pen_volume", 0, 100),
                lambda ry: toggle(ry, "타자 소리", "sound"),
                lambda ry: picker(ry, "소리 팩", "sound_pack", self.sound_packs),
            ])
            disp = []
            if len(self.skins) > 1:
                disp.append(lambda ry: picker(ry, "패션", "skin", self.skin_names))
            disp += [
                lambda ry: stepper(ry, "캐릭터 크기", "scale_pct", 50, 200, 10, "%"),
                lambda ry: toggle(ry, "캐릭터 그림자", "shadow"),
                lambda ry: toggle(ry, "타블렛 낙서 표시", "trail"),
                lambda ry: toggle(ry, "항상 위에 표시", "topmost"),
            ]
            if getattr(sys, "frozen", False):
                disp.append(lambda ry: toggle(ry, "윈도우 시작 시 자동 실행",
                                              "autostart"))
            y = group(y, "표시", disp)

            cv.create_oval(PAD + 3, y - 4, PAD + 11, y + 4,
                           fill=cd["fill"], outline="")
            cv.create_text(PAD + 18, y, anchor="w", text="작업 프로그램",
                           font=(FONT, 9, "bold"), fill=cd["fill"])
            cv.create_text(W - PAD - 4, y, anchor="e", text="쉼표로 구분",
                           font=(FONT, 8), fill=cd["sub"])
            y += 16
            rrect(PAD, y, W - PAD, y + 50, 16, fill="#ffffff",
                  outline=LINE, width=1)
            cv.create_window(LX, y + 13, anchor="nw", window=apps_entry,
                             width=W - PAD * 2 - IN * 2, height=24)
            y += 50 + 22

            cv.create_text(W / 2, y, text="패션 · 크기 · 타이머는 저장 시 재시작",
                           font=(FONT, 8), fill=cd["sub"])
            y += 22
            bx0, bx1 = W / 2 - 64, W / 2 + 64
            rrect(bx0, y, bx1, y + 40, 18, fill=cd["fill"], outline="")
            cv.create_text(W / 2, y + 20, text="저장",
                           font=(FONT, 10, "bold"), fill="#ffffff")
            hits.append((bx0, y, bx1, y + 40, save))
            cv.config(height=y + 40 + 22)

        def set_slider(key, x, sx0, sx1, lo, hi):
            frac = min(1.0, max(0.0, (x - sx0) / max(sx1 - sx0, 1)))
            step = 5 if hi > 20 else 1
            st[key] = int(round((lo + (hi - lo) * frac) / step) * step)

        def on_click(e):
            for x0, y0, x1, y1, fn in hits:
                if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                    fn()
                    if win.winfo_exists():
                        draw()
                    return
            for sx0, sx1, sy, key, lo, hi in sliders:
                if sx0 - 12 <= e.x <= sx1 + 12 and sy - 14 <= e.y <= sy + 14:
                    set_slider(key, e.x, sx0, sx1, lo, hi)
                    draw()
                    return

        def on_drag(e):
            for sx0, sx1, sy, key, lo, hi in sliders:
                if sy - 16 <= e.y <= sy + 16:
                    set_slider(key, e.x, sx0, sx1, lo, hi)
                    draw()
                    return

        def save():
            new = dict(st)
            new["work_apps"] = apps_var.get().strip()
            new["goal_hours"] = float(new["goal_hours"])
            new["idle_sec"] = max(float(new["idle_sec"]), 5.0)
            new["sleep_min"] = max(1, int(new["sleep_min"]))
            new["scale_pct"] = max(50, min(200, int(new["scale_pct"])))
            for k in ("sound_volume", "pen_volume"):
                new[k] = max(0, min(100, int(new[k])))
            need_restart = (new["scale_pct"] != self.us["scale_pct"]
                            or new.get("skin") != self.us.get("skin")
                            or bool(new["show_timer"]) != self.timer_on
                            or bool(new["shadow"]) != bool(self.us.get("shadow", True)))
            self.us.update(new)
            self._save_settings()
            self.idle_thr = self.us["idle_sec"]
            self.root.attributes("-topmost", bool(self.us["topmost"]))
            self._init_sound()
            self._apply_autostart()
            win.destroy()
            if need_restart:
                self._restart()

        cv.bind("<Button-1>", on_click)
        cv.bind("<B1-Motion>", on_drag)
        draw()
        # 화면 밖으로 나가 저장 버튼이 잘리지 않게 위치 보정
        win.update_idletasks()
        wh, ww = win.winfo_height(), win.winfo_width()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = min(max(self.root.winfo_rootx() - 70, 10), max(sw - ww - 10, 10))
        py = min(max(self.root.winfo_rooty() - 30, 10), max(sh - wh - 60, 10))
        win.geometry(f"+{int(px)}+{int(py)}")


    def _sanitize_settings(self):
        """저장된 설정 값이 빈 문자열·null·엉뚱한 형이면 기본값으로 되돌린다.

        옛 설정 창은 텍스트 입력이라 ""가 저장될 수 있었고, 그대로 float()에
        들어가면 매 프레임 예외가 나 화면이 통째로 비어 버린다.
        """
        for k, dv in DEFAULT_SETTINGS.items():
            v = self.us.get(k, dv)
            if isinstance(dv, bool):
                self.us[k] = bool(v)
            elif isinstance(dv, (int, float)):
                try:
                    self.us[k] = type(dv)(float(v))
                except (TypeError, ValueError):
                    self.us[k] = dv
            elif isinstance(dv, str) and not isinstance(v, str):
                self.us[k] = dv
        self.us["sleep_min"] = max(1, int(self.us["sleep_min"]))
        self.us["idle_sec"] = max(5.0, float(self.us["idle_sec"]))
        self.us["goal_hours"] = max(0.5, float(self.us["goal_hours"]))
        self.us["scale_pct"] = max(50, min(200, int(self.us["scale_pct"])))

    def _save_settings(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fp:
                json.dump(self.us, fp, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _apply_autostart(self):
        """로그인 시 자동 실행 등록/해제 (배포본만). 윈도우=레지스트리, 맥=LaunchAgent."""
        if not getattr(sys, "frozen", False):
            return                       # 소스 실행(로컬)에서는 의미 없음
        if IS_MAC:
            return self._apply_autostart_mac()
        try:
            import winreg
            name = os.path.splitext(os.path.basename(sys.executable))[0]
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as key:
                if self.us.get("autostart", True):
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ,
                                      f'"{sys.executable}"')
                else:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        except Exception:
            pass

    def _apply_autostart_mac(self):
        """~/Library/LaunchAgents 에 plist를 쓰거나 지운다 (맥 로그인 자동 실행)."""
        try:
            label = "com.ena.mascot." + self.char.replace("parts_", "")
            d = os.path.expanduser("~/Library/LaunchAgents")
            path = os.path.join(d, label + ".plist")
            if not self.us.get("autostart", True):
                if os.path.exists(path):
                    os.remove(path)
                return
            os.makedirs(d, exist_ok=True)
            app = sys.executable                  # .app 번들이면 open -a 로 실행
            while app and app != "/" and not app.endswith(".app"):
                app = os.path.dirname(app)
            args = ["/usr/bin/open", "-a", app] if app.endswith(".app")                 else [sys.executable]
            out = ['<?xml version="1.0" encoding="UTF-8"?>',
                   '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
                   ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
                   '<plist version="1.0">', '<dict>',
                   "    <key>Label</key>", f"    <string>{label}</string>",
                   "    <key>ProgramArguments</key>", "    <array>"]
            out += [f"        <string>{a}</string>" for a in args]
            out += ["    </array>", "    <key>RunAtLoad</key>", "    <true/>",
                    "</dict>", "</plist>", ""]
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(os.linesep.join(out))
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

    def _setup_mac_window(self):
        """맥 투명 창 설정. 방식별로 이미지가 보이는지 달라서 변형을 고를 수 있게 둔다."""
        mode = os.environ.get("MASCOT_MAC_MODE", "transparent")
        try:
            if mode == "opaque":                     # 대조군 (확실히 보임)
                self.root.config(bg="#808080")
                return "#808080"
            if mode == "canvasonly":                 # 창 속성 없이 캔버스만 투명
                self.root.config(bg="systemTransparent")
                return "systemTransparent"
            if mode == "late":                       # 창이 뜬 뒤에 투명 속성 부여
                self.root.config(bg="systemTransparent")
                self.root.update_idletasks()
                self.root.attributes("-transparent", True)
                return "systemTransparent"
            if mode == "alpha":                      # 투명 + 알파값으로 합성 경로 변경
                self.root.attributes("-transparent", True)
                self.root.config(bg="systemTransparent")
                self.root.attributes("-alpha", 0.999)
                return "systemTransparent"
            if mode == "emptybg":                    # 캔버스 배경색을 비움
                self.root.attributes("-transparent", True)
                self.root.config(bg="systemTransparent")
                return ""
            self.root.attributes("-transparent", True)   # 기본
            self.root.config(bg="systemTransparent")
            return "systemTransparent"
        except Exception:
            return TRANSPARENT

    def _mac_borderless(self):
        """맥에서 제목 표시줄 제거 — Tk 9는 overrideredirect만으로는 안 되는 경우가 있다."""
        if not IS_MAC:
            return
        try:
            self.root.update_idletasks()
            self.root.overrideredirect(False)
            self.root.overrideredirect(True)
        except Exception:
            pass
        try:                                   # 그래도 남으면 AppKit으로 직접
            from AppKit import NSApp
            self.root.update_idletasks()
            for w in NSApp.windows():
                try:
                    w.setStyleMask_(0)         # NSWindowStyleMaskBorderless
                    w.setHasShadow_(False)
                    w.setMovableByWindowBackground_(False)
                except Exception:
                    pass
        except Exception:
            pass

    def _dump_debug(self):
        """맥 진단용 상태 덤프 — 그림이 안 보일 때 원인 좁히기."""
        try:
            lines = [f"platform: win={IS_WIN} mac={IS_MAC}",
                     f"geometry: {self.root.winfo_geometry()} "
                     f"W={self.W} H={self.H} oy={self.oy} scale={self.s:.3f}",
                     f"canvas bg={self.canvas_bg} items={len(self.canvas.find_all())}",
                     f"parts_dir={self.parts_dir}",
                     f"loaded images={sorted(self.im)}"]
            for name in sorted(self.im):
                im = self.im[name]
                lines.append(f"  {name}: {im.width()}x{im.height()}")
            kinds = {}
            for it in self.canvas.find_all():
                k = self.canvas.type(it)
                kinds[k] = kinds.get(k, 0) + 1
            lines.append(f"canvas item kinds: {kinds}")
            for it in self.canvas.find_all():
                if self.canvas.type(it) == "image":
                    lines.append(f"  image at {self.canvas.coords(it)} "
                                 f"state={self.canvas.itemcget(it, 'state')!r}")
            with open(os.path.join(self.state_dir, "debug.txt"), "w",
                      encoding="utf-8") as fp:
                fp.write(os.linesep.join(lines))
        except Exception:
            import traceback
            with open(os.path.join(self.state_dir, "debug.txt"), "w",
                      encoding="utf-8") as fp:
                traceback.print_exc(file=fp)

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
