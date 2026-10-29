#!/usr/bin/env python3
"""
Claude / Oxion AI Android Build Fixer
Автоматический модуль анализа и исправления ошибок сборки Android-проектов через Oxion AI / Claude API.
"""

import os
import sys
import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any

AI_API_KEY = os.getenv("AI_API_KEY", os.getenv("FLUA_API_KEY", os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", "sk-flua-OWGaHaE0ZaiwM6vzLPARSV8IMF1aiydRjsLQ92Hc"))))
AI_BASE_URL = os.getenv("AI_BASE_URL", os.getenv("FLUA_BASE_URL", "https://api.flua.ink/v1"))
AI_MODEL = os.getenv("AI_MODEL", os.getenv("CLAUDE_MODEL", "claude-opus-5-5"))


def normalize_api_key(key: str) -> str:
    k = key.strip()
    if k.startswith("Sk-"):
        k = "sk-" + k[3:]
    elif k.startswith("Cc_"):
        k = "cc_" + k[3:]
    return k

def call_ai_api_sync(api_key: str, prompt: str, system_prompt: str = "", model: str = AI_MODEL, base_url: str = AI_BASE_URL) -> str:
    key = normalize_api_key(api_key)
    if key.lower().startswith("sk-flua-") and base_url == "https://oxionai.com/v1":
        base_url = "https://api.flua.ink/v1"

    """Выполняет HTTPS-запрос к Oxion AI / OpenAI-compatible API или нативному Anthropic API."""
    key = normalize_api_key(api_key)

    # 1. Если ключ нативного Anthropic (начинается с sk-ant-)
    if key.startswith("sk-ant-") and "anthropic.com" in base_url:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "Claude-Android-Build-Fixer/1.0",
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                res_json = json.loads(resp_body)
                content_blocks = res_json.get("content", [])
                text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                return "".join(text_parts).strip()
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic API error ({e.code}): {err_msg}")

    # 2. Иначе Oxion AI / OpenAI-compatible API (https://oxionai.com/v1/chat/completions)
    endpoint = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "OxionAI-Android-Build-Fixer/1.0",
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            res_json = json.loads(resp_body)
            choices = res_json.get("choices", [])
            if not choices:
                raise RuntimeError(f"Пустой ответ от AI API: {res_json}")
            msg = choices[0].get("message", {})
            return msg.get("content", "").strip()
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI API error ({e.code}) from {endpoint}: {err_msg}")


def collect_project_files(project_dir: Path, error_log: str) -> Dict[str, str]:
    """Находит конфигурационные файлы и исходники, упомянутые в логе ошибки."""
    collected: Dict[str, str] = {}
    max_bytes = 64 * 1024

    priority_files = [
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
        "AndroidManifest.xml",
        "Android.mk",
        "Application.mk",
        "CMakeLists.txt",
    ]

    for p in priority_files:
        for f in project_dir.rglob(p):
            if "build" in f.parts or ".gradle" in f.parts:
                continue
            try:
                rel = str(f.relative_to(project_dir))
                if f.stat().st_size <= max_bytes:
                    collected[rel] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    mentioned = set(re.findall(r"([a-zA-Z0-9_\-]+\.(?:java|kt|aidl|cpp|c|h|hpp|xml|mk|properties))", error_log))
    for name in mentioned:
        for f in project_dir.rglob(name):
            if "build" in f.parts or ".gradle" in f.parts:
                continue
            try:
                rel = str(f.relative_to(project_dir))
                if rel not in collected and f.stat().st_size <= max_bytes:
                    collected[rel] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    return collected


def run_claude_autofix(project_dir: Path, error_log_text: str, api_key: str = AI_API_KEY, model: str = AI_MODEL, base_url: str = AI_BASE_URL) -> Optional[dict]:
    """Отправляет диагностический запрос в AI (Oxion AI Claude-opus-5) и применяет предложенные правки."""
    if not api_key:
        print("[!] AI API Key не задан. Авто-исправление пропущено.")
        return None

    files_dict = collect_project_files(project_dir, error_log_text)

    log_slice = error_log_text
    if "* What went wrong:" in error_log_text:
        idx = error_log_text.find("* What went wrong:")
        log_slice = error_log_text[max(0, idx - 400):idx + 3600]
    elif len(error_log_text) > 4000:
        log_slice = error_log_text[-4000:]

    files_text = []
    for rel_path, content in files_dict.items():
        files_text.append(f"--- File: {rel_path} ---\n{content}\n")

    prompt = f"""The Android Gradle build failed with the following error:

=== BUILD ERROR LOG ===
{log_slice}

=== PROJECT CONFIGURATION & SOURCE FILES ===
{''.join(files_text)}

Analyze the exact cause of the build failure and provide full corrected files to fix it.
Return ONLY valid JSON matching this schema:
{{
  "analysis": "Short 1-2 sentence description in Russian of the error cause and the fix",
  "files_to_modify": [
    {{
      "path": "relative/path/to/file",
      "action": "overwrite",
      "content": "<full file content>"
    }}
  ]
}}
"""
    system_prompt = (
        "You are an expert Android build systems engineer specializing in Gradle, AGP, NDK, C++, Kotlin and Java. "
        "Diagnose the build failure and provide full corrected files in pure JSON without markdown codeblock fences."
    )

    print(f"[*] Отправка запроса в AI ({model} via {base_url})...")
    raw_resp = call_ai_api_sync(api_key, prompt, system_prompt, model=model, base_url=base_url)

    cleaned = raw_resp.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            raise RuntimeError(f"Не удалось распарсить JSON-ответ от AI: {cleaned[:300]}")

    analysis = data.get("analysis", "Без описания")
    print(f"[+] Диагноз AI: {analysis}")

    modified_files = []
    for item in data.get("files_to_modify", []):
        rel_path = item.get("path", "")
        action = item.get("action", "overwrite")
        content = item.get("content", "")
        if not rel_path:
            continue
        target = (project_dir / rel_path).resolve()
        if not str(target).startswith(str(project_dir.resolve())):
            continue
        if action == "delete":
            if target.exists():
                target.unlink()
                modified_files.append(f"{rel_path} (удален)")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            modified_files.append(rel_path)

    print(f"[+] Изменены файлы: {', '.join(modified_files)}")
    return {
        "analysis": analysis,
        "modified_files": modified_files,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Использование: {sys.argv[0]} <project_dir> <error_log_path> [api_key] [model] [base_url]")
        sys.exit(1)

    p_dir = Path(sys.argv[1]).resolve()
    log_path = Path(sys.argv[2]).resolve()
    key = sys.argv[3] if len(sys.argv) > 3 else AI_API_KEY
    mod = sys.argv[4] if len(sys.argv) > 4 else AI_MODEL
    b_url = sys.argv[5] if len(sys.argv) > 5 else AI_BASE_URL

    if not log_path.exists():
        print(f"Файл логов не найден: {log_path}")
        sys.exit(1)

    log_content = log_path.read_text(encoding="utf-8", errors="replace")
    result = run_claude_autofix(p_dir, log_content, key, model=mod, base_url=b_url)
    if result:
        print("[SUCCESS] Проект успешно обновлен правками AI.")
        sys.exit(0)
    else:
        print("[FAIL] Правки не были применены.")
        sys.exit(1)
