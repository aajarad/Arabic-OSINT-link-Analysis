"""Render a run summary as JSON and a self-contained, RTL-aware HTML report."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Dict, List

from ..utils import utc_now_iso


class Reporter:
    def __init__(self, summary: Dict) -> None:
        self.summary = summary

    def to_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def to_html(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_html(), encoding="utf-8")
        return path

    def render_html(self) -> str:
        s = self.summary or {}
        esc = _esc

        def table(items: List[Dict], cols: List[tuple]) -> str:
            """cols: (key, header). Empty tables render a placeholder row."""
            head = "".join(f"<th>{esc(h)}</th>" for _, h in cols)
            body = []
            for it in items or []:
                cells = "".join(f"<td>{esc(it.get(k, ''))}</td>" for k, _ in cols)
                body.append(f"<tr>{cells}</tr>")
            if not body:
                body.append(f'<tr><td colspan="{len(cols)}" class="empty">لا توجد بيانات</td></tr>')
            return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

        g = s.get("graph") or {}
        integrity = s.get("archive_integrity") or {}
        ent_counts = s.get("entity_counts") or {}
        timeline = s.get("timeline_preview") or []
        cstats = s.get("collection_stats") or {}
        cfg = s.get("config") or {}
        ethics = cfg.get("ethics") or {}
        problems = integrity.get("problems") or []
        intact = bool(integrity.get("intact"))

        timeline_rows = [
            {
                "date": e.get("date"),
                "event_count": e.get("event_count"),
                "source_count": e.get("source_count"),
                "entities": "، ".join(map(str, e.get("entities") or [])),
            }
            for e in timeline
        ]
        ent_rows = [{"k": k, "v": v} for k, v in ent_counts.items()]
        arts = [{"k": k, "v": v} for k, v in (s.get("artifacts") or {}).items()]

        cards = [
            (s.get("collected", 0), "عنصر مُجمَّع"),
            (s.get("archived", 0), "عنصر مؤرشف"),
            (s.get("entities_total", 0), "كيان"),
            (g.get("nodes", 0), "عقدة في الشبكة"),
            (g.get("edges", 0), "رابط"),
            (g.get("communities", 0), "مجتمع"),
            (s.get("timeline_events", 0), "حدث زمني"),
        ]
        cards_html = "".join(
            f'<div class="card"><div class="n">{esc(n)}</div>{esc(label)}</div>'
            for n, label in cards
        )
        empty_note = ""
        if not s.get("collected"):
            empty_note = '<p class="bad">لم يتم جمع أي عناصر في هذه الجولة.</p>'

        integ_msg = "الأرشيف سليم وغير معدَّل" if intact else "اكتُشف تعديل أو نقص!"
        if not integrity:
            integ_msg = "لم يُنفَّذ التحقق"
        problems_html = ""
        if problems:
            problems_html = table(problems, [("item_id", "المعرّف"), ("issue", "المشكلة"), ("line", "السطر")])

        return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>تقرير التحليل — Arabic OSINT Pipeline</title>
<style>
  body {{ font-family: 'Segoe UI', Tahoma, sans-serif; margin: 2rem auto; max-width: 960px;
          padding: 0 1rem; color: #1a1a1a; background: #fafafa; line-height: 1.6; }}
  h1 {{ border-bottom: 3px solid #2c6e8f; padding-bottom: .4rem; }}
  h2 {{ color: #2c6e8f; margin-top: 2rem; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
  .card {{ flex: 1 1 140px; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
           padding: 1rem; text-align: center; }}
  .card .n {{ font-size: 1.8rem; font-weight: 700; color: #2c6e8f; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; margin-top: .5rem; }}
  th, td {{ border: 1px solid #e0e0e0; padding: .5rem .7rem; text-align: right; }}
  th {{ background: #2c6e8f; color: #fff; }}
  td.empty {{ text-align: center; color: #777; }}
  .ok {{ color: #1a7f37; font-weight: 700; }}
  .bad {{ color: #b3261e; font-weight: 700; }}
  code, .ltr {{ direction: ltr; unicode-bidi: embed; }}
  footer {{ margin-top: 2rem; color: #777; font-size: .85rem; }}
</style>
</head>
<body>
<h1>تقرير جمع وتحليل المصادر المفتوحة</h1>
<p>وقت التوليد: <span class="ltr">{esc(s.get('generated_at', ''))}</span> · محرك الكيانات:
   <b>{esc(s.get('ner_backend', ''))}</b></p>
{empty_note}
<div class="cards">{cards_html}</div>

<h2>سلامة الأرشيف (SHA-256)</h2>
<p>تم التحقق من <b>{esc(integrity.get('checked', 0))}</b> عنصر
  في الأرشيف بأكمله (يشمل جميع الجولات السابقة في نفس المجلد؛ سليم: <b>{esc(integrity.get('ok', 0))}</b>) —
  <span class="{'ok' if intact else 'bad'}">{esc(integ_msg)}</span></p>
{problems_html}

<h2>توزيع الكيانات</h2>
{table(ent_rows, [('k', 'النوع'), ('v', 'العدد')])}

<h2>أبرز الكيانات مركزيةً في الشبكة</h2>
{table(g.get('top_by_centrality') or [], [('entity', 'الكيان'), ('label', 'النوع'), ('degree_centrality', 'مركزية الدرجة')])}

<h2>أكثر الكيانات تأكيداً عبر المصادر (التثليث)</h2>
{table(g.get('top_by_triangulation') or [], [('entity', 'الكيان'), ('label', 'النوع'), ('source_count', 'عدد المصادر'), ('mentions', 'عدد الإشارات')])}

<h2>الخط الزمني (معاينة)</h2>
{table(timeline_rows, [('date', 'التاريخ'), ('event_count', 'عدد الأحداث'), ('source_count', 'عدد المصادر'), ('entities', 'الكيانات')])}

<h2>الجمع والضوابط الأخلاقية</h2>
{table([{'k': k, 'v': v} for k, v in cstats.items()], [('k', 'المؤشر'), ('v', 'القيمة')])}
<p>المصدر: <b>{esc(cfg.get('collector', ''))}</b> · الجمع من المحتوى العام فقط:
   <b>{esc(ethics.get('public_only', ''))}</b> · الحد الأقصى للعناصر:
   <b>{esc(ethics.get('max_items') if ethics.get('max_items') is not None else 'بلا حد')}</b></p>
<p>{esc(ethics.get('note', ''))}</p>

<h2>الملفات الناتجة</h2>
{table(arts, [('k', 'الملف'), ('v', 'المسار')])}

<footer>
  تم إنتاج هذا التقرير آلياً بواسطة Arabic OSINT Collection &amp; Link-Analysis Pipeline.
  جميع البيانات من مصادر متاحة للعموم، مؤرشفة ببصمات SHA-256 لضمان قابلية التتبّع والتحقق.
</footer>
</body>
</html>
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)
