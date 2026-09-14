from pathlib import Path
import re

p = Path("app.py")
s = p.read_text(encoding="utf-8")

if "RDCN performance layer v2" in s:
    raise SystemExit("Performance layer already applied")

s = s.replace(
    "import os, re, csv, sys, math, time, hashlib, zipfile, tempfile, shutil, threading, traceback",
    "import os, re, csv, sys, math, time, hashlib, zipfile, tempfile, shutil, threading, traceback\nfrom concurrent.futures import ThreadPoolExecutor, as_completed\nimport customtkinter as ctk"
)
s = s.replace('APP_VERSION = "1.1.0"', 'APP_VERSION = "2.2.0"  # RDCN performance layer v2')
s = s.replace("RENDER_SCALE = 3.0", "RENDER_SCALE = 2.4")
s = s.replace("fx=1.35, fy=1.35", "fx=1.10, fy=1.10")
s = s.replace("class App(tk.Tk):", "class App(ctk.CTk):")
s = s.replace("import pytesseract\n", "import pytesseract\nos.environ.setdefault('OMP_THREAD_LIMIT', '1')\n")

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

    if all(vals.get(metric) is not None for metric in ("head", "night", "work")):
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


old_page_deep = '''def page_deep(page):
    crop = render_footer(page, 0.69)
    votes = {m: [] for m in METRICS}
    evidence = {m: [] for m in METRICS}
    for name, img in preprocess_variants(crop):
        for psm in (6, 11):
            txt = ocr_image(img, psm)
            for m in METRICS:
                v, e = find_metric(txt, m)
                if v is not None:
                    votes[m].append(v)
                    evidence[m].append((name, psm, v, e))
    best = {}
    for m, vals in votes.items():
        if vals:
            c = Counter(vals)
            best[m] = c.most_common(1)[0][0]
        else:
            best[m] = None
    return best, evidence
'''
new_page_deep = '''def page_deep(page):
    # OCR progressif : arrêt dès que deux lectures concordent.
    crop = render_footer(page, 0.69)
    variants = preprocess_variants(crop)
    votes = {m: [] for m in METRICS}
    evidence = {m: [] for m in METRICS}

    passes = [(variants[0], 6), (variants[1], 6), (variants[2], 6), (variants[3], 6),
              (variants[0], 11), (variants[1], 11)]
    for (name, img), psm in passes:
        txt = ocr_image(img, psm)
        for metric in METRICS:
            value, ev = find_metric(txt, metric)
            if value is not None:
                votes[metric].append(value)
                evidence[metric].append((name, psm, value, ev))
        resolved = True
        for metric in ("head", "night", "work"):
            counts = Counter(votes[metric])
            if not counts or counts.most_common(1)[0][1] < 2:
                resolved = False
                break
        if resolved:
            break

    best = {}
    for metric, values in votes.items():
        best[metric] = Counter(values).most_common(1)[0][0] if values else None
    return best, evidence
'''
if old_page_deep not in s:
    raise SystemExit("page_deep anchor not found")
s = s.replace(old_page_deep, new_page_deep)


def replace_top_function(source, name, next_name, replacement):
    start = source.find(f"def {name}(")
    end = source.find(f"def {next_name}(", start)
    if start < 0 or end < 0:
        raise SystemExit(f"top-level function {name} not found")
    return source[:start] + replacement.rstrip() + "\\n\\n\\n" + source[end:]


fast_analyze = r'''def analyze_pdf(path, source):
    doc = fitz.open(path)
    try:
        result = BSResult(source, path, os.path.basename(path), len(doc))
        result.date, result.agent, result.matricule, result.journee = parse_filename(path)
        if len(doc) != 3:
            result.warnings.append(f"Nombre de pages inhabituel : {len(doc)} (3 attendues)")

        prim = []
        # Deux pages identiques suffisent déjà au niveau historique 2/3.
        # La troisième n'est lue que si les deux premières divergent.
        for page_index, page in enumerate(doc):
            values, _ = page_primary(page)
            prim.append(values)
            if page_index >= 1:
                stable = True
                for metric in ("head", "night", "work"):
                    counts = Counter(v.get(metric) for v in prim if v.get(metric) is not None)
                    if not counts or counts.most_common(1)[0][1] < 2:
                        stable = False
                        break
                if stable:
                    break

        needs_deep = False
        for metric in ("head", "night", "work"):
            values = [item.get(metric) for item in prim]
            counts = Counter(value for value in values if value is not None)
            if not counts or counts.most_common(1)[0][1] < min(2, len(doc)):
                needs_deep = True
                break

        deeps = []
        deep_evidence = []
        if needs_deep:
            for page in doc:
                values, evidence = page_deep(page)
                deeps.append(values)
                deep_evidence.append(evidence)
                enough = True
                for metric in ("head", "night", "work"):
                    combined = [item.get(metric) for item in prim + deeps if item.get(metric) is not None]
                    counts = Counter(combined)
                    if not counts or counts.most_common(1)[0][1] < 3:
                        enough = False
                        break
                if enough:
                    break

        def cm(metric):
            primary = [item.get(metric) for item in prim]
            deep = [item.get(metric) for item in deeps]
            return consensus_metric(primary, deep, deep_evidence)

        result.head = cm("head")
        result.night = cm("night")
        result.work = cm("work")
        result.amplitude = cm("amplitude")
        result.break_time = cm("break")
        main_metrics = [result.head, result.night, result.work]

        if any(metric.minutes is None for metric in main_metrics):
            result.warnings.append("Au moins une donnée principale est absente")
        if result.amplitude.minutes is not None and result.break_time.minutes is not None and result.work.minutes is not None:
            expected = result.amplitude.minutes - result.break_time.minutes
            if abs(expected - result.work.minutes) > 2:
                result.warnings.append(
                    f"Incohérence : amplitude - coupure = {fmt_minutes(expected)}, travail effectif lu = {result.work.value}"
                )
        if result.work.minutes is not None:
            if result.head.minutes is not None and result.head.minutes > result.work.minutes + 2:
                result.warnings.append("Temps en tête de train supérieur au travail effectif")
            if result.night.minutes is not None and result.night.minutes > result.work.minutes + 2:
                result.warnings.append("Heures de nuit supérieures au travail effectif")

        result.global_confidence = min([metric.confidence for metric in main_metrics] or [0])
        if len(doc) == 3 and result.global_confidence >= 96 and not result.warnings:
            result.status = "VALIDÉ"
        elif result.global_confidence >= 90 and not any("Incohérence" in warning or "absente" in warning for warning in result.warnings):
            result.status = "À VÉRIFIER"
        else:
            result.status = "CONTRÔLE REQUIS"
        return result
    finally:
        doc.close()
'''

