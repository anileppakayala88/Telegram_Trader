import json, os

journals = [
    'journal/vip_thrilokh.jsonl',
    'journal/xauusd_big_lots.jsonl',
    'journal/kathy_zip_forex.jsonl',
]

rows = []
for path in journals:
    if not os.path.exists(path):
        continue
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

rows.sort(key=lambda r: r['timestamp'])
data_js = json.dumps(rows, ensure_ascii=False, indent=0)

template = open('journal_viewer_template.html', encoding='utf-8').read()
html = template.replace('__DATA_PLACEHOLDER__', data_js)

with open('journal_viewer.html', 'w', encoding='utf-8') as f:
    f.write(html)
print(f"Done — {len(rows)} entries embedded")
