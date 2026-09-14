from pathlib import Path
import re

p = Path("app.py")
s = p.read_text(encoding="utf-8")

if "RDCN performance layer v2" in s:
    raise SystemExit("Performance layer already applied")

s = s.replace(
    "import os, re, csv, sys, math, time, hashlib, zipfile, tempfile, shutil, threading, traceback",
    "import os, re, csv, sys, math, time, hashlib, zipfile, tempfile, shutil, threading, traceback\nfrom concurrent.futures import ThreadPoolExecutor, as_completed"
)
s = s.replace('APP_VERSION = "1.1.0"', 'APP_VERSION = "2.0.0"  # RDCN performance layer v2')

old_render = '''def render_footer(page, top=FOOTER_TOP):
    pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    y = max(0, int(gray.shape[0] * top))
    return gray[y:, :]
'''
new_render = '''def footer_rect(page, top=FOOTER_TOP):
    rect = page.rect
    return fitz.Rect(rect.x0, rect.y0 + rect.height * top, rect.x1, rect.y1)


def render_footer(page, top=FOOTER_TOP):
    # Rendu direct de la seule zone utile : moins de mémoire et beaucoup moins de pixels.
    pix = page.get_pixmap(
        matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE),
        clip=footer_rect(page, top),
        alpha=False,
    )
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
'''
if old_render not in s:
    raise SystemExit("render_footer anchor not found")
s = s.replace(old_render, new_render)

old_primary = '''def page_primary(page):
    crop = render_footer(page)
    variant = preprocess_variants(crop)[0][1]
    txt = ocr_image(variant, 6)
    vals = {}
    ev = {}
    for m in METRICS:
        v, e = find_metric(txt, m)
        vals[m] = v; ev[m] = e
    return vals, ev
'''
new_primary = '''def page_primary(page):
    # Priorité au texte natif du PDF : exact et presque instantané.
    # L'OCR historique reste le filet de sécurité dès qu'une valeur manque.
    native = page.get_text("text", clip=footer_rect(page), sort=True) or ""
    vals = {}
    ev = {}
    for metric in METRICS:
        value, evidence = find_metric(native, metric)
        vals[metric] = value
        ev[metric] = evidence

    if all(vals.get(metric) is not None for metric in ("head", "night", "work", "amplitude", "break")):
        return vals, ev

    crop = render_footer(page)
    variant = preprocess_variants(crop)[0][1]
    txt = ocr_image(variant, 6)
    for metric in METRICS:
        if vals.get(metric) is None:
            value, evidence = find_metric(txt, metric)
            vals[metric] = value
            ev[metric] = evidence
    return vals, ev
'''
if old_primary not in s:
    raise SystemExit("page_primary anchor not found")
s = s.replace(old_primary, new_primary)

def replace_method(source, name, replacement):
    start = source.find(f"    def {name}(")
    if start < 0:
        raise SystemExit(f"method {name} not found")
    match = re.search(r"(?m)^    def [A-Za-z_][A-Za-z0-9_]*\\(", source[start + 1:])
    end = start + 1 + match.start() if match else source.find("\\n\\nif __name__", start)
    return source[:start] + replacement.rstrip() + "\\n\\n" + source[end:]

