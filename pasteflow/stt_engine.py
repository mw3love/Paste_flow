"""음성 인식(STT) — 마이크 녹음 + mindlogic 게이트웨이 호출.

게이트웨이 오디오 입력은 **Gemini 계열만** 지원한다(2026-08-02 실측: 게이트웨이 41개 모델 중
18종을 계열별로 골라 `chat.completions`에 `input_audio` 파트로 실호출 — Gemini 8/8 성공,
GPT 7종·Claude·Grok 2종·Perplexity 전부 400 "audio unsupported"류 에러). 모델 단위가 아니라
계열 단위로 갈리므로 화이트리스트는 `ocr_engine.family_of()`로 계열째 판정한다.

`ocr_engine.py`의 게이트웨이 클라이언트 캐시(`_get_client`)를 그대로 재사용 — OCR과 STT가
같은 크리덴셜을 쓰므로 커넥션 풀(keep-alive)도 함께 공유한다.
"""
from __future__ import annotations

import base64
import io
import threading
import wave
from typing import Optional

import numpy as np
import sounddevice as sd

from .ocr_engine import _get_client, family_of

SAMPLE_RATE = 16000
MAX_SECONDS = 30  # 무한 녹음 방지 — 토큰 비용·응답 지연 억제. 도달 시 호출자가 stop()해야 함.


class Recorder:
    """푸시투토크 녹음 — start()로 시작, stop()으로 종료하고 wav bytes를 받는다.

    start()/stop() 쌍은 메인 스레드(단축키 훅 콜백)에서 호출된다고 가정. sounddevice의
    콜백은 별도 오디오 스레드에서 오므로 `_lock`으로 프레임 버퍼 접근을 보호한다.
    """

    def __init__(self) -> None:
        self._stream: Optional[sd.InputStream] = None
        self._frames: list[np.ndarray] = []
        self._sample_count = 0
        self._lock = threading.Lock()
        # 오디오 콜백 스레드가 쓰고 UI 스레드가 폴링해 읽는 최근 RMS 음량(0.0~1.0) — 단순
        # float 읽기/쓰기라 GIL 하에서 원자적이므로 락 없이 공유해도 안전(UI 미터용, 정밀도
        # 불필요). 녹음 중 이퀄라이저 표시(ui/stt_indicator.py)가 이 값을 폴링한다.
        self._level: float = 0.0

    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        """이미 녹음 중이면 무시(중복 keydown 방어 — 키 반복 입력 등)."""
        if self._stream is not None:
            return
        self._frames = []
        self._sample_count = 0
        # latency="low" — 기본(high) 버퍼링은 PortAudio가 언더런 방지용으로 크게 잡아
        # 마지막 발화가 stop() 시점에 아직 버퍼 안에 있어 잘리는 원인 중 하나였다
        # (2026-08-02 사용자 리포트: 말한 직후 떼면 끝이 잘림). main.py의 keyup 여유시간
        # (_STT_TAIL_PAD_MS)과 함께 적용한다.
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            latency="low",
            callback=self._callback,
        )
        stream.start()
        self._stream = stream

    def _callback(self, indata, frames, time_info, status) -> None:
        # RMS는 락 밖에서 계산(순수 계산, 공유 상태 미접근) — int16 범위(32768)로 정규화.
        rms = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2))) / 32768.0
        self._level = rms
        with self._lock:
            if self._sample_count >= MAX_SECONDS * SAMPLE_RATE:
                return  # 상한 도달 — 더 안 쌓음(호출자가 stop 호출할 때까지 무음 버림)
            self._frames.append(indata.copy())
            self._sample_count += frames

    def get_level(self) -> float:
        """가장 최근 콜백의 RMS 음량(0.0~1.0) — UI 이퀄라이저 폴링용."""
        return self._level

    def stop(self) -> bytes:
        """녹음 정지 후 wav bytes 반환. 녹음 중이 아니었거나 무음이면 빈 bytes."""
        stream, self._stream = self._stream, None
        if stream is None:
            return b""
        stream.stop()
        stream.close()
        with self._lock:
            frames = self._frames
            self._frames = []
        if not frames:
            return b""
        audio = np.concatenate(frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()


def _stt_prompt(language: str) -> str:
    if language.startswith("ko"):
        return (
            "이 음성을 정확히 한국어 텍스트로 받아써줘. 문장부호는 자연스럽게 넣되 "
            "내용을 요약하거나 의역하지 말고, 다른 설명 없이 인식된 텍스트만 출력해."
        )
    return "Transcribe this audio exactly as spoken. Output only the transcribed text with no explanation."


def transcribe(wav_bytes: bytes, api_key: str, base_url: str, model: str, language: str = "ko") -> str:
    """게이트웨이로 STT 요청.

    `family_of(model)`가 Gemini가 아니면 호출 전에 막는다 — GPT/Claude 등에 보내면
    400으로 크레딧만 낭비하고 실패가 확정적이므로(2026-08-02 18종 전수 확인).
    """
    if not api_key:
        raise RuntimeError("API 키가 설정되지 않았습니다. 설정에서 API 키를 입력하세요.")
    if not base_url:
        raise RuntimeError("Base URL이 설정되지 않았습니다. 설정에서 게이트웨이 주소를 입력하세요.")
    if not wav_bytes:
        raise RuntimeError("녹음된 음성이 없습니다.")
    if family_of(model) != "Gemini":
        raise RuntimeError(f"'{model}'은(는) 음성 입력을 지원하지 않습니다 (Gemini 계열만 가능).")

    client = _get_client(api_key, base_url)
    b64 = base64.standard_b64encode(wav_bytes).decode()
    resp = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
                {"type": "text", "text": _stt_prompt(language)},
            ],
        }],
    )
    return (resp.choices[0].message.content or "").strip()
