import re
from pathlib import Path

p = Path("features_engine/src/hypotheses/modules.py")
text = p.read_text(encoding="utf-8")
text = re.sub(r"f\.get\('([^']+)',\s*([^)]+)\)", r"state.f('\1', \2)", text)
text = re.sub(r"\n\s+f = state\.primary_features\n", "\n", text)
p.write_text(text, encoding="utf-8")
print("patched")