build_ui = r'''    def build_ui(self):
        self.configure(bg="#F3F6FA")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Segoe UI", 10), background="#F3F6FA", foreground="#172033")
        style.configure("Header.TFrame", background="#102A43")
        style.configure("HeaderTitle.TLabel", background="#102A43", foreground="white", font=("Segoe UI Semibold", 20))
        style.configure("HeaderSub.TLabel", background="#102A43", foreground="#B9CBE0", font=("Segoe UI", 10))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(16, 10), foreground="white", background="#007A78")
        style.map("Primary.TButton", background=[("active", "#006B69"), ("disabled", "#8FA9A8")])
        style.configure("Action.TButton", padding=(12, 9))
        style.configure("Card.TFrame", background="white", relief="flat")
        style.configure("CardTitle.TLabel", background="white", foreground="#64748B", font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background="white", foreground="#102A43", font=("Segoe UI Semibold", 17))
        style.configure("Treeview", rowheight=32, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", background="#E8EEF5", foreground="#243B53", font=("Segoe UI Semibold", 9), padding=(8, 9))
        style.map("Treeview", background=[("selected", "#CDEDEA")], foreground=[("selected", "#102A43")])
        style.configure("Status.TLabel", background="#102A43", foreground="#D9E6F2", padding=(12, 7))

        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 16))
        header.pack(fill="x")
        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="Contrôle des Bulletins de Service", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Lecture fiable PDF, ZIP et Excel • contrôle paie RDCN", style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))
        self.add_button = ttk.Button(header, text="＋ Importer des fichiers", style="Primary.TButton", command=self.choose_files)
        self.add_button.pack(side="right")

        toolbar = ttk.Frame(self, padding=(18, 12, 18, 8))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Corriger la sélection", style="Action.TButton", command=self.edit_selected).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Voir le pied de page", style="Action.TButton", command=self.show_footers).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Exporter CSV", style="Action.TButton", command=self.export_csv).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Vider", style="Action.TButton", command=self.clear).pack(side="left", padx=6)
        self.cancel_button = ttk.Button(toolbar, text="Arrêter l’analyse", style="Action.TButton", command=self.cancel_processing, state="disabled")
        self.cancel_button.pack(side="left", padx=6)

        search_box = ttk.Frame(toolbar)
        search_box.pack(side="right")
        ttk.Label(search_box, text="Filtrer").pack(side="left", padx=(0, 7))
        self.filter_var = tk.StringVar()
        search = ttk.Entry(search_box, textvariable=self.filter_var, width=30)
        search.pack(side="left")
        self.filter_job = None
        self.filter_var.trace_add("write", lambda *_: self.schedule_filter())

        cards = ttk.Frame(self, padding=(18, 2, 18, 10))
        cards.pack(fill="x")
        self.card_total = tk.StringVar(value="0")
        self.card_ok = tk.StringVar(value="0")
        self.card_review = tk.StringVar(value="0")
        self.card_work = tk.StringVar(value="0h00")
        for col, (label, variable) in enumerate((
            ("JOURNÉES CHARGÉES", self.card_total),
            ("VALIDÉES / CONFIRMÉES", self.card_ok),
            ("À CONTRÔLER", self.card_review),
            ("TRAVAIL EFFECTIF", self.card_work),
        )):
            card = ttk.Frame(cards, style="Card.TFrame", padding=(16, 11))
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 5, 0 if col == 3 else 5))
            ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=variable, style="CardValue.TLabel").pack(anchor="w", pady=(2, 0))
            cards.columnconfigure(col, weight=1)

        info = ttk.Frame(self, padding=(18, 0, 18, 8))
        info.pack(fill="x")
        self.summary = tk.StringVar(value="Aucune donnée chargée")
        ttk.Label(info, textvariable=self.summary, foreground="#52667A").pack(side="left")
        self.progress = ttk.Progressbar(info, mode="determinate", length=360)
        self.progress.pack(side="right")

        table = ttk.Frame(self, padding=(18, 0, 18, 12))
        table.pack(fill="both", expand=True)
        cols = ("date", "agent", "mat", "journee", "head", "night", "work", "source_type", "cross", "pages", "conf", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse")
        headings = {
            "date": "Date", "agent": "Agent", "mat": "Matricule", "journee": "Journée",
            "head": "Tête de train", "night": "Nuit 22h–07h", "work": "Travail effectif",
            "source_type": "Source", "cross": "Croisement", "pages": "Pages",
            "conf": "Confiance", "status": "Statut",
        }
        widths = {
            "date": 95, "agent": 185, "mat": 95, "journee": 90, "head": 112,
            "night": 108, "work": 120, "source_type": 72, "cross": 95,
            "pages": 58, "conf": 78, "status": 145,
        }
        for col in cols:
            self.tree.heading(col, text=headings[col], command=lambda x=col: self.sort_by(x))
            self.tree.column(col, width=widths[col], minwidth=55, anchor="w" if col in ("agent", "status") else "center")
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.tag_configure("ok", background="#EAF8F1")
        self.tree.tag_configure("review", background="#FFF7E6")
        self.tree.tag_configure("error", background="#FDECEC")
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())

        self.status_var = tk.StringVar(value="Prêt")
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(side="bottom", fill="x")'''

