"""Offline graph overview with escaped labels and no remote JS/CDN dependencies."""

import html
import json
from pathlib import Path


def write_graph_html(store, output):
    graph = store.graph()
    groups = {}
    for uid, attributes in graph.nodes(data=True):
        groups.setdefault(attributes["kind"], []).append(uid)
    width, height = max(1000, len(groups) * 220), max(600, max(map(len, groups.values()), default=1) * 70 + 80)
    positions, elements = {}, []
    palette = ["#dbeafe", "#dcfce7", "#fef3c7", "#fce7f3", "#ede9fe", "#cffafe", "#e2e8f0"]
    for column, (kind, uids) in enumerate(groups.items()):
        x = 110 + column * 220
        elements.append(f'<text x="{x}" y="30" text-anchor="middle">{html.escape(kind)}</text>')
        for row, uid in enumerate(uids):
            positions[uid] = (x, 80 + row * 70)
    for source, target, attributes in graph.edges(data=True):
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        label = html.escape(attributes["relation"])
        elements.append(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="#94a3b8" opacity=".5" marker-end="url(#arrow)"><title>{label}</title></path>')
    for column, (kind, uids) in enumerate(groups.items()):
        for uid in uids:
            x, y = positions[uid]
            title = html.escape(json.dumps(graph.nodes[uid]["payload"], ensure_ascii=False, indent=2))
            label = html.escape(uid if len(uid) < 22 else uid[:19] + "…")
            elements.append(f'<g><title>{title}</title><rect x="{x - 90}" y="{y - 18}" width="180" height="36" rx="8" fill="{palette[column % len(palette)]}" stroke="#64748b"/><text x="{x}" y="{y + 4}" text-anchor="middle" font-size="12">{label}</text></g>')
    content = ('<!doctype html><meta charset="utf-8"><title>能力知识图谱</title>'
               '<style>body{font-family:system-ui;margin:24px;color:#0f172a}svg{background:#f8fafc}</style>'
               '<h1>算法能力知识图谱</h1><p>按实体类型排列，悬停节点查看完整属性。该图保存算法知识与验证历史。</p>'
               f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
               '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10" fill="#94a3b8"/></marker></defs>'
               + "".join(elements) + '</svg>')
    Path(output).write_text(content, encoding="utf-8")
