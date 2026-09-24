#!/usr/bin/env python3
"""
PageSpeed Monitor — run_pagespeed.py
Consulta la API de PageSpeed Insights para múltiples URLs,
almacena el histórico en CSV, guarda los diagnósticos/fallos en JSON
y genera un informe HTML interactivo con gráficas y oportunidades de mejora.
"""

import csv
import json
import os
import sys
import time
import math
import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from html import escape

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "urls.json"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
HISTORY_CSV = DATA_DIR / "history.csv"
LATEST_AUDITS_JSON = DATA_DIR / "latest_audits.json"
REPORT_HTML = REPORTS_DIR / "report.html"
INDEX_HTML = ROOT / "index.html"

# Campos del CSV
CSV_FIELDS = [
    "timestamp",
    "label",
    "url",
    "strategy",
    "performance_score",
    "lcp_ms",
    "fcp_ms",
    "cls",
    "tbt_ms",
    "speed_index_ms",
    "tti_ms",
    "opportunities_count",
    "top_opportunity",
    "error",
]

# Umbrales de Google para Core Web Vitals
SCORE_THRESHOLDS = {"good": 90, "needs_improvement": 50}
LCP_THRESHOLDS = {"good": 2500, "needs_improvement": 4000}
CLS_THRESHOLDS = {"good": 0.1, "needs_improvement": 0.25}

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def load_env():
    """Carga variables desde un archivo .env si existe en la raíz."""
    env_file = ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def pagespeed_request(url: str, strategy: str, api_key: str, retries: int = 3) -> dict:
    """Llama a la API de PageSpeed Insights con reintentos y backoff exponencial."""
    params = {"url": url, "strategy": strategy, "category": ["performance"]}
    if api_key:
        params["key"] = api_key

    query_string = urlencode(params, doseq=True)
    endpoint = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?{query_string}"

    for attempt in range(1, retries + 1):
        try:
            req = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "PageSpeedMonitor/1.0"})
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            wait = 2 ** attempt
            print(f"  ⚠ Intento {attempt}/{retries} fallido ({exc}). Esperando {wait}s...")
            if attempt < retries:
                time.sleep(wait)
            else:
                raise


def extract_metrics(data: dict) -> dict:
    """Extrae las métricas numéricas del JSON de Lighthouse."""
    audits = data.get("lighthouseResult", {}).get("audits", {})
    categories = data.get("lighthouseResult", {}).get("categories", {})

    def ms(key):
        v = audits.get(key, {}).get("numericValue")
        return round(v) if v is not None else ""

    def score_pct(key):
        v = categories.get(key, {}).get("score")
        return round(v * 100) if v is not None else ""

    return {
        "performance_score": score_pct("performance"),
        "lcp_ms": ms("largest-contentful-paint"),
        "fcp_ms": ms("first-contentful-paint"),
        "cls": audits.get("cumulative-layout-shift", {}).get("numericValue", ""),
        "tbt_ms": ms("total-blocking-time"),
        "speed_index_ms": ms("speed-index"),
        "tti_ms": ms("interactive"),
        "error": "",
    }


def extract_opportunities(data: dict) -> list:
    """Extrae auditorías suspendidas, oportunidades de ahorro y diagnósticos clave."""
    audits = data.get("lighthouseResult", {}).get("audits", {})
    opportunities = []

    for audit_id, audit in audits.items():
        score = audit.get("score")
        # Si la auditoría pasa completamente (score == 1.0) o es meramente informativa, omitir
        if score is None or score >= 0.9:
            continue

        details = audit.get("details", {})
        details_type = details.get("type", "") if details else ""
        savings_ms = details.get("overallSavingsMs", 0) if details else 0
        savings_bytes = details.get("overallSavingsBytes", 0) if details else 0
        display_val = audit.get("displayValue", "")

        # Formatear el texto de ahorro estimado
        savings_parts = []
        if savings_ms and savings_ms > 0:
            if savings_ms >= 1000:
                savings_parts.append(f"{savings_ms/1000:.2f}s ahorro")
            else:
                savings_parts.append(f"{int(savings_ms)}ms ahorro")

        if savings_bytes and savings_bytes > 0:
            kb = savings_bytes / 1024
            if kb >= 1024:
                savings_parts.append(f"{kb/1024:.1f} MB ahorro")
            else:
                savings_parts.append(f"{int(kb)} KB ahorro")

        savings_label = " · ".join(savings_parts) if savings_parts else display_val

        # Consideramos mejora relevante si tiene ahorro en ms/bytes o displayValue relevante
        if details_type == "opportunity" or savings_ms > 0 or savings_bytes > 0 or (score < 0.5 and display_val):
            raw_desc = audit.get("description", "")
            # Limpiar enlaces markdown del estilo [Más info](https://...)
            clean_desc = raw_desc.split("[")[0].strip()

            opportunities.append({
                "id": audit_id,
                "title": audit.get("title", audit_id),
                "score": score,
                "savings_ms": savings_ms,
                "savings_bytes": savings_bytes,
                "savings_label": savings_label,
                "display_val": display_val,
                "description": clean_desc,
            })

    # Ordenar por mayor ahorro en tiempo, luego en bytes, y luego por menor puntuación
    opportunities.sort(key=lambda x: (x["savings_ms"] or 0, x["savings_bytes"] or 0, 1 - (x["score"] or 0)), reverse=True)
    return opportunities


