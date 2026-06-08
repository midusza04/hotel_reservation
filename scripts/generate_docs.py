"""Generate project documentation artifacts.

The script intentionally uses only the Python standard library.  It creates:

* docs/pydoc/index.html - a tabbed pydoc-style documentation browser.
* docs/raport_hotel_reservation.docx - a Word report describing the project.
"""

from __future__ import annotations

import ast
import html
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
DOCS_DIR = PROJECT_ROOT / "docs"
PYDOC_DIR = DOCS_DIR / "pydoc"
DIAGRAMS_DIR = DOCS_DIR / "diagrams"
REPORT_PATH = DOCS_DIR / "raport_hotel_reservation.docx"
DETAILED_REPORT_PATH = DOCS_DIR / "raport_hotel_reservation_szczegolowy.docx"

DIAGRAMS = [
    {"id": "architecture", "file": "architecture.svg", "title": "Diagram architektury systemu", "width": 1280, "height": 672},
    {"id": "actors", "file": "actors.svg", "title": "Diagram relacji aktorow Ray", "width": 1000, "height": 590},
    {"id": "booking_flow", "file": "booking_flow.svg", "title": "Diagram przeplywu rezerwacji", "width": 950, "height": 814},
    {"id": "cancel_flow", "file": "cancel_flow.svg", "title": "Diagram przeplywu anulowania", "width": 950, "height": 806},
    {"id": "data_model", "file": "data_model.svg", "title": "Diagram modelu danych", "width": 1040, "height": 430},
    {"id": "events", "file": "events.svg", "title": "Diagram zdarzen audytowych", "width": 1200, "height": 560},
]


def _read_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
    for arg, default in zip(node.args.args, defaults):
        item = arg.arg
        if arg.annotation is not None:
            item += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            item += f" = {ast.unparse(default)}"
        args.append(item)
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        item = arg.arg
        if arg.annotation is not None:
            item += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            item += f" = {ast.unparse(default)}"
        args.append(item)
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    result = f"{node.name}({', '.join(args)})"
    if node.returns is not None:
        result += f" -> {ast.unparse(node.returns)}"
    return result


def _doc(value: str | None) -> str:
    return html.escape(textwrap.dedent(value or "Brak docstringa.").strip()).replace("\n", "<br>")


def _anchor(*parts: str) -> str:
    return "-".join(part.replace("_", "-").lower() for part in parts)


def _module_summary(path: Path) -> dict:
    module = _read_module(path)
    module_name = path.stem
    functions = [n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in module.body if isinstance(n, ast.ClassDef)]
    return {
        "name": module_name,
        "doc": ast.get_docstring(module),
        "functions": functions,
        "classes": classes,
    }


def _module_nav(summary: dict) -> str:
    module_name = summary["name"]
    rows = [f"<strong>{html.escape(module_name)}</strong>"]
    if summary["functions"]:
        rows.append("<span>Funkcje</span>")
        for fn in summary["functions"]:
            rows.append(
                f"<a href='#{_anchor(module_name, fn.name)}' data-module='{html.escape(module_name)}'>"
                f"{html.escape(fn.name)}</a>"
            )
    if summary["classes"]:
        rows.append("<span>Klasy i metody</span>")
        for cls in summary["classes"]:
            rows.append(
                f"<a href='#{_anchor(module_name, cls.name)}' data-module='{html.escape(module_name)}'>"
                f"{html.escape(cls.name)}</a>"
            )
            methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for method in methods:
                rows.append(
                    f"<a class='method-link' href='#{_anchor(module_name, cls.name, method.name)}' "
                    f"data-module='{html.escape(module_name)}'>{html.escape(method.name)}</a>"
                )
    return "\n".join(rows)


def _module_panel(summary: dict, active: bool = False) -> str:
    module_name = summary["name"]
    rows = [
        f"<section class='module-panel {'active' if active else ''}' id='module-{html.escape(module_name)}'>",
        f"<div class='module-hero'><p class='eyebrow'>Modul</p><h2>{html.escape(module_name)}</h2>",
        f"<p>{_doc(summary['doc'])}</p></div>",
    ]

    if summary["functions"]:
        rows.append("<h3>Funkcje modulu</h3>")
        for fn in summary["functions"]:
            rows.append(f"<article class='doc-card' id='{_anchor(module_name, fn.name)}'>")
            rows.append("<div class='kind'>funkcja</div>")
            rows.append(f"<h4>{html.escape(fn.name)}</h4>")
            rows.append(f"<pre><code>{html.escape(_signature(fn))}</code></pre>")
            rows.append(f"<p>{_doc(ast.get_docstring(fn))}</p>")
            rows.append("</article>")

    if summary["classes"]:
        rows.append("<h3>Klasy</h3>")
        for cls in summary["classes"]:
            rows.append(f"<article class='doc-card class-card' id='{_anchor(module_name, cls.name)}'>")
            rows.append("<div class='kind'>klasa</div>")
            rows.append(f"<h4>{html.escape(cls.name)}</h4>")
            rows.append(f"<p>{_doc(ast.get_docstring(cls))}</p>")
            methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if methods:
                rows.append("<div class='methods'>")
                for method in methods:
                    rows.append(f"<section class='method' id='{_anchor(module_name, cls.name, method.name)}'>")
                    rows.append(f"<h5>{html.escape(method.name)}</h5>")
                    rows.append(f"<pre><code>{html.escape(_signature(method))}</code></pre>")
                    rows.append(f"<p>{_doc(ast.get_docstring(method))}</p>")
                    rows.append("</section>")
                rows.append("</div>")
            rows.append("</article>")

    rows.append("</section>")
    return "\n".join(rows)


