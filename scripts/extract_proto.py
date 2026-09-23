# -*- coding: utf-8 -*-
"""提取原型 HTML 的文本结构与样式信息（剥离 base64 图片）。"""
import re
import sys

path = sys.argv[1]
out_path = sys.argv[2] if len(sys.argv) > 2 else None
if out_path:
    sys.stdout = open(out_path, "w", encoding="utf-8")
raw = open(path, encoding="utf-8").read()
print("total len:", len(raw))

s = re.sub(r'data:image/[a-zA-Z0-9+.;=-]*;base64,[A-Za-z0-9+/=]+', "DATAIMG", raw)
print("after strip imgs:", len(s))

# 1) style blocks
styles = re.findall(r"<style[^>]*>(.*?)</style>", s, re.S)
print("== STYLE BLOCKS:", len(styles))
for st in styles:
    st = st.strip()
    if st:
        print(st[:4000])
        print("---")

# 2) tailwind config if any
m = re.findall(r"tailwind\.config\s*=\s*(\{.*?\})\s*</script>", s, re.S)
for cfg in m:
    print("== TAILWIND CONFIG ==")
    print(cfg[:2000])

# 3) body text content (strip tags) chunked by page markers
body = re.sub(r"<script.*?</script>", " ", s, flags=re.S)
body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
# keep structural hints: insert newline at page boundaries
body = re.sub(r'<!--.*?-->', lambda m: "\n[COMMENT]" + m.group(0)[:120] + "\n", body, flags=re.S)
text = re.sub(r"<[^>]+>", "|", body)
text = re.sub(r"[|\s]+", " ", text)
# split into chunks for readability
print("== TEXT CONTENT ==")
out = []
buf = ""
for token in text.split(" "):
    buf += token + " "
    if len(buf) > 300:
        out.append(buf)
        buf = ""
if buf:
    out.append(buf)
print("\n".join(out))
