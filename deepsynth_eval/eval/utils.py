import json
import re
from typing import Any

def extract_json(text: str) -> Any:
    """
    Robust JSON extraction, handling Markdown code blocks or raw JSON strings.
    """
    # 1. Attempt to extract json block
    pattern = r"```(?:json)?\s*(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
    if matches:
        try:
            return json.loads(matches[-1].strip())
        except json.JSONDecodeError:
            pass

    # 2. Attempt to find first { and last } (for unclosed markdown)
    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end+1])
    except:
        pass

    # 3. Attempt to parse the whole string directly
    else:
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from response:\n{text[:500]}...")