def generate_pydoc_html() -> None:
    """Generate a single-page pydoc-style HTML browser for application modules."""
    PYDOC_DIR.mkdir(parents=True, exist_ok=True)
    for old_page in PYDOC_DIR.glob("*.html"):
        old_page.unlink()

    modules = [_module_summary(path) for path in sorted(APP_DIR.glob("*.py"))]
    tabs = []
    navs = []
    panels = []
    for index, module in enumerate(modules):
        active = index == 0
        active_class = " active" if active else ""
        module_name = module["name"]
        tabs.append(
            f"<button class='tab{active_class}' type='button' data-module='{html.escape(module_name)}'>"
            f"{html.escape(module_name)}</button>"
        )
        navs.append(f"<nav class='module-nav{active_class}' data-module='{html.escape(module_name)}'>{_module_nav(module)}</nav>")
        panels.append(_module_panel(module, active=active))

    index = [
        "<!doctype html>",
        "<html lang='pl'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Ray Hotel - pydoc</title>",
        "<style>",
        ":root{--bg:#f8fafc;--panel:#fff;--ink:#172033;--muted:#64748b;--line:#dbe3ef;--brand:#2563eb;--soft:#eff6ff}",
        "*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI,Arial,sans-serif;line-height:1.55}",
        "header{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.94);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:1rem 1.5rem}",
        "h1{margin:0;font-size:1.55rem}header p{margin:.25rem 0 1rem;color:var(--muted)}",
        ".tabs{display:flex;gap:.5rem;flex-wrap:wrap}.tab{border:1px solid var(--line);background:#fff;color:var(--ink);padding:.55rem .9rem;border-radius:999px;cursor:pointer;font-weight:650}",
        ".tab.active,.tab:hover{background:var(--brand);border-color:var(--brand);color:#fff}",
        ".layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:1.25rem;max-width:1280px;margin:0 auto;padding:1.25rem}",
        "aside{position:sticky;top:118px;align-self:start;max-height:calc(100vh - 140px);overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:1rem}",
        ".module-nav{display:none}.module-nav.active{display:grid;gap:.25rem}.module-nav strong{font-size:1.05rem;margin-bottom:.45rem}.module-nav span{margin-top:.7rem;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:800}",
        ".module-nav a{display:block;text-decoration:none;color:var(--ink);padding:.35rem .45rem;border-radius:8px}.module-nav a:hover{background:var(--soft);color:var(--brand)}.method-link{padding-left:1.25rem!important;color:var(--muted)!important}",
        "main{min-width:0}.module-panel{display:none}.module-panel.active{display:block}.module-hero{background:linear-gradient(135deg,#eff6ff,#fff);border:1px solid var(--line);border-radius:18px;padding:1.2rem;margin-bottom:1rem}",
        ".eyebrow{margin:0;color:var(--brand);font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;font-weight:800}.module-hero h2{margin:.1rem 0 .5rem;font-size:2rem}.module-hero p{color:var(--muted)}",
        "h3{margin:1.4rem 0 .75rem}.doc-card{scroll-margin-top:132px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:1rem 1.1rem;margin:.9rem 0;box-shadow:0 1px 2px rgba(15,23,42,.04)}",
        ".doc-card:target,.method:target{outline:2px solid var(--brand);background:#f8fbff}.kind{display:inline-block;color:var(--brand);background:var(--soft);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;font-weight:800;border-radius:999px;padding:.18rem .5rem}",
        "h4{margin:.5rem 0;font-size:1.25rem}h5{margin:.9rem 0 .35rem;font-size:1rem}pre{overflow:auto;background:#0f172a;color:#e2e8f0;border-radius:12px;padding:.8rem}code{font-family:Cascadia Code,Consolas,monospace;font-size:.9rem}",
        ".methods{border-top:1px solid var(--line);margin-top:1rem;padding-top:.25rem}.method{scroll-margin-top:132px;border-left:3px solid var(--line);padding-left:.8rem;margin:.7rem 0}",
        "@media(max-width:850px){.layout{grid-template-columns:1fr}aside{position:static;max-height:none}header{position:static}.doc-card,.method{scroll-margin-top:16px}}",
        "</style>",
        "</head>",
        "<body>",
        "<header>",
        "<h1>Ray Hotel - dokumentacja pydoc</h1>",
        "<p>Jedna strona z zakladkami modulow oraz przewijaniem do funkcji, klas i metod.</p>",
        f"<div class='tabs'>{''.join(tabs)}</div>",
        "</header>",
        "<div class='layout'>",
        f"<aside>{''.join(navs)}</aside>",
        f"<main>{''.join(panels)}</main>",
        "</div>",
        "<script>",
        "function showModule(name){",
        "document.querySelectorAll('.tab').forEach(function(el){el.classList.toggle('active',el.dataset.module===name);});",
        "document.querySelectorAll('.module-panel').forEach(function(el){el.classList.toggle('active',el.id==='module-'+name);});",
        "document.querySelectorAll('.module-nav').forEach(function(el){el.classList.toggle('active',el.dataset.module===name);});",
        "}",
        "document.querySelectorAll('.tab').forEach(function(tab){tab.addEventListener('click',function(){showModule(tab.dataset.module);history.replaceState(null,'','#module-'+tab.dataset.module);window.scrollTo({top:0,behavior:'smooth'});});});",
        "document.querySelectorAll('aside a').forEach(function(link){link.addEventListener('click',function(){showModule(link.dataset.module);});});",
        "if(location.hash){var target=document.getElementById(location.hash.slice(1));if(target){var panel=target.closest('.module-panel');if(panel){showModule(panel.id.replace('module-',''));setTimeout(function(){target.scrollIntoView();},0);}}}",
        "</script>",
        "</body>",
        "</html>",
    ]
    (PYDOC_DIR / "index.html").write_text("\n".join(index), encoding="utf-8")


