"""URL normalization shared by the preview and execution APIs."""
import re
from urllib.parse import urlsplit, urlunsplit


def protocol_for_model(model: str) -> str:
    return "messages" if any(s in model.lower() for s in ("claude", "opus", "sonnet", "haiku")) else "responses"


def endpoint_for(base_url: str, model: str) -> str:
    raw = base_url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("请输入有效的 HTTP(S) 接口地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("地址不能包含用户名、密码、查询参数或 fragment")
    # Validate the port before handing the URL to the HTTP client.
    _ = parsed.port
    path = parsed.path.rstrip("/")
    explicit_endpoint = re.search(r"/(responses|messages|chat/completions)$", path)
    if explicit_endpoint:
        # Preserve an explicitly supplied versionless endpoint or custom prefix.
        path = path[:explicit_endpoint.start()]
    elif not re.search(r"/v\d+(?:beta\d*)?$", path):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/" + protocol_for_model(model), "", ""))


def canonical_model(model: str) -> str:
    # Only documented provider aliases; never fuzzy-match different versions.
    name = model.lower()
    return "claude-" + name if name.startswith(("opus-", "sonnet-", "haiku-")) else name
