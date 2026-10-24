"""使用 Windows 离线语音快速试听文本，不生成任务缓存。"""
import base64
import json
import os
from pathlib import Path

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$speech = $null
try {
    Add-Type -AssemblyName System.Speech
    $data = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voice = $speech.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq $data.language
    } | Select-Object -First 1
    if ($null -eq $voice) { throw 'No installed speech voice for this language.' }
    $speech.SelectVoice($voice.VoiceInfo.Name)
    if ($data.output) { $speech.SetOutputToWaveFile($data.output) }
    else { $speech.SetOutputToDefaultAudioDevice() }
    $speech.Speak([string]$data.text)
    [Console]::Out.WriteLine('{"done":true}')
} catch {
    [Console]::Out.WriteLine((@{error=$_.Exception.Message} | ConvertTo-Json -Compress))
} finally {
    if ($null -ne $speech) { $speech.Dispose() }
}
'''

def preview_sentence(text, language, control, output=None):
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")
    payload = json.dumps({"text": text, "language": language, "output": str(output) if output else None}, ensure_ascii=True)
    done = False
    for line in control.lines([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], payload=payload):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if "error" in event:
            raise RuntimeError(event["error"])
        done = done or event.get("done", False)
    if not done:
        raise RuntimeError("系统语音没有完成试听，请检查 Windows 语音与音频设备。")