def replace_method(source, name, replacement):
    start = source.find(f"    def {name}(")
    if start < 0:
        raise SystemExit(f"method {name} not found")
    match = re.search(r"(?m)^    def [A-Za-z_][A-Za-z0-9_]*\\(", source[start + 1:])
    end = start + 1 + match.start() if match else source.find("\\n\\nif __name__", start)
    return source[:start] + replacement.rstrip() + "\\n\\n" + source[end:]

build_ui = r'''    def build_ui(self):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#F4F7FB")

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=36, background="#FFFFFF", fieldbackground="#FFFFFF",
                        foreground="#24364B", borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#EDF2F7", foreground="#52667A",
                        font=("Segoe UI Semibold", 9), padding=(10, 11), relief="flat")
        style.map("Treeview", background=[("selected", "#DDF3F1")], foreground=[("selected", "#123C3A")])

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True)
        shell.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(1, weight=1)

        sidebar = ctk.CTkFrame(shell, width=235, corner_radius=0, fg_color="#0B1F33")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(28, 30))
        ctk.CTkLabel(brand, text="RDCN", text_color="#5EEAD4",
                     font=ctk.CTkFont("Segoe UI", 13, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(brand, text="Contrôle BS", text_color="white",
                     font=ctk.CTkFont("Segoe UI", 24, weight="bold")).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(brand, text="Centre de contrôle paie", text_color="#8FA6BD",
                     font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", pady=(4, 0))

        self.add_button = ctk.CTkButton(
            sidebar, text="＋  Importer des fichiers", command=self.choose_files,
            height=46, corner_radius=10, fg_color="#0D9488", hover_color="#0F766E",
            font=ctk.CTkFont("Segoe UI", 12, weight="bold"), anchor="w"
        )
        self.add_button.pack(fill="x", padx=16, pady=(0, 18))

        menu = (
            ("✎   Corriger la sélection", self.edit_selected),
            ("▣   Voir le pied de page", self.show_footers),
            ("⇩   Exporter en CSV", self.export_csv),
            ("⌫   Vider les résultats", self.clear),
        )
        for label, command in menu:
            ctk.CTkButton(
                sidebar, text=label, command=command, height=42, corner_radius=8,
                fg_color="transparent", hover_color="#163653", text_color="#D7E3EF",
                anchor="w", font=ctk.CTkFont("Segoe UI", 11)
            ).pack(fill="x", padx=16, pady=3)

        self.cancel_button = ctk.CTkButton(
            sidebar, text="■   Arrêter l’analyse", command=self.cancel_processing,
            height=40, corner_radius=8, fg_color="#7F1D1D", hover_color="#991B1B",
            state="disabled", anchor="w"
        )
        self.cancel_button.pack(side="bottom", fill="x", padx=16, pady=20)

        content = ctk.CTkFrame(shell, fg_color="#F4F7FB", corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(4, weight=1)
        content.grid_columnconfigure(0, weight=1)

        heading = ctk.CTkFrame(content, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=28, pady=(25, 16))
        ctk.CTkLabel(heading, text="Tableau de contrôle",
                     text_color="#142B44", font=ctk.CTkFont("Segoe UI", 26, weight="bold")).pack(side="left")
        badge = ctk.CTkLabel(heading, text="  VERSION 2.2  ", text_color="#0F766E",
                             fg_color="#D9F5F0", corner_radius=8,
                             font=ctk.CTkFont("Segoe UI", 10, weight="bold"), height=28)
        badge.pack(side="left", padx=14)
        self.filter_var = tk.StringVar()
        search = ctk.CTkEntry(
            heading, textvariable=self.filter_var, width=320, height=40, corner_radius=10,
            placeholder_text="Rechercher un agent, une date, une journée…",
            border_width=1, border_color="#D6E0EA", fg_color="white"
        )
        search.pack(side="right")
        self.filter_job = None
        self.filter_var.trace_add("write", lambda *_: self.schedule_filter())

        cards = ctk.CTkFrame(content, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=28)
        for col in range(4):
            cards.grid_columnconfigure(col, weight=1)
        self.card_total = tk.StringVar(value="0")
        self.card_ok = tk.StringVar(value="0")
        self.card_review = tk.StringVar(value="0")
        self.card_work = tk.StringVar(value="0h00")
        card_data = (
            ("Journées chargées", self.card_total, "#2563EB", "#DBEAFE"),
            ("Validées", self.card_ok, "#059669", "#D1FAE5"),
            ("À contrôler", self.card_review, "#DC2626", "#FEE2E2"),
            ("Travail effectif", self.card_work, "#7C3AED", "#EDE9FE"),
        )
        for col, (label, variable, color, pale) in enumerate(card_data):
            card = ctk.CTkFrame(cards, fg_color="white", corner_radius=14, border_width=1, border_color="#E3EAF2")
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 7, 0 if col == 3 else 7))
            stripe = ctk.CTkFrame(card, width=5, height=68, corner_radius=3, fg_color=color)
            stripe.pack(side="left", padx=(13, 10), pady=15)
            box = ctk.CTkFrame(card, fg_color="transparent")
            box.pack(side="left", pady=13)
            ctk.CTkLabel(box, text=label, text_color="#718096",
                         font=ctk.CTkFont("Segoe UI", 10)).pack(anchor="w")
            ctk.CTkLabel(box, textvariable=variable, text_color="#162C46",
                         font=ctk.CTkFont("Segoe UI", 23, weight="bold")).pack(anchor="w")

        activity = ctk.CTkFrame(content, fg_color="white", corner_radius=12, border_width=1, border_color="#E3EAF2")
        activity.grid(row=2, column=0, sticky="ew", padx=28, pady=(16, 12))
        self.summary = tk.StringVar(value="Aucune donnée chargée")
        ctk.CTkLabel(activity, textvariable=self.summary, text_color="#52667A",
                     font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=16, pady=12)
        self.progress = ttk.Progressbar(activity, mode="determinate", length=360)
        self.progress.pack(side="right", padx=16, pady=15)

        table_card = ctk.CTkFrame(content, fg_color="white", corner_radius=14, border_width=1, border_color="#E3EAF2")
        table_card.grid(row=4, column=0, sticky="nsew", padx=28, pady=(0, 20))
        table_card.grid_rowconfigure(1, weight=1)
        table_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(table_card, text="Détail des bulletins", text_color="#20364E",
                     font=ctk.CTkFont("Segoe UI", 14, weight="bold")).grid(
                         row=0, column=0, sticky="w", padx=16, pady=(14, 10))

        table = tk.Frame(table_card, bg="white", bd=0)
        table.grid(row=1, column=0, sticky="nsew", padx=2, pady=(0, 2))
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)
        cols = ("date", "agent", "mat", "journee", "head", "night", "work", "source_type", "cross", "pages", "conf", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse")
        headings = {
            "date": "DATE", "agent": "AGENT", "mat": "MATRICULE", "journee": "JOURNÉE",
            "head": "TÊTE DE TRAIN", "night": "NUIT 22H–07H", "work": "TRAVAIL",
            "source_type": "SOURCE", "cross": "CROISEMENT", "pages": "PAGES",
            "conf": "FIABILITÉ", "status": "STATUT",
        }
        widths = {"date": 92, "agent": 175, "mat": 92, "journee": 85, "head": 112, "night": 110,
                  "work": 105, "source_type": 70, "cross": 92, "pages": 58, "conf": 76, "status": 145}
        for col in cols:
            self.tree.heading(col, text=headings[col], command=lambda x=col: self.sort_by(x))
            self.tree.column(col, width=widths[col], minwidth=55,
                             anchor="w" if col in ("agent", "status") else "center")
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("ok", background="#ECFDF5")
        self.tree.tag_configure("review", background="#FFFBEB")
        self.tree.tag_configure("error", background="#FEF2F2")
        self.tree.bind("<Double-1>", lambda event: self.edit_selected())

        self.status_var = tk.StringVar(value="Prêt • déposez un ZIP ou plusieurs PDF")
        status = ctk.CTkLabel(content, textvariable=self.status_var, anchor="w",
                              text_color="#62758A", font=ctk.CTkFont("Segoe UI", 10))
        status.grid(row=5, column=0, sticky="ew", padx=30, pady=(0, 12))'''

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
        workers = max(1, min(4, os.cpu_count() or 2))
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

s = replace_top_function(s, "analyze_pdf", "safe_extract_pdfs", fast_analyze)
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
print("RDCN v2.2 fast OCR and modern UI patch applied")
