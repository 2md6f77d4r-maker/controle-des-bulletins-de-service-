from pathlib import Path

p=Path('app.py')
s=p.read_text(encoding='utf-8')

if 'def read_excel_results(path):' in s:
    raise SystemExit('Excel support already present')

s=s.replace('import pytesseract\n', 'import pytesseract\nimport openpyxl\nfrom openpyxl.utils.datetime import from_excel\n')
s=s.replace('APP_VERSION = "1.0.0"', 'APP_VERSION = "1.1.0"')
s=s.replace('    manually_corrected: bool = False\n', '    manually_corrected: bool = False\n    source_type: str = "BS"\n    crosscheck: str = ""\n')

insert = r'''

def normalize_header(v):
    if v is None: return ""
    s=str(v).strip().lower()
    repl={'é':'e','è':'e','ê':'e','ë':'e','à':'a','â':'a','î':'i','ï':'i','ô':'o','ù':'u','û':'u','ç':'c'}
    for a,b in repl.items(): s=s.replace(a,b)
    return re.sub(r'[^a-z0-9]+',' ',s).strip()


def excel_date_to_text(v, epoch=None):
    if v is None: return ""
    if isinstance(v, datetime): return v.strftime('%d/%m/%Y')
    if hasattr(v, 'strftime'):
        try: return v.strftime('%d/%m/%Y')
        except Exception: pass
    if isinstance(v,(int,float)) and epoch is not None:
        try: return from_excel(v, epoch).strftime('%d/%m/%Y')
        except Exception: pass
    s=str(v).strip()
    for fmt in ('%d/%m/%Y','%Y-%m-%d','%d-%m-%Y','%d_%m_%Y'):
        try: return datetime.strptime(s[:10],fmt).strftime('%d/%m/%Y')
        except Exception: pass
    return s


def metric_from_excel(v, confidence=100):
    if v is None or str(v).strip()=="": return MetricResult('',None,0,[],'absent')
    if isinstance(v,(int,float)):
        mins = round(v*24*60) if 0 <= float(v) < 2 else round(float(v))
    else:
        mins=token_to_minutes(str(v))
    if mins is None: return MetricResult('',None,0,[str(v)],'excel illisible')
    return MetricResult(fmt_minutes(mins),mins,int(confidence or 100),[str(v)],'Excel')


def read_excel_results(path):
    wb=openpyxl.load_workbook(path,data_only=True,read_only=True)
    results=[]
    aliases={
      'date':['date','date de service'],
      'agent':['agent','nom agent'],
      'matricule':['matricule'],
      'journee':['journee','js','journee de service'],
      'head':['temps tete de train','temps en tete de train','tete de train'],
      'night':['heures de nuit 22h00 07h00','heures de nuit','nuit 22h 07h'],
      'work':['travail effectif','temps de travail effectif'],
      'pages':['pages','nombre de pages'],
      'confidence':['confiance'],
      'status':['statut'],
      'manual':['correction manuelle'],
      'alerts':['alertes'],
      'file':['fichier'],
      'source':['source'],
    }
    for ws in wb.worksheets:
        it=ws.iter_rows(values_only=True)
        try: headers=next(it)
        except StopIteration: continue
        norm=[normalize_header(x) for x in headers]
        idx={}
        for key,names in aliases.items():
            for name in names:
                nn=normalize_header(name)
                if nn in norm:
                    idx[key]=norm.index(nn); break
        if not all(k in idx for k in ('head','night','work')):
            continue
        for rownum,row in enumerate(it,2):
            def get(k,default=None):
                i=idx.get(k)
                return row[i] if i is not None and i < len(row) else default
            if all(get(k) in (None,'') for k in ('date','agent','head','night','work')): continue
            conf=get('confidence',100)
            try: conf=int(float(conf)) if conf not in (None,'') else 100
            except Exception: conf=100
            date=excel_date_to_text(get('date'),getattr(wb,'epoch',None))
            agent=str(get('agent') or '').strip()
            mat=str(get('matricule') or '').strip()
            journee=str(get('journee') or '').strip()
            filename=str(get('file') or f'{Path(path).name} - ligne {rownum}')
            source=str(get('source') or Path(path).name)
            pages=get('pages',0)
            try: pages=int(pages or 0)
            except Exception: pages=0
            r=BSResult(source,path,filename,pages,date,agent,mat,journee)
            r.head=metric_from_excel(get('head'),conf)
            r.night=metric_from_excel(get('night'),conf)
            r.work=metric_from_excel(get('work'),conf)
            r.global_confidence=min([m.confidence for m in (r.head,r.night,r.work) if m.minutes is not None] or [0])
            r.status=str(get('status') or 'IMPORT EXCEL').strip()
            r.manually_corrected=str(get('manual') or '').strip().lower() in ('oui','yes','true','1')
            alerts=str(get('alerts') or '').strip()
            if alerts: r.warnings=[x.strip() for x in alerts.split('|') if x.strip()]
            if any(m.minutes is None for m in (r.head,r.night,r.work)):
                r.warnings.append('Donnée Excel principale absente ou illisible')
                r.status='CONTRÔLE REQUIS'
            r.source_type='EXCEL'
            results.append(r)
    wb.close()
    if not results:
        raise ValueError("Aucune feuille Excel compatible : colonnes Temps tête de train, Heures de nuit et Travail effectif requises.")
    return results


def identity_key(r):
    agent=re.sub(r'[^a-z]','',canonical_for_matching(r.agent or ''))
    date=(r.date or '').strip()
    mat=re.sub(r'[^a-z0-9]','',str(r.matricule or '').lower())
    if date and (agent or mat): return (date, mat or agent)
    return None


def reconcile_results(results):
    groups=defaultdict(list)
    for r in results:
        k=identity_key(r)
        if k: groups[k].append(r)
    for group in groups.values():
        bs=[r for r in group if r.source_type=='BS']
        xl=[r for r in group if r.source_type=='EXCEL']
        if not bs or not xl: continue
        b=bs[0]
        for x in xl:
            diffs=[]
            for label,key in [('tête de train','head'),('nuit','night'),('travail effectif','work')]:
                bv=getattr(b,key).minutes; xv=getattr(x,key).minutes
                if bv is None or xv is None: diffs.append(f'{label}: valeur manquante')
                elif abs(bv-xv)>1: diffs.append(f'{label}: BS {fmt_minutes(bv)} / Excel {fmt_minutes(xv)}')
            if diffs:
                msg='Écart Excel/BS — ' + ' ; '.join(diffs)
                if msg not in b.warnings: b.warnings.append(msg)
                if msg not in x.warnings: x.warnings.append(msg)
                b.status='CONTRÔLE REQUIS'; x.status='CONTRÔLE REQUIS'
                b.crosscheck='ÉCART'; x.crosscheck='ÉCART'
            else:
                b.crosscheck='OK Excel'; x.crosscheck='OK BS'
                b.global_confidence=max(b.global_confidence,99)
                if b.status!='VALIDÉ MANUELLEMENT': b.status='VALIDÉ + EXCEL'
                x.status='CONFIRMÉ PAR BS'
'''

