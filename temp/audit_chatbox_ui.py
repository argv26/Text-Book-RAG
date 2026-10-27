"""Exploratory test script to audit Chat Box UI elements and styling."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
index_html = (ROOT / "ui" / "static" / "index.html").read_text(encoding="utf-8")
styles_css = (ROOT / "ui" / "static" / "styles.css").read_text(encoding="utf-8")
app_js = (ROOT / "ui" / "static" / "app.js").read_text(encoding="utf-8")

print("=== CHAT BOX AUDIT ===")

# 1. Check textarea focus-visible conflict
has_textarea_focus_override = "textarea:focus-visible" in styles_css
has_chat_input_specific = "#chat-input" in styles_css
print(f"1. styles.css forces textarea:focus-visible with !important: {has_textarea_focus_override}")
print(f"   styles.css has specific #chat-input reset: {has_chat_input_specific}")

# 2. Check Tailwind non-standard classes
invalid_classes = re.findall(r"rounded-[a-z]+-xs", app_js) + re.findall(r"rounded-[a-z]+-xs", index_html)
print(f"2. Non-standard Tailwind classes found: {set(invalid_classes)}")

# 3. Check code & pre styling in prose-custom
has_inline_code = ".prose-custom code" in styles_css
has_pre_code = ".prose-custom pre" in styles_css
print(f"3. prose-custom inline code styled: {has_inline_code}")
print(f"   prose-custom pre block styled: {has_pre_code}")

# 4. Check send button disabled toggle logic on input
has_input_btn_state = "btnSend.disabled" in app_js and "chatInput.addEventListener(\"input\"" in app_js
# Check if input listener toggles disabled
input_listener_match = re.search(r"chatInput\.addEventListener\(\"input\",\s*\(\)\s*=>\s*\{([^}]+)\}\)", app_js)
if input_listener_match:
    print(f"4. Input listener body:\n{input_listener_match.group(1).strip()}")
else:
    print("4. Input listener not found")

# 5. Check placeholder length
placeholder_match = re.search(r'placeholder="([^"]+)"', index_html)
if placeholder_match:
    print(f"5. Placeholder text: '{placeholder_match.group(1)}' (length: {len(placeholder_match.group(1))})")

