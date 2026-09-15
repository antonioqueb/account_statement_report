/** @odoo-module **/
import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const COLUMNS = [
    ["company_id", "Compañía"], ["fiscal_date", "Fecha fiscal"], ["counterparty", "Contraparte"], ["rfc", "RFC"],
    ["folio", "Serie / folio"], ["kind", "Tipo"], ["currency", "Moneda"], ["subtotal", "Subtotal"],
    ["discount", "Descuento"], ["vat", "IVA global"], ["withheld", "Retenciones"], ["total", "Total"],
    ["method", "Método"], ["complements", "Complementos"], ["sat_state", "Estado SAT"], ["consistency", "Consistencia"],
    ["uuid", "UUID"], ["stamp_date", "Timbrado"], ["create_date", "Carga"], ["reference", "Referencia"], ["classification", "Categoría"], ["labels", "Etiquetas"],
];
const AMOUNTS = new Set(["subtotal", "discount", "vat", "withheld", "total"]);
const SAT = { unknown: "No consultado", valid: "Vigencia confirmada", cancelled: "Cancelado confirmado", not_found: "No encontrado", error: "Error de consulta" };
const CONSISTENCY = { ok: "Consistente", warning: "Con alertas", partial: "Validación parcial" };

export class AddExplorer extends Component {
    static template = "som_add.Explorer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.files = useRef("files");
        this.mode = this.props.action?.params?.mode || "received";
        this.columns = COLUMNS;
        this.tabs = [["summary", "Resumen"], ["concepts", "Conceptos"], ["taxes", "Impuestos"], ["payments", "Pagos y relaciones"],
            ["complements", "Complementos"], ["xml", "XML original"], ["trace", "Trazabilidad"]];
        this.state = useState({
            boot: { companies: [] }, filters: { companies: [], direction: this.mode === "dashboard" ? "received" : this.mode,
                q: "", start: "", end: "", currency: "", kind: "", method: "", form: "", use: "", complement: "", tax: "",
                sat: "", consistency: "", batch: "", classification: "", label: "", date_basis: "fiscal_date", measure: "base", valid_only: false, archived: false,
                net: false, mxn: false, compare: false },
            rows: [], count: 0, totals: [], domain: [], dashboard: null, offset: 0, limit: 50,
            order: "fiscal_date desc, id desc", selected: [], detail: null, tab: "summary", fullscreen: false,
            busy: false, error: "", advanced: false, showColumns: false, upload: false, uploading: false,
            uploadDirection: this.mode === "issued" ? "issued" : "received", uploadCompany: 0,
            uploadFiles: [], fileProgress: 0, uploadIndex: 0, batch: null,
            visible: window.matchMedia("(max-width: 767px)").matches ? ["fiscal_date", "counterparty", "total", "currency", "kind"] : COLUMNS.slice(0, 16).map(c => c[0]),
            widths: {}, saved: [], saveName: "", archiveReason: "",
        });
        this.generation = 0;
        this.detailGeneration = 0;
        this.alive = true;
        onWillStart(async () => {
            try {
                this.state.boot = await this.orm.call("som.add.document", "bootstrap", []);
                this.state.filters.companies = [this.state.boot.company_id];
                this.state.uploadCompany = this.state.boot.company_id;
                this.storageKey = `som.add.ui.${this.state.boot.uid}`;
                try {
                    const prefs = JSON.parse(localStorage.getItem(this.storageKey) || "{}");
                    this.state.visible = prefs.visible?.filter(k => COLUMNS.some(c => c[0] === k)) || this.state.visible;
                    this.state.widths = prefs.widths || {};
                    this.state.saved = prefs.saved || [];
                } catch { /* Invalid display preferences are safely reset. */ }
                await this.load();
            } catch (error) { this.fail(error); }
        });
        onWillUnmount(() => { this.alive = false; this.generation++; this.detailGeneration++; });
    }

    get title() { return this.mode === "dashboard" ? "Tablero documental" : (this.mode === "issued" ? "Emitidos" : "Recibidos"); }
    get visibleColumns() { return COLUMNS.filter(c => this.state.visible.includes(c[0])); }
    get sections() {
        const sections = new Map();
        for (const row of this.state.dashboard?.rows || []) {
            const key = `${row.section} · ${row.unit}`;
            if (!sections.has(key)) sections.set(key, { name: key, rows: [] });
            sections.get(key).rows.push(row);
        }
        return [...sections.values()].map(section => ({ ...section,
            maximum: Math.max(1, ...section.rows.map(r => Math.abs(r.value))) }));
    }
    get selectionTotals() {
        const groups = {};
        for (const row of this.state.rows.filter(r => this.state.selected.includes(r.id) && ["I", "E"].includes(r.kind))) {
            const key = `${row.kind} · ${row.currency}`;
            groups[key] = (groups[key] || 0) + row.total;
        }
        return Object.entries(groups);
    }
    get pageEnd() { return Math.min(this.state.count, this.state.offset + this.state.limit); }
    get parsed() { return this.state.detail?.parsed || {}; }
    get isIssued() { return this.state.filters.direction === "issued"; }
    entries(value) { return Object.entries(value || {}); }
    pretty(value) { return JSON.stringify(value, null, 2); }
    fmt(value, digits = 2) { return new Intl.NumberFormat("es-MX", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value || 0)); }
    date(value) {
        if (!value) return "—";
        return new Intl.DateTimeFormat("es-MX", { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${String(value).slice(0, 10)}T12:00:00`));
    }
    cell(row, key) {
        if (key === "counterparty") return this.isIssued ? row.receiver_name : row.emitter_name;
        if (key === "rfc") return this.isIssued ? row.receiver_rfc : row.emitter_rfc;
        if (key === "company_id") return row.company_id?.[1] || "—";
        if (key === "folio") return `${row.series || ""} ${row.folio || ""}`.trim();
        if (AMOUNTS.has(key)) {
            let digits = 2;
            try { digits = new Intl.NumberFormat("es-MX", { style: "currency", currency: row.currency }).resolvedOptions().maximumFractionDigits; } catch { /* Preserve sensible display for unknown currency codes. */ }
            return this.fmt(row[key], digits);
        }
        if (["fiscal_date", "stamp_date", "create_date"].includes(key)) return this.date(row[key]);
        if (key === "sat_state") return SAT[row[key]];
        if (key === "consistency") return CONSISTENCY[row[key]];
        return row[key] || "—";
    }
    numeric(key) { return AMOUNTS.has(key); }
    fail(error) {
        if (!this.alive) return;
        this.state.error = error?.data?.message || error.message || "No se pudo completar la operación.";
        this.state.rows = []; this.state.totals = []; this.state.dashboard = null;
        this.state.detail = null; this.state.selected = []; this.state.count = 0;
    }
    async load(reset = false) {
        const generation = ++this.generation;
        this.detailGeneration++;
        if (reset) this.state.offset = 0;
        this.state.busy = true; this.state.error = ""; this.state.detail = null; this.state.selected = [];
        this.state.rows = []; this.state.dashboard = null; this.state.totals = [];
        try {
            // Every request is fresh and authorized on the server. No document cache.
            if (this.mode === "dashboard") {
                const result = await this.orm.call("som.add.document", "dashboard", [this.state.filters]);
                if (generation === this.generation && this.alive) this.state.dashboard = result;
            } else {
                const result = await this.orm.call("som.add.document", "explore", [this.state.filters, this.state.offset, this.state.limit, this.state.order]);
                if (generation === this.generation && this.alive) Object.assign(this.state, result);
            }
        } catch (error) { if (generation === this.generation) this.fail(error); }
        finally { if (generation === this.generation && this.alive) this.state.busy = false; }
    }
    async changeCompany(event) {
        this.state.filters.companies = [...event.target.selectedOptions].map(o => Number(o.value));
        await this.load(true);
    }
    async page(step) {
        this.state.offset = Math.max(0, this.state.offset + step * this.state.limit);
        await this.load();
    }
    async sort(key) {
        key = key === "counterparty" ? (this.isIssued ? "receiver_name" : "emitter_name") : key;
        key = key === "rfc" ? (this.isIssued ? "receiver_rfc" : "emitter_rfc") : key;
        const dir = this.state.order.startsWith(`${key} asc`) ? "desc" : "asc";
        this.state.order = `${key} ${dir}, id desc`;
        await this.load(true);
    }
    toggleSelect(id) { this.state.selected = this.state.selected.includes(id) ? this.state.selected.filter(i => i !== id) : [...this.state.selected, id]; }
    selectPage(event) { this.state.selected = event.target.checked ? this.state.rows.map(r => r.id) : []; }
    async inspect(id) {
        const generation = ++this.detailGeneration;
        this.state.detail = null;
        try {
            const result = await this.orm.call("som.add.document", "detail", [[id]]);
            if (generation === this.detailGeneration && this.alive) this.state.detail = result;
        } catch (error) { this.fail(error); }
    }
    async adjacent(step) {
        const index = this.state.rows.findIndex(r => r.id === this.state.detail?.id) + step;
        if (index >= 0 && index < this.state.rows.length) return this.inspect(this.state.rows[index].id);
        if ((step < 0 && this.state.offset > 0) || (step > 0 && this.pageEnd < this.state.count)) {
            await this.page(step);
            const row = step < 0 ? this.state.rows.at(-1) : this.state.rows[0];
            if (row) await this.inspect(row.id);
        }
    }
    async copy(value) {
        try { await navigator.clipboard.writeText(value); this.notification.add("Copiado", { type: "success" }); }
        catch { this.notification.add("No se pudo acceder al portapapeles. Puede seleccionar y copiar el texto.", { type: "warning" }); }
    }
    persist() {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify({ visible: this.state.visible, widths: this.state.widths, saved: this.state.saved }));
        } catch {
            this.notification.add("El navegador no permite guardar preferencias locales.", { type: "warning" });
        }
    }
    toggleColumn(key) {
        this.state.visible = this.state.visible.includes(key) ? this.state.visible.filter(k => k !== key) : [...this.state.visible, key];
        this.persist();
    }
    resize(event, key) {
        event.preventDefault(); event.stopPropagation();
        const start = event.clientX, initial = event.target.closest("th").offsetWidth;
        const move = e => { this.state.widths[key] = Math.max(85, Math.min(650, initial + e.clientX - start)); };
        const end = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end); this.persist(); };
        window.addEventListener("pointermove", move); window.addEventListener("pointerup", end);
    }
    saveView() {
        if (!this.state.saveName.trim()) return;
        this.state.saved.push({ name: this.state.saveName.trim(), filters: JSON.parse(JSON.stringify(this.state.filters)), order: this.state.order });
        this.state.saveName = ""; this.persist();
    }
    async restoreView(event) {
        if (event.target.value === "") return;
        const saved = this.state.saved[Number(event.target.value)];
        const companies = saved.filters.companies.filter(id => this.state.boot.companies.some(c => c.id === id));
        Object.assign(this.state.filters, saved.filters, { companies: companies.length ? companies : [this.state.boot.company_id] });
        if (this.mode !== "dashboard") this.state.filters.direction = this.mode;
        this.state.order = saved.order;
        await this.load(true);
    }
    openNative(model = "som.add.document", domain = this.state.domain, group = false) {
        return this.action.doAction({ type: "ir.actions.act_window", name: "ADD · detalle del alcance", res_model: model,
            views: [[false, "list"], [false, "form"]], domain, context: { active_test: false, ...(group ? { group_by: group } : {}) } });
    }
    drill(row) { return this.openNative(row.model, row.domain); }
    group(event) { if (event.target.value) this.openNative("som.add.document", this.state.domain, event.target.value); }
    openUpload(direction) {
        this.state.upload = true; this.state.uploadFiles = []; this.state.batch = null;
        this.state.uploadDirection = direction; this.state.uploadCompany = this.state.filters.companies[0];
    }
    picked(event) { this.state.uploadFiles = [...event.target.files]; }
    dropped(event) { event.preventDefault(); if (!this.state.uploading) this.state.uploadFiles = [...event.dataTransfer.files]; }
    async uploadFile(batchId, file) {
        const data = new FormData();
        data.set("csrf_token", odoo.csrf_token); data.set("batch_id", batchId);
        data.set("companies", JSON.stringify([this.state.uploadCompany])); data.set("file", file);
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/som/add/upload");
            xhr.upload.onprogress = event => { if (event.lengthComputable && this.alive) this.state.fileProgress = Math.round(100 * event.loaded / event.total); };
            xhr.onload = () => {
                let response;
                try { response = JSON.parse(xhr.responseText); } catch {
                    const incomplete = xhr.status === 400 && /CSRF|Session expired/i.test(xhr.responseText || "");
                    reject(new Error(incomplete
                        ? `El archivo "${file.name}" (${Math.round(file.size / 1048576)} MB) llegó incompleto al servidor: probablemente seguía creándose o descargándose al enviarlo. Espere a que termine, vuelva a seleccionarlo y reintente.`
                        : `La carga de "${file.name}" (${Math.round(file.size / 1048576)} MB) fue rechazada por el servidor (HTTP ${xhr.status}). Si el archivo supera el máximo configurado, divídalo en ZIP más pequeños.`)); return;
                }
                if (xhr.status >= 400 || response.error) reject(new Error(response.error || "Carga no autorizada."));
                else resolve(response);
            };
            xhr.onerror = () => reject(new Error("Conexión interrumpida. El lote conserva los archivos recibidos."));
            xhr.send(data);
        });
    }
    async importFiles() {
        if (!this.state.uploadFiles.length || this.state.uploading) return;
        this.state.uploading = true; this.state.error = "";
        try {
            const batchId = await this.orm.call("som.add.batch", "begin", [Number(this.state.uploadCompany), this.state.uploadDirection]);
            this.state.batch = { id: batchId, pending_count: 0, progress: 0 };
            for (let i = 0; i < this.state.uploadFiles.length && this.alive && this.state.batch.state !== "cancelled"; i++) {
                this.state.uploadIndex = i + 1; this.state.fileProgress = 0;
                this.state.batch = await this.uploadFile(batchId, this.state.uploadFiles[i]);
            }
            if (!this.alive || this.state.batch.state === "cancelled") return;
            this.state.batch = await this.orm.call("som.add.batch", "seal", [[batchId]]);
            while (this.alive && this.state.batch.state === "processing") {
                this.state.batch = await this.orm.call("som.add.batch", "process_block", [[batchId]]);
            }
            if (this.alive) await this.load();
        } catch (error) { if (this.alive) this.state.error = error?.data?.message || error.message; }
        finally { if (this.alive) this.state.uploading = false; }
    }
    async cancelBatch() {
        if (!this.state.batch?.id) return;
        try { this.state.batch = await this.orm.call("som.add.batch", "cancel", [[this.state.batch.id]]); }
        catch (error) { this.notification.add(error?.data?.message || error.message, { type: "warning" }); }
    }
    openBatch() {
        return this.action.doAction({ type: "ir.actions.act_window", name: "Lote ADD", res_model: "som.add.batch",
            res_id: this.state.batch.id, views: [[false, "form"]] });
    }
    async exportFile(format, current = false) {
        const body = new FormData();
        body.set("csrf_token", odoo.csrf_token); body.set("companies", JSON.stringify(this.state.filters.companies));
        const filters = current ? { companies: [this.state.detail.company_id[0]], direction: "both", archived: !this.state.detail.active } : this.state.filters;
        body.set("filters", JSON.stringify(filters));
        body.set("ids", JSON.stringify(current ? [this.state.detail.id] : this.state.selected));
        body.set("format", format);
        const columns = this.state.visible.flatMap(key => key === "counterparty" ? [this.isIssued ? "receiver_name" : "emitter_name"] : key === "rfc" ? [this.isIssued ? "receiver_rfc" : "emitter_rfc"] : key === "folio" ? ["series", "folio"] : [key]);
        body.set("columns", JSON.stringify(format === "xlsx" ? [] : columns));
        try {
            const response = await fetch("/som/add/export", { method: "POST", body });
            if (!response.ok) throw new Error(`No se pudo exportar (${response.status}). Compruebe permisos y límites: 10,000 documentos / 100 MiB.`);
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement("a"); link.href = url;
            link.download = current ? `${this.state.detail.uuid}.xml` : `ADD.${format}`;
            link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { this.notification.add(error.message, { type: "danger" }); }
    }
    async saveNotes() {
        const d = this.state.detail;
        try {
            await this.orm.write("som.add.document", [d.id], { notes: d.notes || "", reference: d.reference || "", classification: d.classification || "", labels: d.labels || "" });
            this.notification.add("Clasificación guardada y auditada.", { type: "success" });
        } catch (error) { this.fail(error); }
    }
    async reprocess() {
        const id = this.state.detail.id;
        try { await this.orm.call("som.add.document", "reprocess", [[id]]); await this.inspect(id); }
        catch (error) { this.fail(error); }
    }
    async archive() {
        if (!this.state.archiveReason.trim()) return;
        try {
            await this.orm.call("som.add.document", "archive_document", [[this.state.detail.id], this.state.archiveReason]);
            this.state.archiveReason = ""; await this.load();
        } catch (error) { this.fail(error); }
    }
}
registry.category("actions").add("som_add.explorer", AddExplorer);
