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
    {"id": "architecture", "file": "architecture.svg", "title": "Diagram architektury systemu", "width": 1200, "height": 760},
    {"id": "actors", "file": "actors.svg", "title": "Diagram relacji aktorow Ray", "width": 1200, "height": 760},
    {"id": "booking_flow", "file": "booking_flow.svg", "title": "Diagram przeplywu rezerwacji", "width": 1200, "height": 720},
    {"id": "cancel_flow", "file": "cancel_flow.svg", "title": "Diagram przeplywu anulowania", "width": 1200, "height": 680},
    {"id": "data_model", "file": "data_model.svg", "title": "Diagram modelu danych", "width": 1200, "height": 650},
    {"id": "events", "file": "events.svg", "title": "Diagram zdarzen audytowych", "width": 1200, "height": 620},
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


def _svg_base(width: int, height: int, title: str, content: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#334155"/>
  </marker>
  <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">
    <feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#0f172a" flood-opacity="0.14"/>
  </filter>
</defs>
<rect width="100%" height="100%" rx="24" fill="#f8fafc"/>
<text x="40" y="52" font-family="Segoe UI, Arial" font-size="30" font-weight="700" fill="#0f172a">{escape(title)}</text>
{content}
</svg>"""


def _svg_box(x: int, y: int, w: int, h: int, title: str, subtitle: str = "", fill: str = "#ffffff", stroke: str = "#cbd5e1") -> str:
    title_xml = escape(title)
    subtitle_lines = [line.strip() for line in subtitle.split("\n") if line.strip()]
    rows = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#shadow)"/>',
        f'<text x="{x + 18}" y="{y + 32}" font-family="Segoe UI, Arial" font-size="19" font-weight="700" fill="#0f172a">{title_xml}</text>',
    ]
    for index, line in enumerate(subtitle_lines):
        rows.append(
            f'<text x="{x + 18}" y="{y + 60 + index * 22}" font-family="Segoe UI, Arial" font-size="15" fill="#475569">{escape(line)}</text>'
        )
    return "\n".join(rows)


def _svg_arrow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    mid_x = (x1 + x2) / 2
    mid_y = (y1 + y2) / 2
    label_xml = (
        f'<rect x="{mid_x - 72}" y="{mid_y - 22}" width="144" height="24" rx="12" fill="#ffffff" stroke="#e2e8f0"/>'
        f'<text x="{mid_x}" y="{mid_y - 5}" text-anchor="middle" font-family="Segoe UI, Arial" font-size="13" fill="#334155">{escape(label)}</text>'
        if label
        else ""
    )
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#334155" stroke-width="2.2" marker-end="url(#arrow)"/>{label_xml}'


def _diagram_architecture() -> str:
    content = "\n".join(
        [
            _svg_box(55, 115, 210, 105, "Browser / SPA", "frontend/index.html\nJWT + fetch API", "#dbeafe", "#60a5fa"),
            _svg_box(355, 105, 245, 125, "FastAPI", "main.py\nrouting, auth, lifecycle", "#e0f2fe", "#38bdf8"),
            _svg_box(710, 95, 420, 150, "Ray cluster", "head node + worker nodes\nremote actors + scheduler", "#ede9fe", "#a78bfa"),
            _svg_box(735, 295, 170, 92, "Inventory", "hotel registry", "#ffffff", "#a78bfa"),
            _svg_box(940, 285, 165, 112, "HotelActors", "availability\nholds\nreservations", "#ffffff", "#a78bfa"),
            _svg_box(690, 445, 205, 110, "BookingCoordinator", "booking saga\nrollback\nidempotency", "#ffffff", "#a78bfa"),
            _svg_box(930, 445, 155, 95, "Payment", "payment sim\nrefunds", "#ffffff", "#a78bfa"),
            _svg_box(690, 610, 195, 92, "History + Audit", "reservations\ndomain events", "#ffffff", "#a78bfa"),
            _svg_box(75, 445, 250, 120, "PostgreSQL", "hotels\nreservations\naudit_logs", "#dcfce7", "#22c55e"),
            _svg_box(380, 445, 215, 120, "Monitoring", "Prometheus\nGrafana dashboard\nalerts", "#fef3c7", "#f59e0b"),
            _svg_arrow(265, 165, 355, 165, "HTTP"),
            _svg_arrow(600, 165, 710, 165, "ray calls"),
            _svg_arrow(820, 245, 820, 295),
            _svg_arrow(905, 340, 940, 340),
            _svg_arrow(800, 445, 820, 387),
            _svg_arrow(895, 500, 930, 500),
            _svg_arrow(785, 555, 785, 610),
            _svg_arrow(690, 655, 325, 505, "SQL"),
            _svg_arrow(490, 445, 490, 230, "metrics"),
        ]
    )
    return _svg_base(1200, 760, "Architektura Ray Hotel", content)


def _diagram_actors() -> str:
    content = "\n".join(
        [
            _svg_box(470, 100, 260, 115, "BookingCoordinatorActor", "book_room()\ncancel_booking()", "#ede9fe", "#8b5cf6"),
            _svg_box(90, 275, 230, 105, "InventoryActor", "search / hold / confirm\nregistry HotelActor", "#e0f2fe", "#0284c7"),
            _svg_box(90, 500, 230, 125, "HotelActor", "rooms\nholds with TTL\nlocal reservations", "#dbeafe", "#2563eb"),
            _svg_box(470, 300, 230, 95, "PaymentActor", "process_payment()\nrefund_payment()", "#fee2e2", "#ef4444"),
            _svg_box(800, 290, 250, 105, "ReservationHistoryActor", "by_user\nby_id", "#dcfce7", "#22c55e"),
            _svg_box(455, 535, 245, 105, "AuditLogActor", "log()\nlist_logs()", "#fef3c7", "#f59e0b"),
            _svg_box(805, 520, 245, 105, "MetricsActor", "counters\ngauges\nhistograms", "#fce7f3", "#ec4899"),
            _svg_box(90, 95, 230, 105, "AdminActor", "upsert_hotel()", "#f1f5f9", "#64748b"),
            _svg_arrow(470, 160, 320, 325, "inventory"),
            _svg_arrow(320, 380, 205, 500, "delegates"),
            _svg_arrow(600, 215, 585, 300, "payment"),
            _svg_arrow(730, 160, 800, 325, "history"),
            _svg_arrow(590, 215, 575, 535, "audit"),
            _svg_arrow(730, 170, 805, 560, "metrics"),
            _svg_arrow(205, 200, 205, 275, "admin"),
            _svg_arrow(320, 150, 470, 135, "audit"),
        ]
    )
    return _svg_base(1200, 760, "Relacje aktorow Ray", content)


def _diagram_booking_flow() -> str:
    content = "\n".join(
        [
            _svg_box(55, 120, 170, 80, "1. User", "wybiera pokoj", "#dbeafe", "#3b82f6"),
            _svg_box(275, 120, 190, 80, "2. FastAPI", "JWT + request", "#e0f2fe", "#0284c7"),
            _svg_box(520, 105, 250, 110, "3. Coordinator", "saga rezerwacji\nidempotency", "#ede9fe", "#8b5cf6"),
            _svg_box(830, 85, 220, 90, "4. Inventory", "hold_room()", "#f1f5f9", "#64748b"),
            _svg_box(830, 225, 220, 90, "5. HotelActor", "try_hold()\navailable -= 1", "#dbeafe", "#2563eb"),
            _svg_box(520, 300, 250, 90, "6. PaymentActor", "process_payment()", "#fee2e2", "#ef4444"),
            _svg_box(830, 395, 220, 90, "7. HotelActor", "confirm_hold()\nreservation_id", "#dcfce7", "#22c55e"),
            _svg_box(520, 505, 250, 90, "8. History + DB", "save reservation", "#dcfce7", "#16a34a"),
            _svg_box(215, 505, 210, 90, "9. Audit + Metrics", "events\ncounters", "#fef3c7", "#f59e0b"),
            _svg_box(55, 505, 130, 90, "10. API", "ok=true", "#e0f2fe", "#0284c7"),
            _svg_arrow(225, 160, 275, 160),
            _svg_arrow(465, 160, 520, 160),
            _svg_arrow(770, 160, 830, 130, "hold"),
            _svg_arrow(940, 175, 940, 225),
            _svg_arrow(830, 270, 770, 345, "hold_id"),
            _svg_arrow(645, 215, 645, 300, "pay"),
            _svg_arrow(770, 345, 830, 430, "confirm"),
            _svg_arrow(830, 440, 770, 550, "confirmed"),
            _svg_arrow(520, 550, 425, 550),
            _svg_arrow(215, 550, 185, 550),
        ]
    )
    return _svg_base(1200, 720, "Normalny przeplyw rezerwacji", content)


def _diagram_cancel_flow() -> str:
    content = "\n".join(
        [
            _svg_box(80, 120, 190, 80, "1. User/SPA", "Cancel booking", "#dbeafe", "#3b82f6"),
            _svg_box(330, 120, 205, 80, "2. FastAPI", "JWT + owner check", "#e0f2fe", "#0284c7"),
            _svg_box(610, 105, 250, 110, "3. Coordinator", "validate reservation\ncalculate refund", "#ede9fe", "#8b5cf6"),
            _svg_box(900, 95, 215, 90, "4. Inventory", "cancel_reservation", "#f1f5f9", "#64748b"),
            _svg_box(900, 245, 215, 90, "5. HotelActor", "status cancelled\navailable += 1", "#dcfce7", "#22c55e"),
            _svg_box(610, 425, 250, 90, "6. History + DB", "status cancelled\nrefund fields", "#dcfce7", "#16a34a"),
            _svg_box(330, 425, 205, 90, "7. Audit + Metrics", "RESERVATION_CANCELLED", "#fef3c7", "#f59e0b"),
            _svg_box(80, 425, 190, 90, "8. Response", "refund_percent\nrefund_amount", "#e0f2fe", "#0284c7"),
            _svg_arrow(270, 160, 330, 160),
            _svg_arrow(535, 160, 610, 160),
            _svg_arrow(860, 150, 900, 140),
            _svg_arrow(1005, 185, 1005, 245),
            _svg_arrow(900, 290, 860, 470),
            _svg_arrow(610, 470, 535, 470),
            _svg_arrow(330, 470, 270, 470),
        ]
    )
    return _svg_base(1200, 680, "Przeplyw anulowania rezerwacji", content)


def _diagram_data_model() -> str:
    content = "\n".join(
        [
            _svg_box(110, 135, 280, 255, "hotels", "hotel_id PK\nname\ncity\nrooms JSON\nupdated_at", "#dcfce7", "#22c55e"),
            _svg_box(480, 120, 310, 305, "reservations", "reservation_id PK\nuser_id\nhotel_id\nroom_type\nnights\ntotal_price\npayment_id\nstatus\ncreated_at\nrefund_percent\nrefund_amount", "#dbeafe", "#2563eb"),
            _svg_box(860, 150, 280, 255, "audit_logs", "id PK\nevent_id UNIQUE\nevent_type\nactor_id\nentity_id\ndetails JSON\noccurred_at", "#fef3c7", "#f59e0b"),
            _svg_arrow(390, 260, 480, 260, "hotel_id"),
            '<text x="430" y="245" text-anchor="middle" font-family="Segoe UI, Arial" font-size="14" fill="#475569">1:N</text>',
        ]
    )
    return _svg_base(1200, 650, "Model danych PostgreSQL", content)


def _diagram_events() -> str:
    content = "\n".join(
        [
            _svg_box(75, 120, 210, 85, "Logowanie", "LOGIN", "#e0f2fe", "#0284c7"),
            _svg_box(350, 120, 220, 85, "Admin", "HOTEL_UPSERTED", "#f1f5f9", "#64748b"),
            _svg_box(650, 105, 220, 115, "Hold", "HOLD_CREATED\nHOLD_RELEASED\nHOLD_CONFIRMED", "#ede9fe", "#8b5cf6"),
            _svg_box(930, 105, 220, 115, "Platnosc", "PAYMENT_SUCCESS\nPAYMENT_FAILED\nPAYMENT_REFUNDED", "#fee2e2", "#ef4444"),
            _svg_box(255, 350, 250, 105, "Rezerwacja", "RESERVATION_CREATED\nRESERVATION_CANCELLED", "#dcfce7", "#22c55e"),
            _svg_box(610, 350, 250, 105, "Awaria zwrotu", "REFUND_FAILED", "#fee2e2", "#ef4444"),
            _svg_box(430, 500, 300, 75, "AuditLogActor", "write_audit_log(entry)", "#fef3c7", "#f59e0b"),
            _svg_arrow(180, 205, 515, 500),
            _svg_arrow(460, 205, 540, 500),
            _svg_arrow(760, 220, 585, 500),
            _svg_arrow(1040, 220, 640, 500),
            _svg_arrow(380, 455, 540, 500),
            _svg_arrow(735, 455, 640, 500),
        ]
    )
    return _svg_base(1200, 620, "Typy zdarzen audytowych", content)


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
