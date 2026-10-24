"""VoxCPM2 模型文件的位置和完整性检查。"""

from pathlib import Path


MODEL_REPO = "OpenBMB/VoxCPM2"
REQUIRED_FILES = {
    "model.safetensors": 1_000_000_000,
    "audiovae.pth": 100_000_000,
    "config.json": 1,
    "tokenizer.json": 1,
    "tokenizer_config.json": 1,
    "tokenization_voxcpm2.py": 1,
}


def model_directory(cache_root):
    return Path(cache_root) / "VoxCPM2"


def missing_model_files(cache_root):
    folder = model_directory(cache_root)
    missing = []
    for name, minimum_bytes in REQUIRED_FILES.items():
        path = folder / name
        try:
            if path.stat().st_size < minimum_bytes:
                missing.append(name)
        except OSError:
            missing.append(name)
    return missing