anchor='\ndef safe_extract_pdfs(zip_path, target):\n'
if anchor not in s: raise SystemExit('Anchor safe_extract_pdfs not found')
s=s.replace(anchor, insert+anchor)

s=s.replace('text="Ajouter PDF / ZIP"', 'text="Ajouter PDF / ZIP / Excel"')
s=s.replace('title="Choisir des BS ou ZIP"', 'title="Choisir des BS, ZIP ou Excel"')
s=s.replace("filetypes=[('PDF et ZIP','*.pdf *.zip'),('PDF','*.pdf'),('ZIP','*.zip'),('Tous','*.*')]", "filetypes=[('PDF, ZIP et Excel','*.pdf *.zip *.xlsx *.xlsm'),('PDF','*.pdf'),('ZIP','*.zip'),('Excel','*.xlsx *.xlsm'),('Tous','*.*')]")
s=s.replace("cols=('date','agent','mat','journee','head','night','work','pages','conf','status')", "cols=('date','agent','mat','journee','head','night','work','source_type','cross','pages','conf','status')")
s=s.replace("'work':'Travail effectif','pages':'Pages'", "'work':'Travail effectif','source_type':'Source','cross':'Croisement','pages':'Pages'")
s=s.replace("'work':125,'pages':65", "'work':125,'source_type':75,'cross':100,'pages':65")
s=s.replace("r.head.value,r.night.value,r.work.value,r.pages,f\"{r.global_confidence}%\",r.status", "r.head.value,r.night.value,r.work.value,r.source_type,r.crosscheck,r.pages,f\"{r.global_confidence}%\",r.status")

old="""        pdfs=[]
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
"""
new="""        pdfs=[]; excels=[]
        for f in files:
            try:
                if f.lower().endswith('.zip'):
                    sub=os.path.join(self.tempdir, hashlib.md5(f.encode()).hexdigest()); os.makedirs(sub,exist_ok=True)
                    pdfs += [(p, os.path.basename(f)) for p in safe_extract_pdfs(f,sub)]
                elif f.lower().endswith('.pdf'):
                    pdfs.append((f, os.path.basename(f)))
                elif f.lower().endswith(('.xlsx','.xlsm')):
                    excels.append(f)
            except Exception as e:
                self.after(0,messagebox.showerror,"Erreur import",f"{os.path.basename(f)} : {e}")
        self.after(0,lambda:self.progress.configure(maximum=max(1,len(pdfs)+len(excels)),value=0))
"""
if old not in s: raise SystemExit('process_files block not found')
s=s.replace(old,new)

