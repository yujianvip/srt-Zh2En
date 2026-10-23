"""通过正在运行的 Ollama 服务读取模型并翻译字幕。"""

import json
import urllib.parse
import urllib.request


MAX_MODELS_RESPONSE = 2 * 1024 * 1024
MAX_TRANSLATION_RESPONSE = 4 * 1024 * 1024


def _json_response(response, limit):
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Ollama 返回内容过大，请检查服务地址或模型输出。")
    return json.loads(body.decode("utf-8"))


def endpoint(address, path):
    base = address.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment or
            parsed.username or parsed.password):
        raise ValueError("Ollama 地址应为 http://localhost:11434 这样的服务根地址。")
    return base + path


def list_models(address):
    with urllib.request.urlopen(endpoint(address, "/api/tags"), timeout=15) as response:
        data = _json_response(response, MAX_MODELS_RESPONSE)
    return sorted(model["name"] for model in data.get("models", []) if model.get("name"))


def _request_batch(url, model, texts):
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": (
                "Translate Chinese subtitle segments into accurate, natural spoken English. "
                "Preserve meaning, names, technical terms and one-to-one item order. "
                "Do not merge or omit segments. Return only a JSON object with a 'translations' "
                "array containing exactly one English string per input item."
            )},
            {"role": "user", "content": json.dumps(texts, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.2},
    }
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = _json_response(response, MAX_TRANSLATION_RESPONSE)
    try:
        content = json.loads(data["message"]["content"])
        values = content["translations"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("模型没有返回约定的 JSON 译文。") from exc
    if not isinstance(values, list) or len(values) != len(texts):
        count = len(values) if isinstance(values, list) else "无列表"
        raise ValueError(f"需要 {len(texts)} 条英文，模型返回 {count} 条。")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("模型返回了空白或无效的英文。")
    return [value.strip() for value in values]


def _translate_batch_with_fallback(url, model, texts, start_index):
    try:
        return _request_batch(url, model, texts)
    except ValueError:
        if len(texts) == 1:
            try:
                return _request_batch(url, model, texts)
            except ValueError as retry_exc:
                raise ValueError(f"第 {start_index + 1} 条字幕多次翻译失败：{retry_exc}") from retry_exc
        middle = len(texts) // 2
        return (
            _translate_batch_with_fallback(url, model, texts[:middle], start_index)
            + _translate_batch_with_fallback(url, model, texts[middle:], start_index + middle)
        )


def translate(items, model, address, progress=None):
    url = endpoint(address, "/api/chat")
    translated = []
    for offset in range(0, len(items), 16):
        batch = items[offset:offset + 16]
        values = _translate_batch_with_fallback(url, model, [item["text"] for item in batch], offset)
        translated.extend(values)
        if progress:
            progress(min(offset + len(batch), len(items)), len(items))
    return translated
