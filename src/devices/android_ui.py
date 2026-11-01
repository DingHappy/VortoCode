"""Bounded projection of Android native hierarchy; raw XML stays local."""
import hashlib
import re
from xml.etree import ElementTree

from src.devices.android import AndroidError

_SECRET = re.compile(r"password|passwd|passcode|(?:^|[\s_:/-])(?:otp|pin)(?:$|[\s_/-])|verification.?code|security.?code|access.?token|api.?key|secret", re.I)


def project(source, package):
    if not isinstance(source, str) or len(source.encode()) > 256 * 1024 or "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise AndroidError("Android 页面结构无效或过大")
    try:
        root = ElementTree.fromstring(source)
    except (ValueError, ElementTree.ParseError):
        raise AndroidError("Android 页面结构无法核对") from None
    nodes = list(root.iter())
    if len(nodes) > 2000:
        raise AndroidError("Android 页面元素超过上限")
    visible = [node for node in nodes if node.attrib.get("displayed", "true") != "false"]
    for node in visible:
        attrs = node.attrib
        if attrs.get("package") not in (None, "", package) and (attrs.get("clickable") == "true" or attrs.get("focusable") == "true"):
            raise AndroidError("Android 存在其它应用/系统交互层，请真人处理")
        if (attrs.get("password") == "true" or _SECRET.search(attrs.get("resource-id", "") + " " + attrs.get("content-desc", ""))
                or "WebView" in attrs.get("class", "")):
            raise AndroidError("Android 需要真人处理认证或嵌入页面；已暂停读取")
    rows = []
    editor_children = set()
    for node in visible:
        if "EditText" in node.attrib.get("class", "") or node.attrib.get("editable") == "true":
            editor_children.update(child for child in node.iter() if child is not node)
    for node in visible:
        if node in editor_children:
            continue
        a = node.attrib
        if a.get("package") != package:
            continue
        editable = "EditText" in a.get("class", "") or a.get("editable") == "true"
        # Never project editor values or turn an editor into a tap shortcut.
        label = "（输入框，内容已隐藏）" if editable else (a.get("text") or a.get("content-desc") or a.get("resource-id") or "")
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        bounds = tuple(map(int, match.groups())) if match else ()
        clickable = (not editable and a.get("clickable") == "true" and a.get("enabled", "true") == "true"
                     and len(bounds) == 4 and 0 <= bounds[0] < bounds[2] <= 10000 and 0 <= bounds[1] < bounds[3] <= 10000)
        if label or clickable:
            rows.append({"label": " ".join(label.split())[:100], "tap": bool(clickable), "bounds": bounds})
    if not rows or len(rows) > 150:
        raise AndroidError("Android 页面内容为空或超过投影上限")
    return hashlib.sha256(source.encode()).hexdigest(), rows
