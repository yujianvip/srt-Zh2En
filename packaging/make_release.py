"""生成需要首次运行安装脚本的 GitHub Release 小包。"""

import argparse
import hashlib
import zipfile
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.2")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    exe = root / "字幕翻译器-v1.0.exe"
    if not exe.is_file():
        raise FileNotFoundError("请先运行 build.bat，构建字幕翻译器-v1.0.exe。")

    assets = root / "release-assets"
    assets.mkdir(exist_ok=True)
    zip_path = assets / f"srt-Zh2En-v{args.version}-package.zip"
    files = [
        (exe, exe.name),
        *[(root / name, name) for name in (
            "setup_voxcpm.bat", "requirements-tts.txt", "README.md",
            "LICENSE", "THIRD_PARTY_NOTICES.md",
        )],
    ]
    files.extend((path, path.relative_to(root).as_posix())
                 for path in sorted((root / "third_party_licenses").rglob("*")) if path.is_file())
    for path, _ in files:
        if not path.is_file():
            raise FileNotFoundError(path)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for path, name in files:
            archive.write(path, name)
    with zipfile.ZipFile(zip_path) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"ZIP 校验失败：{broken}")

    checksums = assets / "SHA256SUMS.txt"
    checksums.write_text(
        f"{sha256(zip_path)}  {zip_path.name}\n",
        encoding="ascii",
    )
    print(f"ZIP: {zip_path} ({zip_path.stat().st_size / 1024**2:.1f} MiB)")
    print(f"SHA256: {checksums}")


if __name__ == "__main__":
    main()