process_files = r'''    def process_files(self, files):
        self.cancel_event.clear()
        self.after(0, lambda: self.add_button.configure(state="disabled"))
        self.after(0, lambda: self.cancel_button.configure(state="normal"))
        pdfs = []
        excels = []
        errors = []
        for file_path in files:
            try:
                if file_path.lower().endswith(".zip"):
                    sub = os.path.join(self.tempdir, hashlib.md5(file_path.encode()).hexdigest())
                    os.makedirs(sub, exist_ok=True)
                    pdfs += [(path, os.path.basename(file_path)) for path in safe_extract_pdfs(file_path, sub)]
                elif file_path.lower().endswith(".pdf"):
                    pdfs.append((file_path, os.path.basename(file_path)))
                elif file_path.lower().endswith((".xlsx", ".xlsm")):
                    excels.append(file_path)
            except Exception as exc:
                errors.append(f"{os.path.basename(file_path)} : {exc}")

        unique = []
        for path, source in pdfs:
            if self.cancel_event.is_set():
                break
            try:
                digest = sha256(path)
                if digest not in self.seen_hashes:
                    self.seen_hashes.add(digest)
                    unique.append((path, source))
            except Exception as exc:
                errors.append(f"{os.path.basename(path)} : {exc}")

        total = len(unique) + len(excels)
        self.after(0, lambda: self.progress.configure(maximum=max(1, total), value=0))
        done = 0
        added = 0
        workers = max(1, min(4, (os.cpu_count() or 4) // 2))
        futures = {}
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rdcn-ocr")
        try:
            futures = {executor.submit(analyze_pdf, path, source): (path, source) for path, source in unique}
            for future in as_completed(futures):
                if self.cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                path, _ = futures[future]
                done += 1
                try:
                    result = future.result()
                    self.results.append(result)
                    added += 1
                except Exception as exc:
                    errors.append(f"{os.path.basename(path)} : {exc}")
                    traceback.print_exc()
                if done == total or done % 5 == 0:
                    self.after(0, self.refresh_tree)
                self.after(0, lambda value=done: self.progress.configure(value=value))
                self.after(0, self.set_status, f"Analyse {done}/{total} • {added} BS lus")
        finally:
            executor.shutdown(wait=not self.cancel_event.is_set(), cancel_futures=True)

        if not self.cancel_event.is_set():
            for excel_path in excels:
                done += 1
                try:
                    rows = read_excel_results(excel_path)
                    self.results.extend(rows)
                    added += len(rows)
                except Exception as exc:
                    errors.append(f"{os.path.basename(excel_path)} : {exc}")
                self.after(0, lambda value=done: self.progress.configure(value=value))

        reconcile_results(self.results)
        self.after(0, self.refresh_tree)
        self.after(0, self.refresh_summary)
        final_status = "Analyse arrêtée" if self.cancel_event.is_set() else f"Analyse terminée • {added} lignes/BS ajoutés"
        if errors:
            final_status += f" • {len(errors)} erreur(s)"
            self.after(0, messagebox.showwarning, "Fichiers non traités", "\\n".join(errors[:20]) + ("\\n…" if len(errors) > 20 else ""))
        self.after(0, self.set_status, final_status)
        self.after(0, lambda: self.add_button.configure(state="normal"))
        self.after(0, lambda: self.cancel_button.configure(state="disabled"))'''