def _paragraph(text: str = "", style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    runs = []
    for line_no, line in enumerate(text.split("\n")):
        if line_no:
            runs.append("<w:r><w:br/></w:r>")
        runs.append(f"<w:r><w:t xml:space=\"preserve\">{escape(line)}</w:t></w:r>")
    return f"<w:p>{style_xml}{''.join(runs)}</w:p>"


def _bullet(text: str) -> str:
    return _paragraph("• " + text)


def _code_block(text: str) -> str:
    return _paragraph(textwrap.dedent(text).strip("\n"), "CodeBlock")


def _figure(diagram_id: str) -> str:
    diagram = next(item for item in DIAGRAMS if item["id"] == diagram_id)
    rel_id = f"rId_{diagram_id}"
    doc_pr_id = DIAGRAMS.index(diagram) + 1
    width_emu = 6_300_000
    height_emu = int(width_emu * diagram["height"] / diagram["width"])
    title = escape(diagram["title"])
    return f"""
<w:p>
  <w:r>
    <w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="{width_emu}" cy="{height_emu}"/>
        <wp:effectExtent l="0" t="0" r="0" b="0"/>
        <wp:docPr id="{doc_pr_id}" name="{title}"/>
        <wp:cNvGraphicFramePr>
          <a:graphicFrameLocks noChangeAspect="1"/>
        </wp:cNvGraphicFramePr>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic>
              <pic:nvPicPr>
                <pic:cNvPr id="{doc_pr_id}" name="{title}"/>
                <pic:cNvPicPr/>
              </pic:nvPicPr>
              <pic:blipFill>
                <a:blip r:embed="{rel_id}"/>
                <a:stretch><a:fillRect/></a:stretch>
              </pic:blipFill>
              <pic:spPr>
                <a:xfrm>
                  <a:off x="0" y="0"/>
                  <a:ext cx="{width_emu}" cy="{height_emu}"/>
                </a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
              </pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>
{_paragraph(diagram["title"], "Caption")}
"""


def _table(rows: list[list[str]]) -> str:
    xml = ["<w:tbl><w:tblPr><w:tblStyle w:val=\"TableGrid\"/><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>"]
    for row in rows:
        xml.append("<w:tr>")
        for cell in row:
            xml.append(f"<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>{_paragraph(cell)}</w:tc>")
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


# ─────────────────────────────────────────────────────────────────────────────
# SVG diagram helpers
# ─────────────────────────────────────────────────────────────────────────────

def _svg_base(width: int, height: int, title: str, content: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/>
  </marker>
  <filter id="shadow" x="-8%" y="-8%" width="120%" height="130%">
    <feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.10"/>
  </filter>
</defs>
<rect width="100%" height="100%" rx="20" fill="#f8fafc"/>
<text x="40" y="50" font-family="Segoe UI, Arial" font-size="26" font-weight="700" fill="#0f172a">{escape(title)}</text>
{content}
</svg>"""


def _b(x: int, y: int, w: int, h: int, title: str,
       sub: str = "", fill: str = "#fff", stroke: str = "#cbd5e1") -> str:
    """Compact box helper: title at y+26, subtitle lines at y+46+."""
    lines = [line.strip() for line in sub.split("\n") if line.strip()]
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#shadow)"/>',
        f'<text x="{x+14}" y="{y+26}" font-family="Segoe UI,Arial" '
        f'font-size="16" font-weight="700" fill="#0f172a">{escape(title)}</text>',
    ]
    for i, ln in enumerate(lines):
        parts.append(
            f'<text x="{x+14}" y="{y+46+i*19}" font-family="Segoe UI,Arial" '
            f'font-size="13" fill="#475569">{escape(ln)}</text>'
        )
    return "\n".join(parts)


def _container(x: int, y: int, w: int, h: int, label: str,
               fill: str = "#faf5ff", stroke: str = "#c4b5fd") -> str:
    """Dashed background container for grouping (e.g. Ray cluster)."""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" stroke-dasharray="7 4"/>'
        f'<text x="{x+14}" y="{y+20}" font-family="Segoe UI,Arial" font-size="12" '
        f'font-style="italic" fill="#7c3aed">{escape(label)}</text>'
    )


def _pa(d: str, label: str = "", lx: int = 0, ly: int = 0) -> str:
    """Orthogonal path arrow with optional pill label."""
    out = (f'<path d="{d}" stroke="#475569" stroke-width="2" fill="none" '
           f'marker-end="url(#arrow)"/>')
    if label:
        out += (
            f'<rect x="{lx-36}" y="{ly-12}" width="72" height="22" rx="11" '
            f'fill="#fff" stroke="#dde2ee" stroke-width="1.5"/>'
            f'<text x="{lx}" y="{ly+5}" text-anchor="middle" '
            f'font-family="Segoe UI,Arial" font-size="12" fill="#334155">{escape(label)}</text>'
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 1: Architecture
# ─────────────────────────────────────────────────────────────────────────────

def _diagram_architecture() -> str:
    c = []

    # ── Tier 1: user-facing ──────────────────────────────────────────────────
    c.append(_b(50, 78, 195, 82, "Browser / SPA",
                "frontend · JWT · fetch", "#dbeafe", "#60a5fa"))
    c.append(_b(310, 78, 205, 82, "FastAPI",
                "routing · auth · lifecycle", "#e0f2fe", "#38bdf8"))
    c.append(_pa("M 245,119 L 310,119", "HTTP", 277, 107))

    # ── Tier 2: Ray cluster (background drawn first so boxes sit on top) ─────
    c.append(_container(38, 198, 1224, 330,
                        "Ray cluster  (head node + worker nodes)"))

    #  Row 1 inside cluster
    c.append(_b(60, 232, 228, 92, "BookingCoordinator",
                "book_room()\ncancel_booking()", "#ede9fe", "#8b5cf6"))
    c.append(_b(352, 242, 192, 78, "InventoryActor",
                "hotel registry", "#f8fafc", "#94a3b8"))
    c.append(_b(610, 242, 186, 78, "HotelActors",
                "availability / holds", "#dbeafe", "#3b82f6"))
    c.append(_b(1014, 242, 182, 78, "AdminActor",
                "upsert_hotel()", "#f8fafc", "#94a3b8"))

    #  Row 2 inside cluster
    c.append(_b(60, 380, 170, 78, "PaymentActor",
                "payment sim", "#fee2e2", "#f87171"))
    c.append(_b(294, 380, 200, 78, "ReservHistory",
                "per-user history", "#dcfce7", "#4ade80"))
    c.append(_b(562, 380, 178, 78, "AuditLogActor",
                "domain events", "#fef9c3", "#fbbf24"))
    c.append(_b(810, 380, 170, 78, "MetricsActor",
                "Prometheus state", "#fce7f3", "#f472b6"))

    # ── Arrows inside cluster ─────────────────────────────────────────────────
    # FastAPI → BookingCoord (bent: down, left, down)
    c.append(_pa("M 412,160 L 412,220 L 174,220 L 174,232",
                 "ray calls", 293, 209))

    # BookingCoord → Inventory  (horizontal)
    c.append(_pa("M 288,278 L 352,278"))

    # Inventory → HotelActors  (horizontal)
    c.append(_pa("M 544,282 L 610,282"))

    # AdminActor → Inventory  (above row-1 boxes: up, left, down)
    c.append(_pa("M 1105,242 L 1105,214 L 448,214 L 448,242"))

    # BookingCoord → Payment  (straight down, aligned on x=145)
    c.append(_pa("M 145,324 L 145,380", "pay", 168, 353))

    # BookingCoord → ReservHistory  (bend right at y=360)
    c.append(_pa("M 188,324 L 188,358 L 394,358 L 394,380", "hist", 290, 347))

    # BookingCoord → AuditLog  (bend right at y=354, different x exit)
    c.append(_pa("M 210,324 L 210,354 L 651,354 L 651,380", "audit", 430, 342))

    # BookingCoord → Metrics  (bend right at y=350, yet another x exit)
    c.append(_pa("M 230,324 L 230,350 L 895,350 L 895,380", "mtx", 562, 338))

    # ── Tier 3: storage & monitoring ─────────────────────────────────────────
    c.append(_b(50, 564, 215, 82, "PostgreSQL",
                "hotels · reservations\naudit_logs", "#dcfce7", "#16a34a"))
    c.append(_b(336, 564, 215, 82, "Monitoring",
                "Prometheus + Grafana\nalerts", "#fef3c7", "#f59e0b"))

    # PaymentActor area → PostgreSQL
    c.append(_pa("M 145,458 L 145,536 L 158,536 L 158,564", "SQL", 183, 500))

    # MetricsActor → Monitoring
    c.append(_pa("M 895,458 L 895,536 L 443,536 L 443,564", "metrics", 669, 524))

    return _svg_base(1280, 672, "Architektura Ray Hotel", "\n".join(c))


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 2: Actor relations (column layout, no crossing arrows)
# ─────────────────────────────────────────────────────────────────────────────

def _diagram_actors() -> str:
    c = []

    # ── Column positions ──────────────────────────────────────────────────────
    # Col A x=55-258  Col B x=310-515  Col C x=570-775  Col D x=830-1080

    # Level 1
    c.append(_b(55, 85, 200, 78,  "AdminActor",
                "upsert_hotel()", "#f8fafc", "#64748b"))
    c.append(_b(570, 76, 256, 96, "BookingCoordinator",
                "book_room()\ncancel_booking()  idempotency", "#ede9fe", "#8b5cf6"))

    # Level 2
    c.append(_b(55, 268, 202, 96,  "InventoryActor",
                "search_hotels()\nhold / confirm / cancel", "#e0f2fe", "#0284c7"))
    c.append(_b(330, 268, 185, 78,  "PaymentActor",
                "process_payment()\nrefund_payment()", "#fee2e2", "#ef4444"))
    c.append(_b(590, 268, 215, 78, "ReservHistory",
                "add_reservation()\nlist_user_reservations()", "#dcfce7", "#16a34a"))

    # Level 3
    c.append(_b(55, 466, 202, 96,  "HotelActors",
                "try_hold()  confirm_hold()\ncancel_reservation()", "#dbeafe", "#2563eb"))
    c.append(_b(330, 466, 185, 78, "AuditLogActor",
                "log()\nlist_logs()", "#fef3c7", "#f59e0b"))
    c.append(_b(590, 466, 185, 78, "MetricsActor",
                "inc_*()  observe_*()\nget_metrics()", "#fce7f3", "#ec4899"))

    # ── Arrows ────────────────────────────────────────────────────────────────
    # Admin → Inventory  (straight down, same centre x=155)
    c.append(_pa("M 155,163 L 155,268", "admin", 180, 215))

    # BookingCoord → Inventory  (left at y=138 – above AdminActor, then down)
    c.append(_pa("M 570,138 L 272,138 L 272,316 L 257,316",
                 "hold/confirm", 410, 126))

    # BookingCoord → Payment  (down from coord bottom then left)
    c.append(_pa("M 695,172 L 695,248 L 422,248 L 422,268",
                 "payment", 558, 236))

    # BookingCoord → ReservHistory  (short down, almost vertical)
    c.append(_pa("M 718,172 L 718,252 L 697,252 L 697,268",
                 "history", 707, 240))

    # Inventory → HotelActors  (straight down, same centre x=156)
    c.append(_pa("M 156,364 L 156,466", "delegates", 184, 415))

    # BookingCoord → AuditLogActor
    # Route: right of diagram (x=880), down to y=480, left to AuditLog right (x=515)
    c.append(_pa("M 826,138 L 880,138 L 880,480 L 515,480 L 515,466",
                 "audit", 875, 310))

    # BookingCoord → MetricsActor
    # Route: right of diagram (x=900), down to y=505, left to MetricsActor right (x=775)
    c.append(_pa("M 826,158 L 900,158 L 900,505 L 775,505 L 775,466",
                 "metrics", 895, 330))

    return _svg_base(1000, 590, "Relacje aktorow Ray", "\n".join(c))


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 3: Booking flow (sequential numbered steps)
# ─────────────────────────────────────────────────────────────────────────────

def _diagram_booking_flow() -> str:
    c = []
    FF = "#475569"  # font fill for connectors

    # Header row: User → FastAPI → BookingCoordinator
    c.append(_b(44, 78, 148, 72, "User / SPA",
                "wybiera pokoj", "#dbeafe", "#3b82f6"))
    c.append(_b(252, 78, 190, 72, "FastAPI",
                "sprawdza JWT", "#e0f2fe", "#0284c7"))
    c.append(_b(504, 68, 252, 92, "BookingCoordinator",
                "saga · idempotency", "#ede9fe", "#8b5cf6"))
    c.append(_pa("M 192,114 L 252,114", "HTTP", 222, 102))
    c.append(_pa("M 442,114 L 504,114", "book_room()", 472, 102))

    # Steps ─ each 750 wide, centred, 94px tall (2 subtitle lines)
    SX, SW = 96, 750
    steps = [
        (200, "#e0f2fe", "#0284c7",
         "1  hold pokoju",
         "InventoryActor.hold_room() → HotelActor.try_hold()\n"
         "dostepnosc -= 1 · generowany hold_id · TTL = 300 s"),
        (320, "#fee2e2", "#ef4444",
         "2  platnosc",
         "PaymentActor.process_payment(user_id, amount, method)\n"
         "kwota = cena_pokoju × liczba_nocy · metoda: card / cash"),
        (440, "#dcfce7", "#16a34a",
         "3  potwierdzenie hold",
         "InventoryActor.confirm_hold() → HotelActor.confirm_hold()\n"
         "hold usuwany · generowany reservation_id"),
        (560, "#fef9c3", "#f59e0b",
         "4  zapis i zdarzenia",
         "ReservHistory.add_reservation() + DB save_reservation()\n"
         "AuditLog.log(RESERVATION_CREATED) + MetricsActor.inc_reservation()"),
        (694, "#e0f2fe", "#0284c7",
         "5  odpowiedz",
         "ReservationResponse(ok=True, reservation_id, payment_id, total_price)"),
    ]
    prev_bottom = 160
    for y, fill, stroke, title, sub in steps:
        lines = sub.count("\n") + 1
        h = 88 + (lines - 1) * 18
        c.append(_b(SX, y, SW, h, title, sub, fill, stroke))
        c.append(_pa(f"M {SX + SW//2},{prev_bottom} L {SX + SW//2},{y}"))
        prev_bottom = y + h

    return _svg_base(950, 814, "Normalny przeplyw rezerwacji (Happy Path)", "\n".join(c))


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 4: Cancel flow (sequential)
# ─────────────────────────────────────────────────────────────────────────────

def _diagram_cancel_flow() -> str:
    c = []

    # Header
    c.append(_b(44, 78, 155, 72,  "User / SPA",
                "Cancel booking", "#dbeafe", "#3b82f6"))
    c.append(_b(258, 78, 195, 72, "FastAPI",
                "JWT + owner check", "#e0f2fe", "#0284c7"))
    c.append(_b(514, 68, 252, 92, "BookingCoordinator",
                "walidacja · polityka zwrotu", "#ede9fe", "#8b5cf6"))
    c.append(_pa("M 199,114 L 258,114", "HTTP", 228, 102))
    c.append(_pa("M 453,114 L 514,114", "cancel_booking()", 483, 102))

    SX, SW = 96, 750
    steps = [
        (200, "#f8fafc", "#94a3b8",
         "1  weryfikacja wlasciciela",
         "sprawdzenie: reservation.user_id == user_id z tokenu JWT\n"
         "jesli niezgodnosc → ok=False, brak dalszych operacji"),
        (318, "#dcfce7", "#16a34a",
         "2  anulacja w hotelu",
         "InventoryActor.cancel_reservation() → HotelActor.cancel_reservation()\n"
         "status = cancelled · available += 1 · snapshot do DB"),
        (436, "#fef3c7", "#f59e0b",
         "3  polityka zwrotu",
         "czas od created_at <= 1h → refund_percent = 100\n"
         "czas od created_at > 1h  → refund_percent = 0"),
        (554, "#dbeafe", "#3b82f6",
         "4  aktualizacja danych",
         "ReservHistory.cancel_reservation() + DB update_reservation_status()\n"
         "AuditLog.log(RESERVATION_CANCELLED) + MetricsActor.inc_cancellation()"),
        (686, "#e0f2fe", "#0284c7",
         "5  odpowiedz",
         "ReservationResponse(ok=True, refund_percent, refund_amount)"),
    ]
    prev_bottom = 160
    for y, fill, stroke, title, sub in steps:
        lines = sub.count("\n") + 1
        h = 88 + (lines - 1) * 18
        c.append(_b(SX, y, SW, h, title, sub, fill, stroke))
        c.append(_pa(f"M {SX + SW//2},{prev_bottom} L {SX + SW//2},{y}"))
        prev_bottom = y + h

    return _svg_base(950, 806, "Przeplyw anulowania rezerwacji", "\n".join(c))


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 5: Data model (ER tables side by side)
# ─────────────────────────────────────────────────────────────────────────────

def _diagram_data_model() -> str:
    c = []

    def _table(x: int, y: int, w: int, name: str, fields: list[str],
               hdr_fill: str, hdr_stroke: str) -> str:
        row_h = 24
        body_h = len(fields) * row_h + 10
        hdr_h = 38
        parts = [
            f'<rect x="{x}" y="{y}" width="{w}" height="{hdr_h+body_h}" rx="10" '
            f'fill="#fff" stroke="{hdr_stroke}" stroke-width="2" filter="url(#shadow)"/>',
            f'<rect x="{x}" y="{y}" width="{w}" height="{hdr_h}" rx="10" '
            f'fill="{hdr_fill}" stroke="{hdr_stroke}" stroke-width="2"/>',
            f'<rect x="{x}" y="{y+10}" width="{w}" height="{hdr_h-10}" fill="{hdr_fill}"/>',
            f'<text x="{x+14}" y="{y+26}" font-family="Segoe UI,Arial" '
            f'font-size="17" font-weight="700" fill="#0f172a">{escape(name)}</text>',
        ]
        # horizontal separator
        parts.append(
            f'<line x1="{x}" y1="{y+hdr_h}" x2="{x+w}" y2="{y+hdr_h}" '
            f'stroke="{hdr_stroke}" stroke-width="1.5"/>'
        )
        for i, field in enumerate(fields):
            fy = y + hdr_h + 8 + i * row_h
            bold = "font-weight=\"600\"" if ("PK" in field or "UNIQUE" in field) else ""
            col = "#0f172a" if ("PK" in field or "UNIQUE" in field) else "#475569"
            parts.append(
                f'<text x="{x+14}" y="{fy+16}" font-family="Segoe UI,Arial" '
                f'font-size="13" {bold} fill="{col}">{escape(field)}</text>'
            )
        return "\n".join(parts)

    c.append(_table(50, 90, 255, "hotels",
                    ["hotel_id  PK", "name", "city", "rooms  JSON", "updated_at"],
                    "#dcfce7", "#22c55e"))

    c.append(_table(368, 78, 295, "reservations",
                    ["reservation_id  PK", "user_id", "hotel_id  →  hotels",
                     "room_type", "nights", "total_price", "payment_id",
                     "status", "created_at", "refund_percent", "refund_amount"],
                    "#dbeafe", "#2563eb"))

    c.append(_table(726, 90, 265, "audit_logs",
                    ["id  PK", "event_id  UNIQUE", "event_type",
                     "actor_id", "entity_id", "details  JSON", "occurred_at"],
                    "#fef3c7", "#f59e0b"))

    # FK arrow: hotels → reservations (horizontal between tables, mid-height of hotels)
    arrow_y = 90 + 38 + 3 * 24 + 8  # at hotel_id row area → use row 1 y for clarity
    arrow_y = 152  # hotels.hotel_id ≈ y=90+38+8+24=160; reservations.hotel_id row ≈ same
    c.append(
        f'<line x1="305" y1="{arrow_y}" x2="368" y2="{arrow_y}" '
        f'stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>'
        f'<rect x="296" y="{arrow_y-22}" width="76" height="20" rx="10" '
        f'fill="#fff" stroke="#dde2ee" stroke-width="1.5"/>'
        f'<text x="334" y="{arrow_y-7}" text-anchor="middle" font-family="Segoe UI,Arial" '
        f'font-size="12" fill="#334155">1 : N</text>'
    )

    return _svg_base(1040, 430, "Model danych PostgreSQL", "\n".join(c))


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 6: Audit events (fan-in to AuditLogActor via horizontal bus)
# ─────────────────────────────────────────────────────────────────────────────

def _diagram_events() -> str:
    c = []

    # Six event-group boxes in ONE row, then a horizontal bus, then AuditLogActor
    BUS_Y = 280   # y of horizontal bus line
    ACTOR_Y = 336  # top of AuditLogActor box

    groups = [
        (30,   80, 148, 78, "Logowanie",   "LOGIN", "#e0f2fe", "#0284c7"),
        (198,  80, 178, 78, "Admin",        "HOTEL_UPSERTED", "#f1f5f9", "#64748b"),
        (396,  70, 178, 98, "Hold",
         "HOLD_CREATED\nHOLD_RELEASED\nHOLD_CONFIRMED", "#ede9fe", "#8b5cf6"),
        (594,  70, 185, 98, "Platnosc",
         "PAYMENT_SUCCESS\nPAYMENT_FAILED\nPAYMENT_REFUNDED", "#fee2e2", "#ef4444"),
        (799,  70, 195, 98, "Rezerwacja",
         "RESERVATION_CREATED\nRESERVATION_CANCELLED", "#dcfce7", "#22c55e"),
        (1014, 80, 155, 78, "Blad zwrotu",
         "REFUND_FAILED", "#fee2e2", "#ef4444"),
    ]

    centres = []
    for (bx, by, bw, bh, title, sub, fill, stroke) in groups:
        c.append(_b(bx, by, bw, bh, title, sub, fill, stroke))
        cx = bx + bw // 2
        box_bottom = by + bh
        centres.append((cx, box_bottom))

    # Bus line (horizontal)
    left_x  = centres[0][0]
    right_x = centres[-1][0]
    c.append(
        f'<line x1="{left_x}" y1="{BUS_Y}" x2="{right_x}" y2="{BUS_Y}" '
        f'stroke="#94a3b8" stroke-width="2"/>'
    )

    # Verticals from each box bottom to the bus
    for cx, bottom in centres:
        c.append(
            f'<line x1="{cx}" y1="{bottom}" x2="{cx}" y2="{BUS_Y}" '
            f'stroke="#94a3b8" stroke-width="1.5"/>'
        )

    # Central arrow from bus midpoint down to AuditLogActor
    mid_x = (left_x + right_x) // 2
    c.append(_pa(f"M {mid_x},{BUS_Y} L {mid_x},{ACTOR_Y}",
                 "log(entry)", mid_x, BUS_Y + (ACTOR_Y - BUS_Y) // 2))

    # AuditLogActor
    AW = 320
    AX = mid_x - AW // 2
    c.append(_b(AX, ACTOR_Y, AW, 78, "AuditLogActor",
                "write_audit_log(entry)", "#fef3c7", "#f59e0b"))

    # PostgreSQL below AuditLogActor
    PY = ACTOR_Y + 78 + 50
    PW = 280
    PX = mid_x - PW // 2
    c.append(_b(PX, PY, PW, 72, "PostgreSQL  audit_logs",
                "persisted domain events", "#dcfce7", "#16a34a"))
    c.append(_pa(f"M {mid_x},{ACTOR_Y+78} L {mid_x},{PY}", "SQL", mid_x, ACTOR_Y + 78 + 25))

    return _svg_base(1200, PY + 92, "Typy zdarzen audytowych", "\n".join(c))


# ─────────────────────────────────────────────────────────────────────────────

def generate_diagrams() -> None:
    """Generate SVG diagrams used by the DOCX report."""
    DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    svg_by_id = {
        "architecture": _diagram_architecture(),
        "actors": _diagram_actors(),
        "booking_flow": _diagram_booking_flow(),
        "cancel_flow": _diagram_cancel_flow(),
        "data_model": _diagram_data_model(),
        "events": _diagram_events(),
    }
    for diagram in DIAGRAMS:
        (DIAGRAMS_DIR / diagram["file"]).write_text(svg_by_id[diagram["id"]], encoding="utf-8")


def _document_xml() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    body = [
        _paragraph("Rozproszony system rezerwacji hoteli", "Title"),
        _paragraph("Szczegolowy raport techniczny projektu Ray Hotel", "Subtitle"),
        _paragraph(f"Wygenerowano: {today}"),
        _paragraph("1. Cel projektu", "Heading1"),
        _paragraph(
            "Projekt implementuje rozproszony system rezerwacji hoteli oparty na architekturze aktorowej Ray. "
            "System pozwala wyszukiwać hotele, rezerwować pokoje, anulować rezerwacje, obsługiwać panel administratora "
            "oraz monitorować działanie aplikacji przez Prometheus i Grafana. Celem architektury jest pokazanie praktycznego "
            "podziału odpowiedzialności na niezależne komponenty, które komunikują się przez zdalne wywołania aktorów, a nie "
            "przez współdzieloną pamięć."
        ),
        _paragraph("2. Zakres przeanalizowanych materiałów", "Heading1"),
        _bullet("Kod źródłowy projektu w katalogu app/, frontend/, monitoring/ oraz testy w tests/."),
        _bullet("README.md z opisem uruchomienia, endpointów i etapów realizacji."),
        _bullet("system_rezerwacji_podsumowanie.txt z założeniami architektury rozproszonej."),
        _bullet("Diagram sekwencji docs/ray_hotel_sequence.puml opisujący przepływ rezerwacji i anulacji."),
        _paragraph(
            "W dostępnym workspace nie znaleziono plików prezentacji ani pliku flight_radar.pdf, dlatego struktura raportu "
            "została opracowana na podstawie dostępnych materiałów projektu."
        ),
        _paragraph("3. Stos technologiczny", "Heading1"),
        _table([
            ["Obszar", "Technologia", "Rola w systemie"],
            ["Backend/API", "FastAPI, Pydantic", "Walidacja danych, autoryzacja i ekspozycja endpointów HTTP."],
            ["Rozproszenie", "Ray Actors", "Izolowane aktory dla hoteli, koordynacji, płatności i historii."],
            ["Trwałość danych", "PostgreSQL, SQLAlchemy", "Snapshoty hoteli, rezerwacje oraz audit log."],
            ["Frontend", "HTML/CSS/Vanilla JS, Nginx", "Demo SPA dla użytkownika i administratora."],
            ["Monitoring", "Prometheus, Grafana", "Metryki HTTP, rezerwacji, płatności i alerty."],
            ["Uruchomienie", "Docker Compose", "Head node Ray, workery, baza, frontend i monitoring."],
        ]),
        _paragraph("4. Diagram architektury systemu", "Heading1"),
        _paragraph(
            "System jest podzielony na warstwę prezentacji, API, klaster aktorów Ray, trwałą bazę danych oraz monitoring. "
            "FastAPI działa jako brama HTTP: waliduje dane, sprawdza JWT i przekazuje operacje do odpowiednich aktorów. "
            "Najważniejsza logika domenowa nie znajduje się w kontrolerach HTTP, tylko w aktorach Ray."
        ),
        _figure("architecture"),
        _paragraph("5. Struktura katalogów", "Heading1"),
        _bullet("app/main.py - konfiguracja FastAPI, JWT, lifecycle aplikacji i endpointy."),
        _bullet("app/actors.py - aktorzy Ray implementujący logikę domenową i komunikację rozproszoną."),
        _bullet("app/models.py - modele Pydantic request/response dla API."),
        _bullet("app/db.py - modele SQLAlchemy i funkcje zapisu/odczytu z Postgresa."),
        _bullet("frontend/ - statyczny interfejs SPA z widokami logowania, hoteli, checkoutu, historii i admina."),
        _bullet("monitoring/ - konfiguracja Prometheus, alertów i dashboardu Grafana."),
        _bullet("tests/ - testy aktorów oraz testy integracyjne FastAPI bez Dockera i z mockowaną bazą."),
        _bullet("docs/ - diagram PlantUML, dokumentacja pydoc i niniejszy raport."),
        _paragraph("6. Architektura aktorów i odpowiedzialności", "Heading1"),
        _paragraph(
            "System jest podzielony na aktorów o jasnych odpowiedzialnościach. HotelActor przechowuje stan pojedynczego hotelu "
            "i jest granicą współbieżności zapobiegającą overbookingowi. InventoryActor agreguje hotele i deleguje operacje "
            "hold/confirm/cancel. BookingCoordinatorActor realizuje przepływ typu saga: tworzy hold, wykonuje płatność, "
            "potwierdza rezerwację, zapisuje historię i wykonuje rollback w przypadku błędu. PaymentActor symuluje bramkę "
            "płatniczą, ReservationHistoryActor utrzymuje historię użytkowników, AuditLogActor zapisuje zdarzenia, a "
            "MetricsActor utrzymuje metryki Prometheus."
        ),
        _table([
            ["Aktor", "Odpowiedzialność", "Najważniejszy stan", "Komunikuje się z"],
            ["HotelActor", "Stan pojedynczego hotelu, dostępność, holdy, rezerwacje lokalne.", "rooms, holds, reservations", "InventoryActor"],
            ["InventoryActor", "Rejestr hoteli i fasada dla operacji hotelowych.", "mapa hotel_id -> HotelActor", "HotelActor, AdminActor, BookingCoordinatorActor"],
            ["BookingCoordinatorActor", "Orkiestracja procesu rezerwacji i anulacji.", "reservations, idempotency", "InventoryActor, PaymentActor, ReservationHistoryActor, AuditLogActor, MetricsActor"],
            ["PaymentActor", "Symulacja płatności i zwrotów.", "brak trwałego stanu", "BookingCoordinatorActor"],
            ["ReservationHistoryActor", "Historia rezerwacji per użytkownik.", "by_user, by_id", "BookingCoordinatorActor"],
            ["AuditLogActor", "Asynchroniczny zapis zdarzeń domenowych.", "brak stanu biznesowego", "FastAPI, BookingCoordinatorActor, AdminActor"],
            ["MetricsActor", "Liczniki i histogramy Prometheus.", "CollectorRegistry", "FastAPI, BookingCoordinatorActor"],
            ["AdminActor", "Operacje administracyjne na hotelach.", "referencje do aktorów", "InventoryActor, AuditLogActor"],
        ]),
        _paragraph("7. Diagram klas i relacji aktorów", "Heading1"),
        _paragraph(
            "Poniższy diagram pokazuje relacje zależności między klasami aktorów. Strzałka oznacza, że aktor posiada referencję "
            "do drugiego aktora albo wywołuje jego metodę zdalną. Nie jest to dziedziczenie, tylko relacja komunikacyjna."
        ),
        _figure("actors"),
        _paragraph("8. Jak działa komunikacja między aktorami", "Heading1"),
        _paragraph(
            "Aktor Ray jest obiektem działającym w osobnym procesie lub na innym nodzie klastra. Wywołanie metody aktora nie jest "
            "zwykłym wywołaniem lokalnym. Kod używa składni actor.method.remote(...), która zwraca ObjectRef. Wynik jest pobierany "
            "przez ray.get(...). W projekcie wiele wywołań przechodzi przez funkcję _ray_call(), która dodaje timeout, retry oraz "
            "exponential backoff dla błędów RayActorError i RayTaskError."
        ),
        _bullet("FastAPI nie dotyka bezpośrednio stanu hotelu. Wysyła żądania do aktorów przez referencje zapisane w app.state."),
        _bullet("BookingCoordinatorActor nie zmienia rooms w HotelActor bezpośrednio. Prosi InventoryActor o hold/confirm/cancel, a InventoryActor deleguje do właściwego HotelActor."),
        _bullet("AuditLogActor i MetricsActor są wywoływane asynchronicznie tam, gdzie operacja nie powinna blokować głównego przepływu."),
        _bullet("HotelActor jest naturalnym lockiem dla pojedynczego hotelu: wszystkie operacje na jego stanie przechodzą przez kolejkę aktora, więc nie ma współdzielonej mutacji rooms poza aktorem."),
        _paragraph("9. Zwykły przepływ rezerwacji - opis", "Heading1"),
        _paragraph(
            "Normalny przepływ zaczyna się w przeglądarce. Użytkownik loguje się, dostaje JWT, wyszukuje hotel, wybiera pokój i "
            "wysyła żądanie rezerwacji. FastAPI sprawdza token oraz zgodność user_id z użytkownikiem z tokenu. Następnie przekazuje "
            "żądanie do BookingCoordinatorActor. Koordynator najpierw tworzy hold w hotelu, później wykonuje płatność, potwierdza hold, "
            "zapisuje rezerwację w historii i bazie, publikuje zdarzenia audytowe oraz metryki. Dopiero po potwierdzeniu wszystkich "
            "krytycznych kroków zwraca odpowiedź z reservation_id i payment_id."
        ),
        _figure("booking_flow"),
        _paragraph("10. Przepływy alternatywne i obsługa błędów", "Heading1"),
        _table([
            ["Sytuacja", "Miejsce wykrycia", "Zachowanie systemu"],
            ["Brak pokoju", "HotelActor.try_hold", "Zwracany jest wynik ok=false, nie ma płatności ani rezerwacji."],
            ["Odrzucona płatność", "PaymentActor.process_payment", "BookingCoordinatorActor zwalnia hold przez InventoryActor.release_hold."],
            ["Hold wygasł przed confirm", "HotelActor.confirm_hold", "Koordynator próbuje wykonać refund płatności i zwraca błąd."],
            ["Ponowione żądanie", "BookingCoordinatorActor.idempotency", "Ten sam user_id i idempotency_key zwracają zapamiętaną odpowiedź."],
            ["Timeout Ray", "FastAPI lub _ray_call", "Endpoint zwraca HTTP 504 albo retry jest wykonany wewnątrz aktora."],
            ["Anulacja po czasie", "BookingCoordinatorActor.cancel_booking", "Rezerwacja jest anulowana, ale refund_percent wynosi 0."],
        ]),
        _paragraph("11. Diagram anulowania rezerwacji", "Heading1"),
        _figure("cancel_flow"),
        _paragraph("12. Typy zdarzeń audytowych", "Heading1"),
        _paragraph(
            "Audit log zapisuje istotne operacje domenowe w tabeli audit_logs. Każdy wpis ma event_id, event_type, actor_id, "
            "entity_id, details oraz occurred_at. Dzięki temu administrator może później sprawdzić, kto wykonał operację, "
            "na jakim obiekcie i z jakim skutkiem."
        ),
        _figure("events"),
        _table([
            ["Event type", "Kiedy powstaje", "Najważniejsze pola details"],
            ["LOGIN", "Po poprawnym logowaniu.", "role"],
            ["HOTEL_UPSERTED", "Po dodaniu lub aktualizacji hotelu przez admina.", "name, city, action"],
            ["HOLD_CREATED", "Po tymczasowej blokadzie pokoju.", "hotel_id, room_type, nights, total_price"],
            ["HOLD_RELEASED", "Po zwolnieniu holda, np. przez błąd płatności.", "hotel_id, reason"],
            ["HOLD_CONFIRMED", "Po zamianie holda na rezerwację.", "hotel_id, reservation_id"],
            ["PAYMENT_SUCCESS", "Po zaakceptowaniu płatności.", "amount, payment_method"],
            ["PAYMENT_FAILED", "Po odrzuceniu płatności.", "hotel_id, amount, reason"],
            ["PAYMENT_REFUNDED", "Po zwrocie płatności, gdy confirm się nie udał.", "amount, reason, hold_id"],
            ["REFUND_FAILED", "Gdy zwrot po błędzie confirm się nie powiedzie.", "amount, reason"],
            ["RESERVATION_CREATED", "Po utworzeniu rezerwacji.", "hotel_id, room_type, nights, total_price, payment_id"],
            ["RESERVATION_CANCELLED", "Po anulowaniu rezerwacji.", "hotel_id, refund_percent, refund_amount"],
        ]),
        _paragraph("13. Diagram danych i relacji tabel", "Heading1"),
        _figure("data_model"),
        _paragraph("14. API", "Heading1"),
        _table([
            ["Endpoint", "Metoda", "Opis"],
            ["/health", "GET", "Status aplikacji i klastra Ray."],
            ["/metrics", "GET", "Metryki Prometheus."],
            ["/auth/login", "POST", "Logowanie demo i JWT."],
            ["/hotels/search", "POST", "Wyszukiwanie hoteli po filtrach."],
            ["/reservations", "POST", "Utworzenie rezerwacji."],
            ["/reservations/cancel", "POST", "Anulowanie rezerwacji."],
            ["/users/{user_id}/reservations", "GET", "Historia rezerwacji użytkownika."],
            ["/admin/hotels", "POST", "Dodanie lub aktualizacja hotelu."],
            ["/admin/audit-logs", "GET", "Przegląd zdarzeń audytowych."],
        ]),
        _paragraph("15. Dane i trwałość", "Heading1"),
        _paragraph(
            "Baza Postgres przechowuje trzy główne tabele: hotels, reservations i audit_logs. Aktorzy odtwarzają stan z bazy "
            "przy starcie, a HotelActor okresowo zapisuje snapshot dostępności. Holdy celowo nie są trwałe, ponieważ są "
            "tymczasową blokadą z TTL i po restarcie nie powinny pozostać aktywne."
        ),
        _paragraph(
            "Najważniejszy kompromis projektowy dotyczy holdów. Są one stanem ulotnym, bo reprezentują tymczasową blokadę pokoju "
            "na czas płatności. Po restarcie systemu hold nie wraca z bazy, co jest bezpieczniejsze niż odtworzenie potencjalnie "
            "przeterminowanej blokady. Rezerwacje potwierdzone są natomiast zapisywane trwale."
        ),
        _paragraph("16. Niezawodność i skalowanie", "Heading1"),
        _bullet("Ray head node uruchamia FastAPI, GCS i dashboard, a workery dołączają do klastra przez ray-head:6379."),
        _bullet("Aktorzy mają limity CPU i strategię SPREAD, co ułatwia równomierne rozłożenie po nodach."),
        _bullet("Wywołania między aktorami są opakowane timeoutem, retry i exponential backoff."),
        _bullet("Idempotency key zabezpiecza przed podwójnym utworzeniem rezerwacji po ponowieniu żądania."),
        _bullet("Rollback hold przy błędzie płatności chroni dostępność pokoi przed trwałą blokadą."),
        _paragraph("17. Monitoring", "Heading1"),
        _paragraph(
            "MetricsActor publikuje liczniki rezerwacji, anulacji, płatności i żądań HTTP oraz histogramy czasu rezerwacji "
            "i anulacji. Prometheus scrape'uje backend i Ray Dashboard, a Grafana ma przygotowany dashboard. Alerty obejmują "
            "wysoki udział timeoutów, błędów rezerwacji, błędów płatności i wolne rezerwacje."
        ),
        _table([
            ["Metryka", "Typ", "Znaczenie"],
            ["hotel_reservations_total{status}", "Counter", "Liczba prób rezerwacji z podziałem na confirmed/failed."],
            ["hotel_cancellations_total{status}", "Counter", "Liczba prób anulowania."],
            ["hotel_payments_total{status}", "Counter", "Liczba płatności zaakceptowanych i odrzuconych."],
            ["hotel_active_holds", "Gauge", "Aktualnie aktywne holdy."],
            ["hotel_booking_duration_seconds", "Histogram", "Czas pełnego przepływu rezerwacji."],
            ["hotel_cancellation_duration_seconds", "Histogram", "Czas anulowania rezerwacji."],
            ["hotel_http_requests_total", "Counter", "Ruch HTTP z podziałem na method, endpoint i status_code."],
        ]),
        _paragraph("18. Testy", "Heading1"),
        _bullet("tests/test_actors.py testuje pojedynczych aktorów Ray: hotel, płatności, historię i audit log."),
        _bullet("tests/test_api.py testuje logowanie, wyszukiwanie, pełny przepływ rezerwacji, admina i brak overbookingu."),
        _bullet("tests/conftest.py uruchamia lokalny Ray i mockuje bazę, dzięki czemu testy nie wymagają Dockera ani Postgresa."),
        _paragraph("19. Uruchomienie", "Heading1"),
        _bullet("docker compose build"),
        _bullet("docker compose up -d"),
        _bullet("Backend: http://localhost:8000/docs"),
        _bullet("Frontend: http://localhost:8080"),
        _bullet("Ray Dashboard: http://localhost:8265"),
        _bullet("Prometheus: http://localhost:9090, Grafana: http://localhost:3000"),
        _paragraph("20. Dokumentacja pydoc", "Heading1"),
        _paragraph(
            "Kod backendu został uzupełniony o docstringi modułów, klas i funkcji. Statyczna dokumentacja wygenerowana "
            "z tych docstringów znajduje się w docs/pydoc/index.html."
        ),
        _paragraph("21. Wnioski", "Heading1"),
        _paragraph(
            "Projekt spełnia założenia systemu rozproszonego: komponenty domenowe są izolowane jako aktorzy Ray, przepływ "
            "rezerwacji jest koordynowany transakcyjnie na poziomie aplikacji, stan krytyczny jest utrwalany w bazie, a "
            "działanie systemu można obserwować przez metryki i testować bez pełnego środowiska kontenerowego. Najważniejszym "
            "elementem projektu jest świadome wydzielenie HotelActor jako granicy spójności dla dostępności pokoi oraz "
            "BookingCoordinatorActor jako orkiestratora procesu rezerwacji z rollbackiem i idempotencją."
        ),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{''.join(body)}<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr></w:body>"
        "</w:document>"
    )


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:rPr><w:i/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="160"/></w:pPr><w:rPr><w:i/><w:color w:val="475569"/><w:sz w:val="18"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="CodeBlock"><w:name w:val="Code Block"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="120" w:after="120"/><w:ind w:left="240"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="16"/></w:rPr></w:style>
</w:styles>"""


def _document_relationships_xml() -> str:
    rows = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for diagram in DIAGRAMS:
        rows.append(
            f'<Relationship Id="rId_{diagram["id"]}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/{diagram["file"]}"/>'
        )
    rows.append("</Relationships>")
    return "\n".join(rows)


def generate_docx_report(path: Path = REPORT_PATH) -> Path:
    """Create a detailed valid DOCX report and return its path."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    generate_diagrams()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="svg" ContentType="image/svg+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>""",
        )
        docx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        docx.writestr("word/document.xml", _document_xml())
        docx.writestr("word/styles.xml", _styles_xml())
        docx.writestr("word/_rels/document.xml.rels", _document_relationships_xml())
        for diagram in DIAGRAMS:
            docx.write(DIAGRAMS_DIR / diagram["file"], f"word/media/{diagram['file']}")
    return path


def main() -> None:
    """Generate all documentation artifacts."""
    generate_pydoc_html()
    try:
        report_path = generate_docx_report()
    except PermissionError:
        report_path = generate_docx_report(DETAILED_REPORT_PATH)
        print(f"Skipped {REPORT_PATH} because the file is open or locked.")
    print(f"Generated {PYDOC_DIR / 'index.html'}")
    print(f"Generated {report_path}")


if __name__ == "__main__":
    main()
