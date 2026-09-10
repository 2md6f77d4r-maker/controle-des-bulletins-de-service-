import os, re, csv, sys, math, time, hashlib, zipfile, tempfile, shutil, threading, traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import fitz
import cv2
import numpy as np
from PIL import Image, ImageTk
import pytesseract

APP_NAME = "Contrôle BS - RDCN"
APP_VERSION = "1.0.0"
FOOTER_TOP = 0.72
RENDER_SCALE = 3.0

METRICS = {
    "head": "Temps en tête de train",
    "night": "Heures de nuit",
    "work": "Travail effectif",
    "amplitude": "Amplitude",
    "break": "Coupure",
}

@dataclass
class MetricResult:
    value: str = ""
    minutes: int | None = None
    confidence: int = 0
    evidence: list = field(default_factory=list)
    status: str = "absent"

@dataclass
class BSResult:
    source: str
    pdf_path: str
    filename: str
    pages: int
    date: str = ""
    agent: str = ""
    matricule: str = ""
    journee: str = ""
    head: MetricResult = field(default_factory=MetricResult)
    night: MetricResult = field(default_factory=MetricResult)
    work: MetricResult = field(default_factory=MetricResult)
    amplitude: MetricResult = field(default_factory=MetricResult)
    break_time: MetricResult = field(default_factory=MetricResult)
    global_confidence: int = 0
    status: str = "À contrôler"
    warnings: list = field(default_factory=list)
    manually_corrected: bool = False


def app_base_dir():
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


def configure_tesseract():
    candidates = []
    bundled = os.path.join(app_base_dir(), "tesseract", "tesseract.exe")
    candidates.append(bundled)
    candidates += [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract") or "",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            pytesseract.pytesseract.tesseract_cmd = c
            tessdata = os.path.join(os.path.dirname(c), "tessdata")
            if os.path.isdir(tessdata):
                os.environ["TESSDATA_PREFIX"] = tessdata
            return c
    raise FileNotFoundError("Moteur OCR Tesseract introuvable.")


def normalize_ocr_text(s: str) -> str:
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"[\t\r]+", " ", s)
    s = re.sub(r" +", " ", s)
    return s