refresh_tree = r'''    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        query = self.filter_var.get().strip().lower() if hasattr(self, "filter_var") else ""
        for index, result in enumerate(self.results):
            values = (
                result.date, result.agent, result.matricule, result.journee,
                result.head.value, result.night.value, result.work.value,
                getattr(result, "source_type", "BS"), getattr(result, "crosscheck", ""),
                result.pages, f"{result.global_confidence}%", result.status,
            )
            if query and query not in " ".join(str(value).lower() for value in values):
                continue
            if result.status in ("VALIDÉ", "VALIDÉ + EXCEL", "CONFIRMÉ PAR BS", "VALIDÉ MANUELLEMENT"):
                tag = "ok"
            elif result.status == "CONTRÔLE REQUIS":
                tag = "error"
            else:
                tag = "review"
            self.tree.insert("", "end", iid=str(index), values=values, tags=(tag,))
        self.refresh_summary()'''

refresh_summary = r'''    def refresh_summary(self):
        total = len(self.results)
        ok_statuses = ("VALIDÉ", "VALIDÉ + EXCEL", "CONFIRMÉ PAR BS", "VALIDÉ MANUELLEMENT")
        ok = sum(result.status in ok_statuses for result in self.results)
        review = total - ok
        chosen = []
        seen = set()
        for result in sorted(self.results, key=lambda x: 0 if getattr(x, "source_type", "BS") == "BS" else 1):
            key = identity_key(result)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            chosen.append(result)
        head = sum((result.head.minutes or 0) for result in chosen)
        night = sum((result.night.minutes or 0) for result in chosen)
        work = sum((result.work.minutes or 0) for result in chosen)
        self.summary.set(
            f"{len(chosen)} journées calculées • Tête {fmt_minutes(head)} • Nuit {fmt_minutes(night)} • Travail {fmt_minutes(work)}"
            if total else "Aucune donnée chargée"
        )
        self.card_total.set(str(len(chosen)))
        self.card_ok.set(str(ok))
        self.card_review.set(str(review))
        self.card_work.set(fmt_minutes(work) or "0h00")'''

helpers = r'''    def schedule_filter(self):
        if self.filter_job:
            self.after_cancel(self.filter_job)
        self.filter_job = self.after(180, self.refresh_tree)

    def cancel_processing(self):
        self.cancel_event.set()
        self.set_status("Arrêt demandé • fin du fichier en cours…")'''

clear_method = r'''    def clear(self):
        if hasattr(self, "cancel_event") and not self.cancel_event.is_set() and str(self.add_button["state"]) == "disabled":
            messagebox.showinfo("Analyse en cours", "Arrête d’abord l’analyse avant de vider les résultats.")
            return
        self.results.clear()
        self.seen_hashes.clear()
        self.refresh_tree()
        self.progress["value"] = 0
        self.set_status("Prêt")'''

s = replace_method(s, "build_ui", build_ui)
s = replace_method(s, "process_files", process_files)
s = replace_method(s, "refresh_tree", refresh_tree)
s = replace_method(s, "refresh_summary", refresh_summary)
s = replace_method(s, "clear", clear_method)

init_anchor = '''        self.sort_col=None; self.sort_rev=False
        self.protocol('WM_DELETE_WINDOW', self.on_close)
'''
init_replacement = '''        self.sort_col=None; self.sort_rev=False
        self.cancel_event = threading.Event()
        self.cancel_event.set()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
'''
if init_anchor not in s:
    raise SystemExit("init anchor not found")
s = s.replace(init_anchor, init_replacement)
s = s.replace(
    "    def selected_result(self):",
    helpers + "\\n\\n    def selected_result(self):"
)

p.write_text(s, encoding="utf-8")
print("RDCN v2 performance and UI patch applied")