def ensure_csv_header():
    DATA_DIR.mkdir(exist_ok=True)
    if not HISTORY_CSV.exists():
        with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
    else:
        with open(HISTORY_CSV, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if lines:
            header = lines[0].strip().split(",")
            if header != CSV_FIELDS:
                lines[0] = ",".join(CSV_FIELDS) + "\n"
                with open(HISTORY_CSV, "w", encoding="utf-8") as f:
                    f.writelines(lines)


def append_row(row: dict):
    # Asegurar que todos los campos existan en el dict
    for k in CSV_FIELDS:
        row.setdefault(k, "")
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(row)


def read_history() -> list:
    if not HISTORY_CSV.exists():
        return []
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_latest_audits() -> dict:
    if not LATEST_AUDITS_JSON.exists():
        return {}
    try:
        with open(LATEST_AUDITS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_latest_audits(audits_data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(LATEST_AUDITS_JSON, "w", encoding="utf-8") as f:
        json.dump(audits_data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Generación del informe HTML
# ---------------------------------------------------------------------------

def score_color(score):
    try:
        s = int(score)
    except (ValueError, TypeError):
        return "#888"
    if s >= SCORE_THRESHOLDS["good"]:
        return "#0cce6b"
    if s >= SCORE_THRESHOLDS["needs_improvement"]:
        return "#ffa400"
    return "#ff4e42"


def metric_color_lcp(val):
    try:
        v = int(val)
    except (ValueError, TypeError):
        return "#888"
    if v <= LCP_THRESHOLDS["good"]:
        return "#0cce6b"
    if v <= LCP_THRESHOLDS["needs_improvement"]:
        return "#ffa400"
    return "#ff4e42"


def metric_color_cls(val):
    try:
        v = float(val)
    except (ValueError, TypeError):
        return "#888"
    if v <= CLS_THRESHOLDS["good"]:
        return "#0cce6b"
    if v <= CLS_THRESHOLDS["needs_improvement"]:
        return "#ffa400"
    return "#ff4e42"


def delta_html(current, previous, lower_is_better=False, is_time_ms=False, is_cls=False):
    try:
        c = float(current)
        p = float(previous)
    except (ValueError, TypeError):
        return ""
    diff = c - p
    if is_cls:
        if abs(diff) < 0.001:
            return '<span style="color:#aaa">—</span>'
    elif abs(diff) < 0.01:
        return '<span style="color:#aaa">—</span>'

    if lower_is_better:
        good = diff < 0
    else:
        good = diff > 0
    color = "#0cce6b" if good else "#ff4e42"
    sign = "+" if diff > 0 else ""
    arrow = "▲" if diff > 0 else "▼"

    if is_time_ms:
        display = f"{sign}{diff/1000:.2f}s"
    elif is_cls:
        display = f"{sign}{diff:.3f}"
    elif isinstance(c, float) and not c.is_integer():
        display = f"{sign}{diff:.1f}"
    else:
        display = f"{sign}{int(diff)}"

    return f'<span style="color:{color};font-size:0.85em">{display} {arrow}</span>'


def build_chart_datasets(history: list, label: str, strategies: list) -> tuple:
    relevant_rows = [r for r in history if r.get("label") == label and r.get("performance_score")]
    all_timestamps = sorted(list(set(r["timestamp"] for r in relevant_rows if r.get("timestamp"))))

    # Si hay múltiples mediciones en un mismo día, incluir la hora
    unique_dates = set(t[:10] for t in all_timestamps)
    has_multiple_in_day = len(unique_dates) < len(all_timestamps)

    if has_multiple_in_day:
        formatted_labels = [t[:16].replace("T", " ") for t in all_timestamps]
    else:
        formatted_labels = [t[:10] for t in all_timestamps]

    datasets_by_strat = {}
    for strat in strategies:
        strat_rows = {
            r["timestamp"]: r["performance_score"]
            for r in history
            if r.get("label") == label and r.get("strategy") == strat and r.get("performance_score")
        }
        scores = []
        for ts in all_timestamps:
            val = strat_rows.get(ts)
            try:
                scores.append(int(val) if val is not None else None)
            except (ValueError, TypeError):
                scores.append(None)
        datasets_by_strat[strat] = scores

    return formatted_labels, datasets_by_strat


def generate_html(history: list, config: dict, latest_audits: dict):
    REPORTS_DIR.mkdir(exist_ok=True)

    # Últimas dos mediciones por (label, url, strategy)
    last_two = {}
    for row in history:
        key = (row.get("label"), row.get("url"), row.get("strategy"))
        last_two.setdefault(key, [])
        last_two[key].append(row)

    strategies = config.get("strategies", ["mobile", "desktop"])

    # Filas de la tabla resumen
    summary_rows_html = []
    opportunities_cards_html = []

    for entry in config.get("urls", []):
        lbl = entry.get("label", entry["url"])
        url = entry["url"]
        for strat in strategies:
            key = (lbl, url, strat)
            rows = last_two.get(key, [])
            current = rows[-1] if rows else None
            previous = rows[-2] if len(rows) >= 2 else None

            if current is None:
                continue

            score = current.get("performance_score") or ""
            lcp = current.get("lcp_ms") or ""
            cls_v = current.get("cls") or ""
            fcp = current.get("fcp_ms") or ""
            tbt = current.get("tbt_ms") or ""
            err = current.get("error") or ""

            try:
                cls_display = f"{float(cls_v):.3f}" if cls_v else "—"
            except (ValueError, TypeError):
                cls_display = cls_v or "—"

            score_c = score_color(score) if score else "#888"
            lcp_c = metric_color_lcp(lcp) if lcp else "inherit"
            cls_c = metric_color_cls(cls_v) if cls_v else "inherit"

            d_score = delta_html(score, previous.get("performance_score") if previous else None) if score else ""
            d_lcp = delta_html(lcp, previous.get("lcp_ms") if previous else None, lower_is_better=True, is_time_ms=True) if lcp else ""
            d_cls = delta_html(cls_v, previous.get("cls") if previous else None, lower_is_better=True, is_cls=True) if cls_v else ""

            strat_badge = (
                '<span class="badge badge-mobile">📱 Mobile</span>'
                if strat == "mobile"
                else '<span class="badge badge-desktop">🖥 Desktop</span>'
            )

            lcp_s = f"{int(lcp)/1000:.2f}s" if lcp else "—"
            fcp_s = f"{int(fcp)/1000:.2f}s" if fcp else "—"
            tbt_s = f"{int(tbt)}ms" if tbt else "—"
            ts = current.get("timestamp", "")[:16].replace("T", " ")

            if score:
                score_html = f'<span class="score-badge" style="background:{score_c}">{score}</span> {d_score}'
            elif err:
                score_html = f'<span class="badge badge-error" title="{escape(err)}">⚠️ Error API</span>'
            else:
                score_html = '<span style="color:var(--muted)">—</span>'

            # Obtener oportunidades de mejora de esta URL/estrategia
            audit_key = f"{url}::{strat}"
            audit_info = latest_audits.get(audit_key, {})
            opps = audit_info.get("opportunities", [])
            opps_count = len(opps)

            if opps_count > 0:
                opps_badge = f'<span class="badge badge-warning">🛠️ {opps_count} mejoras</span>'
            elif score:
                opps_badge = '<span class="badge badge-success">✨ Óptimo</span>'
            else:
                opps_badge = '<span style="color:var(--muted)">—</span>'

            summary_rows_html.append(f"""
            <tr>
              <td><strong>{escape(lbl)}</strong><br><small class="url-cell"><a href="{escape(url)}" target="_blank">{escape(url)}</a></small></td>
              <td>{strat_badge}</td>
              <td>{score_html}</td>
              <td style="color:{lcp_c}">{lcp_s} {d_lcp}</td>
              <td style="color:{cls_c}">{cls_display} {d_cls}</td>
              <td>{fcp_s}</td>
              <td>{tbt_s}</td>
              <td>{opps_badge}</td>
              <td class="ts-cell">{ts}</td>
            </tr>""")

            # Generar tarjeta de diagnóstico de oportunidades
            if opps:
                items_html = []
                for o in opps[:5]:  # Top 5 mejoras
                    sav = escape(o.get("savings_label") or "")
                    sav_badge = f'<span class="opp-savings">{sav}</span>' if sav else ""
                    items_html.append(f"""
                    <li class="opp-item">
                      <div class="opp-item-header">
                        <span class="opp-title">⚠️ {escape(o.get("title", ""))}</span>
                        {sav_badge}
                      </div>
                      <p class="opp-desc">{escape(o.get("description", ""))}</p>
                    </li>""")

                opportunities_cards_html.append(f"""
                <div class="opp-card">
                  <div class="opp-card-header">
                    <div>
                      <strong>{escape(lbl)}</strong> ({strat_badge})
                      <br><small class="url-cell"><a href="{escape(url)}" target="_blank">{escape(url)}</a></small>
                    </div>
                    <span class="score-badge" style="background:{score_c}">{score}</span>
                  </div>
                  <ul class="opp-list">
                    {"".join(items_html)}
                  </ul>
                </div>""")

    # Preparar gráficas Chart.js
    charts_js = []
    chart_canvases = []
    chart_idx = 0
    for entry in config.get("urls", []):
        lbl = entry.get("label", entry["url"])
        url = entry["url"]
        chart_idx += 1
        canvas_id = f"chart_{chart_idx}"
        datasets = []
        colors = {"mobile": "#a78bfa", "desktop": "#34d399"}

        formatted_labels, datasets_by_strat = build_chart_datasets(history, lbl, strategies)

        for strat in strategies:
            scores = datasets_by_strat.get(strat, [])
            color = colors.get(strat, "#60a5fa")
            datasets.append(f"""{{
                label: '{strat.capitalize()}',
                data: {json.dumps(scores)},
                borderColor: '{color}',
                backgroundColor: '{color}22',
                fill: true,
                tension: 0.4,
                pointRadius: 4,
                pointHoverRadius: 7,
                spanGaps: true
            }}""")

        chart_canvases.append(f"""
        <div class="chart-card">
          <h3>📊 {escape(lbl)} — <small>{escape(url)}</small></h3>
          <canvas id="{canvas_id}"></canvas>
        </div>""")

        charts_js.append(f"""
        new Chart(document.getElementById('{canvas_id}'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(formatted_labels)},
                datasets: [{", ".join(datasets)}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ labels: {{ color: '#e2e8f0' }} }},
                    tooltip: {{ mode: 'index', intersect: false }}
                }},
                scales: {{
                    x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
                    y: {{
                        min: 0, max: 100,
                        ticks: {{ color: '#94a3b8' }},
                        grid: {{ color: '#334155' }},
                        title: {{ display: true, text: 'Score', color: '#94a3b8' }}
                    }}
                }}
            }}
        }});""")

    # Histórico completo
    history_rows_html = []
    for row in reversed(history[-200:]):
        strat = row.get("strategy", "")
        badge = (
            '<span class="badge badge-mobile">📱 Mobile</span>'
            if strat == "mobile"
            else '<span class="badge badge-desktop">🖥 Desktop</span>'
        )
        score = row.get("performance_score", "—")
        score_c = score_color(score)
        try:
            cls_display = f"{float(row.get('cls', '')):.3f}"
        except (ValueError, TypeError):
            cls_display = row.get("cls", "—")

        def fmt_ms(v):
            try:
                return f"{int(v)/1000:.2f}s"
            except (ValueError, TypeError):
                return "—"

        top_opp = row.get("top_opportunity", "")
        top_opp_html = f'<small class="url-cell" title="{escape(top_opp)}">{escape(top_opp[:38]) + "..." if len(top_opp) > 38 else escape(top_opp)}</small>' if top_opp else "—"

        history_rows_html.append(f"""
        <tr>
          <td class="ts-cell">{row.get('timestamp','')[:16].replace('T',' ')}</td>
          <td>{escape(row.get('label',''))}</td>
          <td>{badge}</td>
          <td><span class="score-badge" style="background:{score_c}">{score}</span></td>
          <td>{fmt_ms(row.get('lcp_ms'))}</td>
          <td style="color:{metric_color_cls(row.get('cls',''))}">{cls_display}</td>
          <td>{fmt_ms(row.get('fcp_ms'))}</td>
          <td>{fmt_ms(row.get('tbt_ms'))}</td>
          <td>{top_opp_html}</td>
        </tr>""")

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_runs = len(set(r.get("timestamp","")[:16] for r in history))
    total_urls = len(config.get("urls", []))

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PageSpeed Monitor — Filux</title>
<meta name="description" content="Monitorización periódica de Core Web Vitals, rendimiento y oportunidades de mejora para Filux">
<style>
  :root {{
    --bg: #0f172a;
    --surface: #1e293b;
    --surface2: #263247;
    --border: #334155;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #818cf8;
    --green: #0cce6b;
    --yellow: #ffa400;
    --red: #ff4e42;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}

  /* Layout */
  .container {{ max-width: 1300px; margin: 0 auto; padding: 0 1.5rem; }}
  header {{ background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%); border-bottom: 1px solid var(--border); padding: 2rem 0; }}
  header .container {{ display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; }}
  header h1 {{ font-size: 1.8rem; font-weight: 700; background: linear-gradient(90deg, #a78bfa, #34d399); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
  header .meta {{ color: var(--muted); font-size: 0.9rem; margin-left: auto; text-align: right; }}

  /* Stats */
  .stats-bar {{ display: flex; gap: 1rem; padding: 1.5rem 0; flex-wrap: wrap; }}
  .stat-card {{ flex: 1; min-width: 130px; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.25rem; }}
  .stat-card .value {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
  .stat-card .label {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.2rem; }}

  /* Section */
  section {{ padding: 2rem 0; }}
  section h2 {{ font-size: 1.3rem; font-weight: 600; margin-bottom: 1.25rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem; }}

  /* Table */
  .table-wrapper {{ overflow-x: auto; border-radius: 12px; border: 1px solid var(--border); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ background: var(--surface2); color: var(--muted); font-weight: 500; text-align: left; padding: 0.75rem 1rem; white-space: nowrap; }}
  td {{ padding: 0.75rem 1rem; border-top: 1px solid var(--border); vertical-align: middle; }}
  tr:hover td {{ background: var(--surface2); }}
  .url-cell {{ color: var(--muted); font-size: 0.8rem; }}
  .ts-cell {{ color: var(--muted); font-size: 0.82rem; white-space: nowrap; }}

  /* Badges */
  .score-badge {{ display: inline-block; padding: 0.25rem 0.65rem; border-radius: 99px; color: #0f172a; font-weight: 700; font-size: 0.95rem; min-width: 42px; text-align: center; }}
  .badge {{ display: inline-block; padding: 0.2rem 0.55rem; border-radius: 6px; font-size: 0.78rem; font-weight: 600; }}
  .badge-mobile {{ background: #3730a333; color: #a78bfa; border: 1px solid #a78bfa44; }}
  .badge-desktop {{ background: #06643033; color: #34d399; border: 1px solid #34d39944; }}
  .badge-warning {{ background: #ffa40022; color: #ffa400; border: 1px solid #ffa40044; }}
  .badge-success {{ background: #0cce6b22; color: #0cce6b; border: 1px solid #0cce6b44; }}
  .badge-error {{ background: #ff4e4222; color: #ff4e42; border: 1px solid #ff4e4244; }}

  /* Oportunidades & Diagnósticos */
  .opps-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 1.25rem; }}
  .opp-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem; display: flex; flex-direction: column; gap: 0.75rem; }}
  .opp-card-header {{ display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 0.75rem; border-bottom: 1px solid var(--border); }}
  .opp-list {{ list-style: none; display: flex; flex-direction: column; gap: 0.75rem; }}
  .opp-item {{ background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem; }}
  .opp-item-header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; margin-bottom: 0.25rem; }}
  .opp-title {{ font-weight: 600; font-size: 0.88rem; color: #f8fafc; }}
  .opp-savings {{ font-size: 0.78rem; font-weight: 600; background: #ffa40026; color: #ffa400; padding: 0.15rem 0.45rem; border-radius: 4px; white-space: nowrap; }}
  .opp-desc {{ font-size: 0.78rem; color: var(--muted); line-height: 1.4; }}

  /* Charts */
  .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr)); gap: 1.25rem; }}
  .chart-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem; }}
  .chart-card h3 {{ font-size: 0.95rem; color: var(--text); margin-bottom: 1rem; }}
  .chart-card h3 small {{ color: var(--muted); font-weight: 400; }}

  /* Legend */
  .legend-bar {{ display: flex; gap: 1.5rem; margin-bottom: 1rem; font-size: 0.82rem; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 0.4rem; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; }}

  /* Footer */
  footer {{ border-top: 1px solid var(--border); padding: 1.5rem 0; text-align: center; color: var(--muted); font-size: 0.82rem; }}
</style>
</head>
<body>

<header>
  <div class="container">
    <div>
      <h1>⚡ PageSpeed Monitor — Filux</h1>
      <p style="color:var(--muted);font-size:0.9rem;margin-top:0.25rem">Monitorización de Core Web Vitals, comparativas y oportunidades de mejora</p>
    </div>
    <div class="meta">
      Generado el <strong>{now_str}</strong><br>
      Fuente: <a href="https://developers.google.com/speed/docs/insights/v5/about" target="_blank">PageSpeed Insights API v5</a>
    </div>
  </div>
</header>

<div class="container">

  <!-- Stats bar -->
  <div class="stats-bar">
    <div class="stat-card">
      <div class="value">{total_urls}</div>
      <div class="label">URLs monitorizadas</div>
    </div>
    <div class="stat-card">
      <div class="value">{len(strategies)}</div>
      <div class="label">Estrategias (Mobile / Desktop)</div>
    </div>
    <div class="stat-card">
      <div class="value">{total_runs}</div>
      <div class="label">Ejecuciones registradas</div>
    </div>
    <div class="stat-card">
      <div class="value">{len(history)}</div>
      <div class="label">Mediciones totales</div>
    </div>
  </div>

  <!-- Resumen actual -->
  <section>
    <h2>📋 Última medición y estado</h2>
    <div class="legend-bar">
      <div class="legend-item"><div class="dot" style="background:#0cce6b"></div> Bueno (≥90 / LCP ≤2.5s / CLS ≤0.1)</div>
      <div class="legend-item"><div class="dot" style="background:#ffa400"></div> Necesita mejora</div>
      <div class="legend-item"><div class="dot" style="background:#ff4e42"></div> Malo</div>
    </div>
    <div class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Página</th>
            <th>Dispositivo</th>
            <th>Score</th>
            <th>LCP</th>
            <th>CLS</th>
            <th>FCP</th>
            <th>TBT</th>
            <th>Mejoras</th>
            <th>Medido</th>
          </tr>
        </thead>
        <tbody>
          {"".join(summary_rows_html) or '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:2rem">Sin datos todavía. Ejecuta el script para obtener la primera medición.</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

  <!-- Oportunidades y Diagnósticos de Lighthouse -->
  {"<section><h2>🛠️ Oportunidades de Mejora y Diagnósticos (Lighthouse)</h2><p style='color:var(--muted);margin-bottom:1.25rem;font-size:0.9rem;'>Principales factores técnicos detectados que ralentizan la carga, con el ahorro estimado en tiempo y peso:</p><div class='opps-grid'>" + "".join(opportunities_cards_html) + "</div></section>" if opportunities_cards_html else ""}

  <!-- Gráficas de evolución -->
  <section>
    <h2>📈 Evolución del Performance Score</h2>
    {"<div class='charts-grid'>" + "".join(chart_canvases) + "</div>" if chart_canvases else '<p style="color:var(--muted)">Las gráficas aparecerán tras registrar mediciones periódicas.</p>'}
  </section>

  <!-- Histórico completo -->
  <section>
    <h2>🗂 Histórico acumulado <span style="font-size:0.8rem;color:var(--muted);font-weight:400">(últimas 200 mediciones)</span></h2>
    <div class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Página</th>
            <th>Dispositivo</th>
            <th>Score</th>
            <th>LCP</th>
            <th>CLS</th>
            <th>FCP</th>
            <th>TBT</th>
            <th>Principal mejora</th>
          </tr>
        </thead>
        <tbody>
          {"".join(history_rows_html) or '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:2rem">Sin datos todavía.</td></tr>'}
        </tbody>
      </table>
    </div>
  </section>

</div>

<footer>
  <div class="container">
    PageSpeed Monitor · Filux · Datos procesados con Google PageSpeed Insights & Lighthouse
  </div>
</footer>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
{chr(10).join(charts_js)}
</script>

</body>
</html>
"""

    with open(REPORT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✅ Informe generado en {REPORT_HTML}")
    print(f"  ✅ Informe publicado en la raíz: {INDEX_HTML} (para GitHub Pages)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("🚀 PageSpeed Monitor — Filux")
    print("=" * 55)

    load_env()
    config = load_config()
    api_key = os.environ.get("PAGESPEED_API_KEY") or config.get("api_key", "")
    urls = config.get("urls", [])
    strategies = config.get("strategies", ["mobile", "desktop"])

    if not urls:
        print("❌ No hay URLs configuradas en config/urls.json")
        sys.exit(1)

    ensure_csv_header()
    latest_audits = load_latest_audits()
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for entry in urls:
        label = entry.get("label", entry["url"])
        url = entry["url"]
        for strategy in strategies:
            print(f"\n🔍 [{strategy.upper()}] {label}\n   {url}")
            row = {
                "timestamp": timestamp,
                "label": label,
                "url": url,
                "strategy": strategy,
                "performance_score": "",
                "lcp_ms": "",
                "fcp_ms": "",
                "cls": "",
                "tbt_ms": "",
                "speed_index_ms": "",
                "tti_ms": "",
                "opportunities_count": 0,
                "top_opportunity": "",
                "error": "",
            }
            try:
                data = pagespeed_request(url, strategy, api_key)
                metrics = extract_metrics(data)
                row.update(metrics)

                # Extraer oportunidades de mejora y diagnósticos
                opps = extract_opportunities(data)
                row["opportunities_count"] = len(opps)
                if opps:
                    row["top_opportunity"] = f"{opps[0]['title']} ({opps[0].get('savings_label', '')})"

                # Guardar en latest_audits
                audit_key = f"{url}::{strategy}"
                latest_audits[audit_key] = {
                    "timestamp": timestamp,
                    "label": label,
                    "url": url,
                    "strategy": strategy,
                    "performance_score": metrics.get("performance_score"),
                    "opportunities": opps
                }

                score = metrics.get("performance_score", "?")
                lcp = metrics.get("lcp_ms", "?")
                cls_v = metrics.get("cls", "?")
                print(f"  Score: {score}  |  LCP: {lcp}ms  |  CLS: {cls_v}  |  Mejoras detectadas: {len(opps)}")
                if opps:
                    print(f"  👉 Principal mejora: {opps[0]['title']} ({opps[0].get('savings_label','')})")

            except Exception as exc:
                row["error"] = str(exc)
                print(f"  ❌ Error: {exc}")

            append_row(row)
            # Espera prudencial entre peticiones para respetar la cuota de la API
            time.sleep(2)

    # Persistir auditorías detalladas en JSON
    save_latest_audits(latest_audits)
    print(f"\n💾 Diagnósticos guardados en {LATEST_AUDITS_JSON}")

    # Generar reporte HTML
    print(f"📊 Generando informe HTML...")
    history = read_history()
    generate_html(history, config, latest_audits)

    print(f"\n✅ Completado con éxito.")
    print(f"   Histórico CSV:  {HISTORY_CSV}")
    print(f"   Auditorías JSON: {LATEST_AUDITS_JSON}")
    print(f"   Informe HTML:   {REPORT_HTML}")
    print(f"   GitHub Pages:   {INDEX_HTML}")


if __name__ == "__main__":
    main()
