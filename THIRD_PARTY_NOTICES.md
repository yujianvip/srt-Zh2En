# 第三方组件说明

当前 EXE 使用 `tkinterdnd2` 提供 Windows 文件拖入支持，并使用 PyInstaller 打包。依赖版本见 `requirements-build.txt`。

- `tkinterdnd2`：MIT 许可证，版权声明和完整条款见 [`third_party_licenses/tkinterdnd2-LICENSE`](third_party_licenses/tkinterdnd2-LICENSE)。
- `tkdnd`：由 `tkinterdnd2` 打包的拖放扩展，原项目声明见 [`third_party_licenses/tkdnd-license.terms`](third_party_licenses/tkdnd-license.terms)。
- Python 3.11 运行时：许可证文本见 [`third_party_licenses/Python-3.11-LICENSE`](third_party_licenses/Python-3.11-LICENSE)。
- PyInstaller：发布方说明其引导程序可随应用分发，许可说明见 [PyInstaller License](https://pyinstaller.org/en/stable/license.html)。

Ollama 由用户在本机单独安装和运行；本程序通过其本地 API 翻译，不打包 Ollama 或模型权重。VoxCPM2 在独立的 `.venv-tts` 中运行，模型权重存放在 `.model-cache`，也不包含在 EXE 内。VoxCPM2 项目采用 [Apache-2.0](https://github.com/OpenBMB/VoxCPM/blob/main/LICENSE)。

当前版本不打包 llama.cpp Vulkan 运行文件。发布 EXE 前，应按实际打包的依赖核对许可证，并附上相应许可文本。
