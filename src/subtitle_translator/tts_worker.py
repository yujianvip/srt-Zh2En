"""由桌面程序调用的 VoxCPM2 工作进程。依赖单独的 .venv-tts。"""

import json
import os
import sys
import wave
from pathlib import Path


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


def _write_reference_wav(path, audio, sample_rate):
    samples = (audio.clip(-1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())


def _default_voice_reference(model, torch, np, model_dir, sample_rate):
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
    temp = reference_path.with_suffix(".partial.wav")
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
        raise RuntimeError("未检测到 CUDA 版 PyTorch。请重新运行 setup_voxcpm.bat 安装 GPU 依赖。")

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    items = manifest["items"]
    groups = groups_from_items(items)
    output = Path(manifest["wav_path"])
    temp = output.with_suffix(".partial.wav")
    reference = manifest.get("reference_wav") or None
    model_dir = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "VoxCPM2"
    if not (model_dir / "model.safetensors").is_file() or not (model_dir / "audiovae.pth").is_file():
        from modelscope import snapshot_download
        snapshot_download("OpenBMB/VoxCPM2", local_dir=str(model_dir))
    model = VoxCPM.from_pretrained(str(model_dir), device="cuda", load_denoiser=False, optimize=False)
    sample_rate = int(model.tts_model.sample_rate)
    if not reference:
        reference = str(_default_voice_reference(model, torch, np, model_dir, sample_rate))
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
                start_sample = max(cursor_samples, round(original_start * sample_rate / 1000))
                if start_sample > cursor_samples:
                    wav_file.writeframes(b"\x00\x00" * (start_sample - cursor_samples))
                options = {"text": text, "cfg_value": 2.0, "inference_timesteps": 10}
                if reference:
                    options["reference_wav_path"] = reference
                audio = np.asarray(model.generate(**options), dtype=np.float32).reshape(-1)
                if audio.size == 0:
                    raise ValueError(f"第 {group_index} 组没有生成音频。")
                samples = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
                wav_file.writeframes(samples.tobytes())
                weights = [max(1, len(x["english"].split())) for x in group]
                weight_total = sum(weights)
                elapsed = 0
                for item, weight in zip(group, weights):
                    cue_start = start_sample + round(len(samples) * elapsed / weight_total)
                    elapsed += weight
                    cue_end = start_sample + round(len(samples) * elapsed / weight_total)
                    original_cue_start = ms_from_timecode(item["time"].split("-->")[0].strip())
                    positions.append({
                        "start_ms": round(cue_start * 1000 / sample_rate),
                        "end_ms": round(cue_end * 1000 / sample_rate),
                        "shift_ms": round(cue_start * 1000 / sample_rate) - original_cue_start,
                    })
                cursor_samples = start_sample + len(samples)
                progress(group_index, len(groups))
        os.replace(temp, output)
        Path(manifest["timing_path"]).write_text(
            json.dumps({"positions": positions, "sample_rate": sample_rate}, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        if temp.exists():
            temp.unlink()


if __name__ == "__main__":
    try:
        generate(sys.argv[1])
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        raise