old2="""            self.after(0,lambda d=done:self.progress.configure(value=d))
        self.after(0,self.refresh_summary)
        self.after(0,self.set_status,f"Analyse terminée — {added} BS ajoutés")
"""
new2="""            self.after(0,lambda d=done:self.progress.configure(value=d))
        for xf in excels:
            done+=1
            self.after(0,self.set_status,f"Lecture Excel {done}/{len(pdfs)+len(excels)} — {os.path.basename(xf)}")
            try:
                rows=read_excel_results(xf)
                self.results.extend(rows); added += len(rows)
            except Exception as e:
                self.after(0,messagebox.showerror,"Erreur Excel",f"{os.path.basename(xf)} : {e}")
            self.after(0,lambda d=done:self.progress.configure(value=d))
        reconcile_results(self.results)
        self.after(0,self.refresh_tree)
        self.after(0,self.refresh_summary)
        self.after(0,self.set_status,f"Analyse terminée — {added} lignes/BS ajoutés")
"""
if old2 not in s: raise SystemExit('process_files tail not found')
s=s.replace(old2,new2)

old3="""        n=len(self.results); ok=sum(r.status=='VALIDÉ' for r in self.results); rev=n-ok
        ht=sum((r.head.minutes or 0) for r in self.results); nt=sum((r.night.minutes or 0) for r in self.results); wt=sum((r.work.minutes or 0) for r in self.results)
        self.summary.set(f"{n} BS  |  {ok} validés automatiquement  |  {rev} à contrôler  |  Tête de train {fmt_minutes(ht)}  |  Nuit {fmt_minutes(nt)}  |  Travail {fmt_minutes(wt)}")
"""
new3="""        n=len(self.results); ok=sum(r.status in ('VALIDÉ','VALIDÉ + EXCEL','CONFIRMÉ PAR BS','VALIDÉ MANUELLEMENT') for r in self.results); rev=n-ok
        chosen=[]; seen=set()
        for r in sorted(self.results,key=lambda x: 0 if x.source_type=='BS' else 1):
            k=identity_key(r)
            if k and k in seen: continue
            if k: seen.add(k)
            chosen.append(r)
        ht=sum((r.head.minutes or 0) for r in chosen); nt=sum((r.night.minutes or 0) for r in chosen); wt=sum((r.work.minutes or 0) for r in chosen)
        self.summary.set(f"{n} lignes  |  {len(chosen)} journées calculées  |  {ok} validées/confirmées  |  {rev} à contrôler  |  Tête {fmt_minutes(ht)}  |  Nuit {fmt_minutes(nt)}  |  Travail {fmt_minutes(wt)}")
"""
if old3 not in s: raise SystemExit('summary block not found')
s=s.replace(old3,new3)

needle="""    def show_footers(self):
        r=self.selected_result()
        if not r: return
        try:
            doc=fitz.open(r.pdf_path)
"""
replacement="""    def show_footers(self):
        r=self.selected_result()
        if not r: return
        if r.source_type!='BS':
            messagebox.showinfo('Aperçu','Cette ligne provient d’un fichier Excel et n’a pas de pages PDF à afficher.'); return
        try:
            doc=fitz.open(r.pdf_path)
"""
if needle not in s: raise SystemExit('show_footers block not found')
s=s.replace(needle,replacement)

s=s.replace("['Date','Agent','Matricule','Journée','Temps tête de train','Heures de nuit 22h00-07h00','Travail effectif','Pages','Confiance','Statut','Correction manuelle','Alertes','Fichier','Source']", "['Date','Agent','Matricule','Journée','Temps tête de train','Heures de nuit 22h00-07h00','Travail effectif','Type source','Croisement','Pages','Confiance','Statut','Correction manuelle','Alertes','Fichier','Source']")
s=s.replace("[r.date,r.agent,r.matricule,r.journee,r.head.value,r.night.value,r.work.value,r.pages,r.global_confidence,r.status,'Oui'", "[r.date,r.agent,r.matricule,r.journee,r.head.value,r.night.value,r.work.value,r.source_type,r.crosscheck,r.pages,r.global_confidence,r.status,'Oui'")
s=s.replace("'work':lambda r:r.work.minutes or -1,'pages':lambda r:r.pages", "'work':lambda r:r.work.minutes or -1,'source_type':lambda r:r.source_type,'cross':lambda r:r.crosscheck,'pages':lambda r:r.pages")

p.write_text(s,encoding='utf-8')
print('Excel support patch applied')
