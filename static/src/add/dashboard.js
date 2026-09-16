/** @odoo-module **/
/* Tablero ADD v2 (15 sep 2026): misma línea visual que SOM Analytics —
 * banda superior con presets de periodo, tarjetas KPI, gráficas Chart.js
 * con drill a la lista nativa y tema claro/oscuro. Los datos vienen de
 * som.add.document.dashboard_v2, ya agregados y con su dominio de drill. */
import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const SAT = { unknown: "No consultado", valid: "Vigente", cancelled: "Cancelado", not_found: "No encontrado", error: "Error SAT" };
const CONS = { ok: "Consistente", warning: "Con alertas", partial: "Validación parcial" };
const KINDS = { I: "Facturas (I)", E: "Ajustes (E)", P: "Pagos (P)", T: "Traslados (T)" };
const PRESETS = [["month", "Mes"], ["quarter", "Trimestre"], ["year", "Año"], ["12m", "12 meses"], ["all", "Todo"]];
const THEME_KEY = "som.add.dash.theme";

export class AddDashboard extends Component {
    static template = "som_add.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.root = useRef("root");
        this.charts = {};
        this.presets = PRESETS;
        let theme = "light";
        try { theme = localStorage.getItem(THEME_KEY) || "light"; } catch { /* sin almacenamiento local */ }
        this.state = useState({
            boot: { companies: [], today: "" },
            filters: { companies: [], direction: "received", start: "", end: "", date_basis: "fiscal_date", measure: "base", valid_only: false },
            preset: "year", currency: "", data: null, busy: false, error: "", theme,
        });
        this.generation = 0;
        this.alive = true;
        onWillStart(async () => {
            try {
                await loadJS("/web/static/lib/Chart/Chart.js");
                this.state.boot = await this.orm.call("som.add.document", "bootstrap", []);
                this.state.filters.companies = [this.state.boot.company_id];
                this.applyPreset("year", false);
                await this.load();
            } catch (error) { this.fail(error); }
        });
        useEffect(() => { this.renderCharts(); }, () => [this.state.data, this.state.currency, this.state.theme]);
        onWillUnmount(() => { this.alive = false; this.generation++; this.destroyCharts(); });
    }

    // ── Periodo ─────────────────────────────────────────────────────
    applyPreset(preset, reload = true) {
        const today = new Date(`${this.state.boot.today || new Date().toISOString().slice(0, 10)}T12:00:00`);
        const iso = (d) => d.toISOString().slice(0, 10);
        const f = this.state.filters;
        this.state.preset = preset;
        if (preset === "month") { f.start = iso(new Date(today.getFullYear(), today.getMonth(), 1)); f.end = iso(today); }
        else if (preset === "quarter") { const q = Math.floor(today.getMonth() / 3) * 3; f.start = iso(new Date(today.getFullYear(), q, 1)); f.end = iso(today); }
        else if (preset === "year") { f.start = iso(new Date(today.getFullYear(), 0, 1)); f.end = iso(today); }
        else if (preset === "12m") { f.start = iso(new Date(today.getFullYear() - 1, today.getMonth() + 1, 1)); f.end = iso(today); }
        else { f.start = ""; f.end = ""; }
        if (reload) this.load();
    }
    customDates() { this.state.preset = ""; this.load(); }
    changeCompany(event) {
        const ids = [...event.target.selectedOptions].map(o => Number(o.value)).filter(Boolean);
        this.state.filters.companies = ids.length ? ids : [this.state.boot.company_id];
        this.load();
    }
    setDirection(direction) { this.state.filters.direction = direction; this.load(); }
    setCurrency(cur) { this.state.currency = cur; }
    toggleTheme() {
        this.state.theme = this.state.theme === "dark" ? "light" : "dark";
        try { localStorage.setItem(THEME_KEY, this.state.theme); } catch { /* sin almacenamiento local */ }
    }

    // ── Datos ───────────────────────────────────────────────────────
    async load() {
        const generation = ++this.generation;
        this.state.busy = true; this.state.error = "";
        try {
            const data = await this.orm.call("som.add.document", "dashboard_v2", [this.state.filters]);
            if (generation !== this.generation || !this.alive) return;
            this.state.data = data;
            if (!data.currencies.includes(this.state.currency)) this.state.currency = data.primary;
        } catch (error) { this.fail(error); }
        finally { if (this.alive && generation === this.generation) this.state.busy = false; }
    }
    fail(error) {
        if (!this.alive) return;
        this.state.error = error?.data?.message || error.message || "No se pudo consultar el tablero.";
        this.state.data = null;
    }

    // ── Accesores ───────────────────────────────────────────────────
    get d() { return this.state.data; }
    get cur() { return this.state.currency || this.d?.primary || "MXN"; }
    get k() { return this.d?.kpis || {}; }
    get isIssued() { return this.state.filters.direction === "issued"; }
    get directionLabel() { return this.isIssued ? "Emitidos" : this.state.filters.direction === "received" ? "Recibidos" : "Ambas perspectivas"; }
    get counterpartLabel() { return this.isIssued ? "clientes" : this.state.filters.direction === "received" ? "proveedores" : "contrapartes"; }
    per(section) { return (this.d?.[section] || {})[this.cur]; }
    get monthly() {
        const rows = Object.values(this.per("monthly") || {}).filter(r => r.month !== "Sin fecha");
        return rows.sort((a, b) => a.month.localeCompare(b.month));
    }
    get counterparts() { return this.per("counterparts") || { rows: [], others: 0, total: 0 }; }
    get methods() { return (this.per("methods") || []).filter(m => m.kind === "I"); }
    get taxes() { return this.per("taxes") || []; }
    get concepts() { return this.per("concepts") || []; }
    get categories() { return this.per("categories") || []; }
    get paymentRows() {
        return Object.values(this.per("payments") || {}).filter(r => r.month !== "Sin fecha").sort((a, b) => a.month.localeCompare(b.month));
    }
    get ppdAmount() { return (this.k.ppd_open?.amounts || {})[this.cur] || 0; }
    satLabel(s) { return SAT[s] || s; }
    consLabel(s) { return CONS[s] || s; }
    kindLabel(k) { return KINDS[k] || k; }
    monthLabel(m) {
        const [y, mo] = String(m).split("-");
        return new Intl.DateTimeFormat("es-MX", { month: "short", year: "2-digit" }).format(new Date(Number(y), Number(mo) - 1, 1)).replace(".", "");
    }

    // ── Formato ─────────────────────────────────────────────────────
    fmt(value, digits = 2) { return new Intl.NumberFormat("es-MX", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value || 0)); }
    fmtK(value) {
        const n = Number(value || 0), a = Math.abs(n);
        if (a >= 1e9) return `${this.fmt(n / 1e9, 2)} MM`;
        if (a >= 1e6) return `${this.fmt(n / 1e6, 2)} M`;
        if (a >= 1e4) return `${this.fmt(n / 1e3, 1)} k`;
        return this.fmt(n, a >= 100 ? 0 : 2);
    }
    pct(value) { return `${this.fmt(value, 1)}%`; }

    // ── Drill ───────────────────────────────────────────────────────
    drill(domain, model = "som.add.document", name = "ADD · detalle") {
        if (!domain) return;
        return this.action.doAction({ type: "ir.actions.act_window", name, res_model: model,
            views: [[false, "list"], [false, "form"]], domain, context: { active_test: false } });
    }
    openBatch(id) {
        return this.action.doAction({ type: "ir.actions.act_window", name: "Lote ADD", res_model: "som.add.batch", res_id: id, views: [[false, "form"]] });
    }
    openExplorer(direction) {
        return this.action.doAction(direction === "issued" ? "account_statement_report.action_add_issued" : "account_statement_report.action_add_received");
    }

    // ── Gráficas ────────────────────────────────────────────────────
    destroyCharts() { for (const c of Object.values(this.charts)) { try { c.destroy(); } catch { /* ya destruida */ } } this.charts = {}; }
    palette() {
        const css = getComputedStyle(this.root.el);
        const v = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
        return { blue: v("--blue", "#0b57d0"), sky: v("--sky", "#0284c7"), green: v("--green", "#059669"), amber: v("--amber", "#d97706"),
            red: v("--red", "#dc2626"), txt: v("--mut", "#334155"), line: v("--line", "rgba(15,23,42,.1)"), violet: "#7c3aed", teal: "#0d9488", slate: "#64748b" };
    }
    chart(name, config) {
        const canvas = this.root.el?.querySelector(`canvas[data-chart="${name}"]`);
        if (!canvas || typeof Chart === "undefined") return;
        this.charts[name] = new Chart(canvas, config);
    }
    base(p, extra = {}) {
        return { responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
            plugins: { legend: { labels: { color: p.txt, boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: (c) => ` ${c.dataset.label || ""}: ${this.fmt(c.parsed.y ?? c.parsed.x ?? c.parsed)}` } } },
            scales: { x: { ticks: { color: p.txt, font: { size: 11 } }, grid: { color: p.line } }, y: { ticks: { color: p.txt, font: { size: 11 }, callback: (v) => this.fmtK(v) }, grid: { color: p.line } } }, ...extra };
    }
    donut(name, labels, values, colors, domains, p) {
        this.chart(name, { type: "doughnut", data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, cutout: "62%", animation: { duration: 250 },
                plugins: { legend: { position: "right", labels: { color: p.txt, boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: (c) => ` ${c.label}: ${this.fmt(c.parsed, Number.isInteger(c.parsed) ? 0 : 2)}` } } },
                onClick: (_e, els) => { if (els.length) this.drill(domains[els[0].index]); } } });
    }
    renderCharts() {
        this.destroyCharts();
        if (!this.d || !this.root.el) return;
        const p = this.palette();
        const bar = (color, alpha = "cc") => color.length === 7 ? color + alpha : color;

        const monthly = this.monthly;
        this.chart("monthly", { type: "bar",
            data: { labels: monthly.map(r => this.monthLabel(r.month)), datasets: [
                { label: "Facturas I", data: monthly.map(r => r.I), backgroundColor: bar(p.blue), borderRadius: 4, order: 2 },
                { label: "Ajustes E", data: monthly.map(r => r.E), backgroundColor: bar(p.red, "99"), borderRadius: 4, order: 3 },
                { label: "Pagos P", data: monthly.map(r => r.P), type: "line", borderColor: p.green, backgroundColor: p.green, tension: .3, pointRadius: 3, order: 1 },
            ] },
            options: { ...this.base(p), onClick: (_e, els) => { if (els.length) this.drill(monthly[els[0].index].domain); } } });

        const cp = this.counterparts.rows;
        this.chart("counterparts", { type: "bar",
            data: { labels: cp.map(r => (r.name || r.rfc).slice(0, 28)), datasets: [{ label: `Facturas I ${this.cur}`, data: cp.map(r => r.value), backgroundColor: bar(p.sky), borderRadius: 4 }] },
            options: { ...this.base(p, { indexAxis: "y" }), scales: { x: { ticks: { color: p.txt, callback: (v) => this.fmtK(v) }, grid: { color: p.line } }, y: { ticks: { color: p.txt, font: { size: 11 } }, grid: { display: false } } },
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${this.fmt(c.parsed.x)} · ${this.pct(cp[c.dataIndex].percent)} · acum. ${this.pct(cp[c.dataIndex].cumulative)}` } } },
                onClick: (_e, els) => { if (els.length) this.drill(cp[els[0].index].domain); } } });

        const kinds = this.d.kinds || [];
        this.donut("kinds", kinds.map(r => this.kindLabel(r.kind)), kinds.map(r => r.count), [p.blue, p.red, p.green, p.amber], kinds.map(r => r.domain), p);
        const methods = this.methods;
        this.donut("methods", methods.map(r => r.method), methods.map(r => r.amount), [p.teal, p.violet, p.slate], methods.map(r => r.domain), p);
        const sat = this.d.sat || [];
        const satColor = { valid: p.green, unknown: p.slate, cancelled: p.red, not_found: p.amber, error: p.violet };
        this.donut("sat", sat.map(r => this.satLabel(r.state)), sat.map(r => r.count), sat.map(r => satColor[r.state] || p.slate), sat.map(r => r.domain), p);
        const cons = this.d.consistency || [];
        const consColor = { ok: p.green, warning: p.amber, partial: p.red };
        this.donut("consistency", cons.map(r => this.consLabel(r.state)), cons.map(r => r.count), cons.map(r => consColor[r.state] || p.slate), cons.map(r => r.domain), p);

        const taxes = this.taxes;
        this.chart("taxes", { type: "bar",
            data: { labels: taxes.map(r => `${r.label} · ${r.kind}`), datasets: [{ label: this.cur, data: taxes.map(r => r.amount), backgroundColor: taxes.map(r => bar(r.nature === "withholding" ? p.amber : p.blue)), borderRadius: 4 }] },
            options: { ...this.base(p), plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${this.fmt(c.parsed.y)} ${this.cur}` } } },
                onClick: (_e, els) => { if (els.length) this.drill(taxes[els[0].index].domain, "som.add.tax", "ADD · impuestos"); } } });

        const pay = this.paymentRows;
        this.chart("payments", { type: "bar",
            data: { labels: pay.map(r => this.monthLabel(r.month)), datasets: [
                { label: `Monto pagado ${this.cur}`, data: pay.map(r => r.amount), backgroundColor: bar(p.green), borderRadius: 4, yAxisID: "y" },
                { label: "Pagos (n)", data: pay.map(r => r.count), type: "line", borderColor: p.amber, backgroundColor: p.amber, tension: .3, pointRadius: 3, yAxisID: "y2" },
            ] },
            options: { ...this.base(p), scales: { x: { ticks: { color: p.txt }, grid: { color: p.line } }, y: { ticks: { color: p.txt, callback: (v) => this.fmtK(v) }, grid: { color: p.line } }, y2: { position: "right", ticks: { color: p.txt, precision: 0 }, grid: { display: false } } },
                onClick: (_e, els) => { if (els.length) this.drill(pay[els[0].index].domain, "som.add.payment", "ADD · pagos"); } } });

        const concepts = this.concepts;
        this.chart("concepts", { type: "bar",
            data: { labels: concepts.map(r => `${r.code}${r.unit ? " · " + r.unit : ""}`), datasets: [{ label: this.cur, data: concepts.map(r => r.amount), backgroundColor: bar(p.violet, "b3"), borderRadius: 4 }] },
            options: { ...this.base(p, { indexAxis: "y" }), scales: { x: { ticks: { color: p.txt, callback: (v) => this.fmtK(v) }, grid: { color: p.line } }, y: { ticks: { color: p.txt, font: { size: 11 } }, grid: { display: false } } },
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${this.fmt(c.parsed.x)} ${this.cur} · ${concepts[c.dataIndex].count} partidas` } } },
                onClick: (_e, els) => { if (els.length) this.drill(concepts[els[0].index].domain, "som.add.concept", "ADD · conceptos"); } } });
    }
}

registry.category("actions").add("som_add.dashboard", AddDashboard);