def token_to_minutes(token: str):
    if token is None:
        return None
    t = token.strip().lower()
    t = t.replace('o', '0').replace('l', '1').replace('i', '1')
    t = t.replace('n', 'h').replace('m', 'h')
    t = re.sub(r'[^0-9h:]', '', t)
    m = re.fullmatch(r'(\d{1,2})h(\d{1,2})', t)
    if not m:
        m = re.fullmatch(r'(\d{1,2}):(\d{1,2})', t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 24 and 0 <= mi < 60:
            return h * 60 + mi
        return None
    if re.fullmatch(r'\d{3,4}', t):
        h = int(t[:-2]); mi = int(t[-2:])
        if 0 <= h <= 24 and 0 <= mi < 60:
            return h * 60 + mi
    return None


def fmt_minutes(m):
    if m is None: return ""
    return f"{m//60}h{m%60:02d}"


def duration_tokens(text):
    rgx = r'(?<!\d)(?:[0-9OoIl]{1,2}\s*[hHnNmM:]\s*[0-9OoIl]{1,2}|[0-9OoIl]{3,4})(?!\d)'
    out = []
    for m in re.finditer(rgx, text):
        mins = token_to_minutes(m.group(0))
        if mins is not None:
            out.append((m.start(), m.group(0), mins))
    return out


def canonical_for_matching(text):
    t = text.lower()
    repl = {'é':'e','è':'e','ê':'e','ë':'e','à':'a','â':'a','î':'i','ï':'i','ô':'o','ù':'u','û':'u','ç':'c'}
    for a,b in repl.items(): t=t.replace(a,b)
    t = re.sub(r'[^a-z0-9:\-\n ]+', ' ', t)
    t = re.sub(r' +', ' ', t)
    return t


def find_metric(text, metric):
    raw = normalize_ocr_text(text)
    t = canonical_for_matching(raw)
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    patterns = {
        'work': [r'travail\s+effectif'],
        'head': [r'[tf]?emps\s+en\s+t.?te\s+de\s+train', r'tete\s+de\s+train'],
        'night': [r'heures?\s+de\s+nuit'],
        'amplitude': [r'amplitude'],
        'break': [r'coupure'],
    }[metric]
    for line in lines:
        pm = None
        for p in patterns:
            pm = re.search(p, line)
            if pm: break
        if not pm: continue
        sub = line[pm.end():]
        toks = duration_tokens(sub)
        if not toks: continue
        if metric == 'night':
            values = [x[2] for x in toks]
            if len(values) >= 3 and abs(values[0]-1320) <= 2 and abs(values[1]-420) <= 2:
                return values[2], line
            for vv in values:
                if vv not in (1320, 420) and vv <= 12*60:
                    return vv, line
            return values[0], line
        return toks[0][2], line
    for p in patterns:
        pm = re.search(p, t, re.S)
        if pm:
            sub = t[pm.end():pm.end()+160]
            toks = duration_tokens(sub)
            if toks:
                if metric == 'night':
                    values=[x[2] for x in toks]
                    if len(values) >= 3 and abs(values[0]-1320) <= 2 and abs(values[1]-420) <= 2:
                        return values[2], sub
                    for vv in values:
                        if vv not in (1320,420) and vv <= 12*60:
                            return vv, sub
                return toks[0][2], sub
    return None, ""


def preprocess_variants(gray):
    up = cv2.resize(gray, None, fx=1.35, fy=1.35, interpolation=cv2.INTER_CUBIC)
    otsu = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(up)
    adap = cv2.adaptiveThreshold(up,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,11)
    return [('otsu', otsu), ('gray', up), ('clahe', clahe), ('adaptive', adap)]


def render_footer(page, top=FOOTER_TOP):
    pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    y = max(0, int(gray.shape[0] * top))
    return gray[y:, :]


def ocr_image(img, psm=6):
    return pytesseract.image_to_string(img, lang='eng', config=f'--oem 3 --psm {psm}')


def page_primary(page):
    crop = render_footer(page)
    variant = preprocess_variants(crop)[0][1]
    txt = ocr_image(variant, 6)
    vals = {}
    ev = {}
    for m in METRICS:
        v, e = find_metric(txt, m)
        vals[m] = v; ev[m] = e
    return vals, ev


def page_deep(page):
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


def consensus_metric(primary_values, deep_values=None, deep_evidence=None):
    vals = [v for v in primary_values if v is not None]
    c = Counter(vals)
    if c:
        val, n = c.most_common(1)[0]
        if n >= 3:
            return MetricResult(fmt_minutes(val), val, 100, primary_values, 'confirmé 3/3')
        if n >= 2:
            return MetricResult(fmt_minutes(val), val, 98, primary_values, 'confirmé 2/3')
    allvals = vals[:]
    if deep_values:
        allvals += [v for v in deep_values if v is not None]
    if allvals:
        c2 = Counter(allvals)
        val, n = c2.most_common(1)[0]
        second = c2.most_common(2)[1][1] if len(c2)>1 else 0
        if n >= 4 and n >= second + 2:
            return MetricResult(fmt_minutes(val), val, 96, allvals, 'confirmé multi-OCR')
        if n >= 2 and n > second:
            return MetricResult(fmt_minutes(val), val, 90, allvals, 'probable')
        if len(c2) == 1:
            return MetricResult(fmt_minutes(val), val, 82, allvals, 'lecture unique')
        return MetricResult(fmt_minutes(val), val, 55, allvals, 'conflit')
    return MetricResult('', None, 0, [], 'absent')


def parse_filename(name):
    base = os.path.splitext(os.path.basename(name))[0]
    date = agent = matricule = journee = ""
    m = re.search(r'(\d{2}[\/_-]\d{2}[\/_-]\d{4})', base)
    if m: date = m.group(1).replace('_','/').replace('-','/')
    parts = [p.strip() for p in base.split(' - ')]
    if len(parts) >= 5 and re.search(r'\d{2}[_/]\d{2}[_/]\d{4}', parts[1]):
        date = parts[1].replace('_','/')
        agent = parts[2]
        matricule = parts[3]
        journee = re.sub(r'^JS\s*', '', parts[4], flags=re.I)
    return date, agent, matricule, journee


def analyze_pdf(path, source):
    doc = fitz.open(path)
    result = BSResult(source, path, os.path.basename(path), len(doc))
    result.date, result.agent, result.matricule, result.journee = parse_filename(path)
    if len(doc) != 3:
        result.warnings.append(f"Nombre de pages inhabituel : {len(doc)} (3 attendues)")
    prim = []
    for p in doc:
        v, _ = page_primary(p)
        prim.append(v)
    needs_deep = False
    for metric in METRICS:
        vals = [x.get(metric) for x in prim]
        cc = Counter([v for v in vals if v is not None])
        if not cc or cc.most_common(1)[0][1] < min(2, len(doc)):
            needs_deep = True
            break
    deeps = []
    deep_evidence = []
    if needs_deep:
        for p in doc:
            dv, de = page_deep(p)
            deeps.append(dv); deep_evidence.append(de)
    def cm(metric):
        pv=[x.get(metric) for x in prim]
        dv=[x.get(metric) for x in deeps] if deeps else []
        return consensus_metric(pv,dv,deep_evidence)
    result.head=cm('head'); result.night=cm('night'); result.work=cm('work')
    result.amplitude=cm('amplitude'); result.break_time=cm('break')
    main_metrics=[result.head,result.night,result.work]
    if any(m.minutes is None for m in main_metrics): result.warnings.append("Au moins une donnée principale est absente")
    if result.amplitude.minutes is not None and result.break_time.minutes is not None and result.work.minutes is not None:
        expected = result.amplitude.minutes - result.break_time.minutes
        if abs(expected - result.work.minutes) > 2:
            result.warnings.append(f"Incohérence : amplitude - coupure = {fmt_minutes(expected)}, travail effectif lu = {result.work.value}")
    if result.work.minutes is not None:
        if result.head.minutes is not None and result.head.minutes > result.work.minutes + 2:
            result.warnings.append("Temps en tête de train supérieur au travail effectif")
        if result.night.minutes is not None and result.night.minutes > result.work.minutes + 2:
            result.warnings.append("Heures de nuit supérieures au travail effectif")
    result.global_confidence = min([m.confidence for m in main_metrics] or [0])
    if len(doc)==3 and result.global_confidence >= 96 and not result.warnings:
        result.status="VALIDÉ"
    elif result.global_confidence >= 90 and not any('Incohérence' in w or 'absente' in w for w in result.warnings):
        result.status="À VÉRIFIER"
    else:
        result.status="CONTRÔLE REQUIS"
    doc.close()
    return result


def safe_extract_pdfs(zip_path, target):
    out=[]
    with zipfile.ZipFile(zip_path) as z:
        for i, info in enumerate(z.infolist()):
            if not info.filename.lower().endswith('.pdf'): continue
            safe_name = os.path.basename(info.filename)
            if not safe_name: continue
            dest=os.path.join(target, f"{i:04d}_{safe_name}")
            with z.open(info) as src, open(dest,'wb') as dst:
                shutil.copyfileobj(src,dst)
            out.append(dest)
    return out


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1450x820")
        self.minsize(1100,650)
        self.results=[]
        self.tempdir=tempfile.mkdtemp(prefix='rdcn_bs_')
        self.seen_hashes=set()
        self.sort_col=None; self.sort_rev=False
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.build_ui()
        try:
            tpath=configure_tesseract()
            self.set_status(f"OCR prêt — {tpath}")
        except Exception as e:
            messagebox.showerror("OCR indisponible", str(e))
            self.set_status("OCR indisponible")

    def build_ui(self):
        top=ttk.Frame(self,padding=10); top.pack(fill='x')
        ttk.Label(top,text="Contrôle des Bulletins de Service",font=('Segoe UI',18,'bold')).pack(side='left')
        ttk.Button(top,text="Ajouter PDF / ZIP",command=self.choose_files).pack(side='right',padx=4)
        ttk.Button(top,text="Exporter CSV",command=self.export_csv).pack(side='right',padx=4)
        ttk.Button(top,text="Corriger sélection",command=self.edit_selected).pack(side='right',padx=4)
        ttk.Button(top,text="Voir pieds de page",command=self.show_footers).pack(side='right',padx=4)
        ttk.Button(top,text="Vider",command=self.clear).pack(side='right',padx=4)
        info=ttk.Frame(self,padding=(10,0,10,8)); info.pack(fill='x')
        self.summary=tk.StringVar(value="0 BS")
        ttk.Label(info,textvariable=self.summary).pack(side='left')
        self.progress=ttk.Progressbar(info,mode='determinate',length=350); self.progress.pack(side='right')
        cols=('date','agent','mat','journee','head','night','work','pages','conf','status')
        self.tree=ttk.Treeview(self,columns=cols,show='headings',selectmode='browse')
        headings={'date':'Date','agent':'Agent','mat':'Matricule','journee':'Journée','head':'Tête de train','night':'Nuit 22h-07h','work':'Travail effectif','pages':'Pages','conf':'Confiance','status':'Statut'}
        widths={'date':95,'agent':210,'mat':105,'journee':100,'head':115,'night':115,'work':125,'pages':65,'conf':85,'status':145}
        for c in cols:
            self.tree.heading(c,text=headings[c],command=lambda x=c:self.sort_by(x))
            self.tree.column(c,width=widths[c],anchor='center' if c not in ('agent','status') else 'w')
        y=ttk.Scrollbar(self,orient='vertical',command=self.tree.yview); self.tree.configure(yscrollcommand=y.set)
        self.tree.pack(side='left',fill='both',expand=True,padx=(10,0),pady=(0,10)); y.pack(side='right',fill='y',padx=(0,10),pady=(0,10))
        self.tree.bind('<Double-1>',lambda e:self.edit_selected())
        self.status_var=tk.StringVar(value="Prêt")
        ttk.Label(self,textvariable=self.status_var,relief='sunken',anchor='w',padding=5).pack(side='bottom',fill='x')

    def set_status(self,s): self.status_var.set(s); self.update_idletasks()

    def choose_files(self):
        files=filedialog.askopenfilenames(title="Choisir des BS ou ZIP",filetypes=[('PDF et ZIP','*.pdf *.zip'),('PDF','*.pdf'),('ZIP','*.zip'),('Tous','*.*')])
        if files: threading.Thread(target=self.process_files,args=(files,),daemon=True).start()

    def process_files(self,files):
        pdfs=[]
        for f in files:
            try:
                if f.lower().endswith('.zip'):
                    sub=os.path.join(self.tempdir, hashlib.md5(f.encode()).hexdigest()); os.makedirs(sub,exist_ok=True)
                    pdfs += [(p, os.path.basename(f)) for p in safe_extract_pdfs(f,sub)]
                elif f.lower().endswith('.pdf'):
                    pdfs.append((f, os.path.basename(f)))
            except Exception as e:
                self.after(0,messagebox.showerror,"Erreur ZIP",f"{os.path.basename(f)} : {e}")
        self.after(0,lambda:self.progress.configure(maximum=max(1,len(pdfs)),value=0))
        done=0; added=0
        for p,source in pdfs:
            done+=1
            self.after(0,self.set_status,f"Analyse {done}/{len(pdfs)} — {os.path.basename(p)}")
            try:
                h=sha256(p)
                if h in self.seen_hashes:
                    self.after(0,lambda d=done:self.progress.configure(value=d)); continue
                self.seen_hashes.add(h)
                r=analyze_pdf(p,source)
                self.results.append(r); added+=1
                self.after(0,self.insert_result,r)
            except Exception as e:
                print(f"{os.path.basename(p)} : {e}"); traceback.print_exc()
            self.after(0,lambda d=done:self.progress.configure(value=d))
        self.after(0,self.refresh_summary)
        self.after(0,self.set_status,f"Analyse terminée — {added} BS ajoutés")

    def insert_result(self,r):
        idx=len(self.results)-1
        self.tree.insert('', 'end', iid=str(idx), values=(r.date,r.agent,r.matricule,r.journee,r.head.value,r.night.value,r.work.value,r.pages,f"{r.global_confidence}%",r.status))

    def refresh_tree(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        for i,r in enumerate(self.results):
            self.tree.insert('', 'end', iid=str(i), values=(r.date,r.agent,r.matricule,r.journee,r.head.value,r.night.value,r.work.value,r.pages,f"{r.global_confidence}%",r.status))
        self.refresh_summary()

    def refresh_summary(self):
        n=len(self.results); ok=sum(r.status=='VALIDÉ' for r in self.results); rev=n-ok
        ht=sum((r.head.minutes or 0) for r in self.results); nt=sum((r.night.minutes or 0) for r in self.results); wt=sum((r.work.minutes or 0) for r in self.results)
        self.summary.set(f"{n} BS  |  {ok} validés automatiquement  |  {rev} à contrôler  |  Tête de train {fmt_minutes(ht)}  |  Nuit {fmt_minutes(nt)}  |  Travail {fmt_minutes(wt)}")

    def selected_result(self):
        sel=self.tree.selection()
        if not sel: return None
        return self.results[int(sel[0])]

    def edit_selected(self):
        r=self.selected_result()
        if not r: return
        win=tk.Toplevel(self); win.title("Contrôle manuel"); win.transient(self); win.grab_set(); win.geometry('560x430')
        frm=ttk.Frame(win,padding=15); frm.pack(fill='both',expand=True)
        ttk.Label(frm,text=r.filename,wraplength=510).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,12))
        vars={}
        rows=[('Temps en tête de train','head'),('Heures de nuit','night'),('Travail effectif','work')]
        for rr,(lab,key) in enumerate(rows,1):
            ttk.Label(frm,text=lab).grid(row=rr,column=0,sticky='w',pady=6)
            v=tk.StringVar(value=getattr(r,key).value); vars[key]=v
            ttk.Entry(frm,textvariable=v,width=15).grid(row=rr,column=1,sticky='w')
        warn='\n'.join('• '+w for w in r.warnings) if r.warnings else 'Aucune incohérence détectée.'
        ttk.Label(frm,text=f"Confiance automatique : {r.global_confidence}%\n\n{warn}",wraplength=500).grid(row=5,column=0,columnspan=2,sticky='w',pady=15)
        def save():
            for key,v in vars.items():
                mins=token_to_minutes(v.get())
                if mins is None:
                    messagebox.showerror('Valeur incorrecte',f"Format attendu : 4h58 (champ {key})",parent=win); return
                mr=getattr(r,key); mr.minutes=mins; mr.value=fmt_minutes(mins); mr.confidence=100; mr.status='corrigé manuellement'
            r.manually_corrected=True; r.global_confidence=100; r.status='VALIDÉ MANUELLEMENT'
            win.destroy(); self.refresh_tree()
        ttk.Button(frm,text='Valider la correction',command=save).grid(row=6,column=0,columnspan=2,pady=10)

    def show_footers(self):
        r=self.selected_result()
        if not r: return
        try:
            doc=fitz.open(r.pdf_path)
            win=tk.Toplevel(self); win.title(f"Pieds de page — {r.filename}"); win.geometry('1200x800')
            canvas=tk.Canvas(win); sb=ttk.Scrollbar(win,orient='vertical',command=canvas.yview); canvas.configure(yscrollcommand=sb.set)
            sb.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True)
            inner=ttk.Frame(canvas); canvas.create_window((0,0),window=inner,anchor='nw')
            win._imgs=[]
            for i,p in enumerate(doc):
                gray=render_footer(p,0.69)
                h,w=gray.shape; scale=min(1.0,1100/w); arr=cv2.resize(gray,(int(w*scale),int(h*scale)))
                im=Image.fromarray(arr); ph=ImageTk.PhotoImage(im); win._imgs.append(ph)
                ttk.Label(inner,text=f"Page {i+1}").pack(anchor='w',padx=10,pady=(10,2))
                ttk.Label(inner,image=ph).pack(anchor='w',padx=10)
            doc.close()
            inner.update_idletasks(); canvas.configure(scrollregion=canvas.bbox('all'))
        except Exception as e: messagebox.showerror('Aperçu impossible',str(e))

    def export_csv(self):
        if not self.results: return
        f=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile=f"controle_BS_{datetime.now():%Y%m%d_%H%M}.csv")
        if not f:return
        with open(f,'w',newline='',encoding='utf-8-sig') as out:
            w=csv.writer(out,delimiter=';')
            w.writerow(['Date','Agent','Matricule','Journée','Temps tête de train','Heures de nuit 22h00-07h00','Travail effectif','Pages','Confiance','Statut','Correction manuelle','Alertes','Fichier','Source'])
            for r in self.results:
                w.writerow([r.date,r.agent,r.matricule,r.journee,r.head.value,r.night.value,r.work.value,r.pages,r.global_confidence,r.status,'Oui' if r.manually_corrected else 'Non',' | '.join(r.warnings),r.filename,r.source])
        self.set_status(f"Export créé : {f}")

    def sort_by(self,col):
        keymap={'date':lambda r:r.date,'agent':lambda r:r.agent.lower(),'mat':lambda r:r.matricule.lower(),'journee':lambda r:r.journee.lower(),'head':lambda r:r.head.minutes or -1,'night':lambda r:r.night.minutes or -1,'work':lambda r:r.work.minutes or -1,'pages':lambda r:r.pages,'conf':lambda r:r.global_confidence,'status':lambda r:r.status}
        self.sort_rev = (not self.sort_rev) if self.sort_col==col else False; self.sort_col=col
        self.results.sort(key=keymap[col],reverse=self.sort_rev); self.refresh_tree()

    def clear(self):
        self.results.clear(); self.seen_hashes.clear(); self.refresh_tree(); self.progress['value']=0; self.set_status('Prêt')

    def on_close(self):
        try: shutil.rmtree(self.tempdir,ignore_errors=True)
        finally: self.destroy()


if __name__=='__main__':
    App().mainloop()
