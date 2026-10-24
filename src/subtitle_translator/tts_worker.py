"""由桌面程序调用的 VoxCPM2 工作进程。"""

import json
import os
import sys
import wave
from pathlib import Path
from modelscope.hub import ProgressCallback
from voice_model import MODEL_REPO, missing_model_files, model_directory


def ms_from_timecode(value):
    hours, minutes, seconds = value.split(":")
    sec, millis = seconds.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(sec)) * 1000 + int(millis)


def groups_from_items(items):
    groups = []
    current = []
    for item in items:
        if current and ms_from_timecode(item["time"].split("-->")[0].strip()) - ms_from_timecode(
            current[-1]["time"].split("-->")[1].strip()
        ) > 800:
            groups.append(current)
            current = []
        current.append(item)
        text = item["english"].rstrip()
        if text.endswith((".", "?", "!", "。", "？", "！")) or sum(
            len(x["english"]) for x in current
        ) >= 180:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def progress(done, total):
    print(json.dumps({"progress": done, "total": total}), flush=True)


class _ModelDownloadProgress(ProgressCallback):
    """Forward ModelScope's per-file download progress to the desktop UI."""

    def __init__(self, filename, file_size):
        super().__init__(filename, file_size)
        self.filename = Path(filename).name
        self.total = max(0, int(file_size or 0))
        self.done = 0
        self._emit()

    def update(self, size):
        self.done += max(0, int(size or 0))
        self._emit()

    def end(self):
        if self.total:
            self.done = self.total
        self._emit()

    def _emit(self):
        print(json.dumps({
            "model_download": {
                "filename": self.filename,
                "done": self.done,
                "total": self.total,
            }
        }), flush=True)


def _write_reference_wav(path, audio, sample_rate):
    samples = (audio.clip(-1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())


MAX_SPEEDUP = 1.25


def fit_audio_to_window(audio, target_samples, group_index):
    """Keep speech inside its source cue window without changing voice pitch."""
    if target_samples <= 0:
        raise ValueError(f"第 {group_index} 组字幕时间窗无效。")
    if len(audio) <= target_samples:
        return audio
    speedup = len(audio) / target_samples
    if speedup > MAX_SPEEDUP:
        raise ValueError(
            f"第 {group_index} 组英文配音比原字幕时间窗长 {speedup:.2f} 倍；"
            "为避免画面与配音错位，请缩短这组英文后重试。"
        )
    import librosa
    stretched = librosa.effects.time_stretch(audio, rate=speedup)
    return stretched[:target_samples]


def _default_voice_reference(model, torch, np, model_dir, sample_rate, task_temp_dir=None):
    """Generate and cache one deterministic seed voice for no-reference projects."""
    reference_path = model_dir / "default_english_voice.wav"
    if reference_path.is_file() and reference_path.stat().st_size > 1024:
        return reference_path

    seed = 20261023
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    text = (
        "Hello, and welcome. In this video, we'll walk through the main ideas "
        "step by step, with clear examples and practical details."
    )
    audio = np.asarray(
        model.generate(text=text, cfg_value=2.0, inference_timesteps=10),
        dtype=np.float32,
    ).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("生成默认英文音色失败，模型没有返回音频。")
    temp = (Path(task_temp_dir) / "default_english_voice.partial.wav") if task_temp_dir else reference_path.with_suffix(".partial.wav")
    try:
        _write_reference_wav(temp, audio, sample_rate)
        os.replace(temp, reference_path)
    finally:
        if temp.exists():
            temp.unlink()
    return reference_path


def generate(manifest_path):
    import numpy as np
    import torch
    from voxcpm import VoxCPM

    if not torch.cuda.is_available():
        raise RuntimeError("未检测到 CUDA 版 PyTorch。请检查显卡驱动或重新下载完整配音组件。")

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    items = manifest["items"]
    groups = groups_from_items(items)
    output = Path(manifest["wav_path"])
    temp = output.with_suffix(".partial.wav")
    reference = manifest.get("reference_wav") or None
    model_dir = download_model()
    model = VoxCPM.from_pretrained(str(model_dir), device="cuda", load_denoiser=False, optimize=False)
    sample_rate = int(model.tts_model.sample_rate)
    if not reference:
        reference = str(_default_voice_reference(model, torch, np, model_dir, sample_rate, manifest.get("task_temp_dir")))
    positions = []
    cursor_samples = 0
    try:
        with wave.open(str(temp), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            for group_index, group in enumerate(groups, 1):
                text = " ".join(x["english"].strip() for x in group).strip()
                if not text:
                    raise ValueError(f"第 {group_index} 组英文为空，请先修改字幕。")
                original_start = ms_from_timecode(group[0]["time"].split("-->")[0].strip())
                original_end = ms_from_timecode(group[-1]["time"].split("-->")[1].strip())
                start_sample = round(original_start * sample_rate / 1000)
                end_sample = round(original_end * sample_rate / 1000)
                if start_sample < cursor_samples:
                    raise ValueError(f"第 {group_index} 组字幕时间与上一组重叠，无法保持原视频节奏。")
                if start_sample > cursor_samples:
                    wav_file.writeframes(b"\x00\x00" * (start_sample - cursor_samples))
                options = {"text": text, "cfg_value": 2.0, "inference_timesteps": 10}
                if reference:
                    options["reference_wav_path"] = reference
                audio = np.asarray(model.generate(**options), dtype=np.float32).reshape(-1)
                if audio.size == 0:
                    raise ValueError(f"第 {group_index} 组没有生成音频。")
                audio = fit_audio_to_window(audio, end_sample - start_sample, group_index)
                samples = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
                wav_file.writeframes(samples.tobytes())
                for item in group:
                    cue_start = ms_from_timecode(item["time"].split("-->")[0].strip())
                    cue_end = ms_from_timecode(item["time"].split("-->")[1].strip())
                    positions.append({
                        "start_ms": cue_start,
                        "end_ms": cue_end,
                        "shift_ms": 0,
                    })
                padding = end_sample - start_sample - len(samples)
                if padding > 0:
                    wav_file.writeframes(b"\x00\x00" * padding)
                cursor_samples = end_sample
                progress(group_index, len(groups))
        os.replace(temp, output)
        Path(manifest["timing_path"]).write_text(
            json.dumps({"positions": positions, "sample_rate": sample_rate}, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        if temp.exists():
            temp.unlink()


def download_model():
    cache_root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    model_dir = model_directory(cache_root)
    if missing_model_files(cache_root):
        from modelscope import snapshot_download
        snapshot_download(MODEL_REPO, local_dir=str(model_dir),
                          progress_callbacks=[_ModelDownloadProgress])
    missing = missing_model_files(cache_root)
    if missing:
        raise RuntimeError("配音模型下载不完整：" + "、".join(missing))
    return model_dir


if __name__ == "__main__":
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "--download-model":
            download_model()
            print(json.dumps({"model_ready": True}), flush=True)
        else:
            generate(sys.argv[1])
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        raise
