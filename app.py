import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import sqlite3

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Universal", layout="wide", page_icon="🏢")

st.title("🏢 Gestor de Folha eSocial (Leitor Universal)")
st.markdown("""
Versão 10.0: Busca profunda de tags ignorando namespaces e estrutura rígida.
""")

# --- BANCO DE DADOS (Igual à versão anterior) ---
def init_db():
    conn = sqlite3.connect('esocial_db.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (codigo TEXT PRIMARY KEY, tipo TEXT, nome_personalizado TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (cpf TEXT PRIMARY KEY, nome TEXT, departamento TEXT)''')
    try: c.execute("ALTER TABLE rubricas ADD COLUMN nome_personalizado TEXT")
    except sqlite3.OperationalError: pass
    conn.commit(); conn.close()

def get_db_connection(): return sqlite3.connect('esocial_db.db')

def carregar_rubricas_db():
    conn = get_db_connection()
    try: df = pd.read_sql("SELECT * FROM rubricas", conn)
    except: df = pd.DataFrame(columns=["codigo", "tipo", "nome_personalizado"])
    conn.close()
    if not df.empty: return df.set_index("codigo")[["tipo", "nome_personalizado"]].to_dict('index')
    return {}

def carregar_funcionarios_db():
    conn = get_db_connection()
    try: df = pd.read_sql("SELECT * FROM funcionarios", conn)
    except: df = pd.DataFrame(columns=["cpf", "nome", "departamento"])
    conn.close()
    return df

def salvar_alteracoes_rubricas(df_edited):
    conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM rubricas")
    for _, row in df_edited.iterrows():
        c.execute("INSERT INTO rubricas VALUES (?, ?, ?)", (str(row['codigo']), str(row['tipo']), str(row['nome_personalizado']) if pd.notna(row['nome_personalizado']) else ""))
    conn.commit(); conn.close()

def salvar_alteracoes_funcionarios(df_edited):
    conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM funcionarios")
    for _, row in df_edited.iterrows():
        c.execute("INSERT INTO funcionarios VALUES (?, ?, ?)", (str(row['cpf']), str(row['nome']) if pd.notna(row['nome']) else "", str(row['departamento']) if pd.notna(row['departamento']) else ""))
    conn.commit(); conn.close()

def importar_referencia_funcionarios(df_ref, col_cpf, col_nome, col_depto):
    conn = get_db_connection(); c = conn.cursor(); count = 0
    for _, row in df_ref.iterrows():
        c.execute("INSERT OR REPLACE INTO funcionarios VALUES (?, ?, ?)", (str(row[col_cpf]), str(row[col_nome]) if col_nome and pd.notna(row[col_nome]) else "", str(row[col_depto]) if col_depto and pd.notna(row[col_depto]) else "Geral"))
        count += 1
    conn.commit(); conn.close(); return count

def importar_referencia_rubricas(df_ref, col_cod, col_nome, col_tipo):
    conn = get_db_connection(); c = conn.cursor(); count = 0
    c.execute("SELECT codigo, tipo FROM rubricas"); existentes = {row[0]: row[1] for row in c.fetchall()}
    for _, row in df_ref.iterrows():
        cod = str(row[col_cod])
        tipo = str(row[col_tipo]) if col_tipo and pd.notna(row[col_tipo]) else existentes.get(cod, "Provento")
        nome = str(row[col_nome]) if col_nome and pd.notna(row[col_nome]) else ""
        c.execute("INSERT OR REPLACE INTO rubricas VALUES (?, ?, ?)", (cod, tipo, nome)); count += 1
    conn.commit(); conn.close(); return count

init_db()

# --- BACKUP ---
st.sidebar.header("💾 Backup")
udb = st.sidebar.file_uploader("Restaurar .db", type=["db"])
if udb: 
    if st.sidebar.button("♻️ Restaurar"): 
        with open("esocial_db.db", "wb") as f: f.write(udb.getbuffer())
        st.success("Ok!"); st.rerun()
if st.sidebar.button("Download Backup"):
    with open("esocial_db.db", "rb") as f: st.sidebar.download_button("⬇️ Baixar .db", f.read(), "esocial_backup.db", "application/x-sqlite3")
st.sidebar.divider()

# --- LÓGICA DE LEITURA UNIVERSAL (SEM REGEX) ---

def safe_find_text(element, tag_suffix):
    """Busca tag ignorando namespace (ex: encontra 'ns1:cpfTrab' procurando só 'cpfTrab')"""
    for child in element.iter():
        if child.tag.endswith(tag_suffix):
            return child.text
    return None

def process_xml_file(file_content, filename, rubricas_conhecidas):
    data_rows = []
    novas_rubricas = {} 
    cpfs_encontrados = set()

    try:
        # Lê o XML puro, sem tentar limpar texto com regex (evita corrupção)
        root = ET.fromstring(file_content)
        
        # 1. Encontrar todos os EVENTOS (S-1200) ignorando namespace
        eventos = []
        for elem in root.iter():
            if elem.tag.endswith('evtRemun'):
                eventos.append(elem)
        
        for evt in eventos:
            # Dados do Cabeçalho
            per_apur = safe_find_text(evt, 'perApur') or "N/A"
            cpf_val = safe_find_text(evt, 'cpfTrab') or "N/A"
            cpfs_encontrados.add(cpf_val)

            # 2. Encontrar DEMONSTRATIVOS (dmDev) dentro deste evento
            demonstrativos = []
            for child in evt.iter():
                if child.tag.endswith('dmDev'):
                    demonstrativos.append(child)

            for dm in demonstrativos:
                # Tenta pegar o ID do demonstrativo direto no filho imediato para precisão
                id_demo = "N/A"
                for child in dm:
                    if child.tag.endswith('ideDmDev'):
                        id_demo = child.text
                        break
                
                # 3. Encontrar RUBRICAS (itensRemun) dentro deste demonstrativo
                # Usamos .iter() aqui para pegar até as que estão em 'infoPerAnt' (retroativos)
                itens_no_demonstrativo = []
                for child in dm.iter():
                    if child.tag.endswith('itensRemun'):
                        itens_no_demonstrativo.append(child)
                
                idx_item = 0
                for item in itens_no_demonstrativo:
                    idx_item += 1
                    
                    # Extração segura dos campos
                    cod_rubr = ""
                    vr_rubr = "0.00"
                    referencia = ""
                    
                    for sub in item:
                        if sub.tag.endswith('codRubr'): cod_rubr = sub.text
                        if sub.tag.endswith('vrRubr'): vr_rubr = sub.text
                        if sub.tag.endswith('qtdRubr'): referencia = sub.text
                        if sub.tag.endswith('fatorRubr'): referencia = sub.text
                    
                    if not cod_rubr: continue # Pula se não tiver código (sujeira)

                    try: valor = float(vr_rubr)
                    except: valor = 0.00

                    # Lógica de Classificação
                    nome_final = ""
                    if cod_rubr in rubricas_conhecidas: 
                        tipo_final = rubricas_conhecidas[cod_rubr]['tipo']
                        nome_final = rubricas_conhecidas[cod_rubr]['nome_personalizado']
                    else:
                        code_upper = str(cod_rubr).upper()
                        if any(k in code_upper for k in ['BASE', 'FGTS']): tipo_final = "Informativo"
                        elif any(k in code_upper for k in ['INSS', 'IRRF', 'DESC', 'ADIANT']): tipo_final = "Desconto"
                        else: tipo_final = "Provento"
                        
                        novas_rubricas[cod_rubr] = {'tipo': tipo_final, 'nome_personalizado': ''}
                        rubricas_conhecidas[cod_rubr] = {'tipo': tipo_final, 'nome_personalizado': ''}
                    
                    data_rows.append({
                        "Unique_ID": f"{filename}_{cpf_val}_{id_demo}_{idx_item}_{cod_rubr}",
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "ID_Demonstrativo": id_demo,
                        "Rubrica": cod_rubr,
                        "Descrição": nome_final if nome_final else cod_rubr,
                        "Referencia": referencia,
                        "Tipo": tipo_final,
                        "Valor": valor
                    })
                    
    except Exception as e:
        # Em caso de erro grave no arquivo, ignora e segue pro próximo
        print(f"Erro XML: {e}")
        return [], {}, set()
        
    return data_rows, novas_rubricas, cpfs_encontrados

# --- INTERFACE ---
rubricas_db = carregar_rubricas_db()
funcionarios_db = carregar_funcionarios_db()

st.sidebar.header("📂 Upload")
uploaded_file = st.sidebar.file_uploader("ZIP/XML", type=["zip", "xml"], accept_multiple_files=True)

if uploaded_file:
    if st.sidebar.button("🚀 Processar"):
        with st.spinner('Processando...'):
            all_data = []
            files_to_process = []
            if isinstance(uploaded_file, list):
                for f in uploaded_file:
                    if f.name.endswith('.xml'): files_to_process.append((f.name, f.read()))
                    elif f.name.endswith('.zip'):
                        with zipfile.ZipFile(f) as z:
                            for n in z.namelist(): 
                                if n.endswith('.xml'): files_to_process.append((n, z.read(n)))
            else:
                if uploaded_file.name.endswith('.zip'):
                    with zipfile.ZipFile(uploaded_file) as z:
                        for n in z.namelist(): 
                            if n.endswith('.xml'): files_to_process.append((n, z.read(n)))
                elif uploaded_file.name.endswith('.xml'): files_to_process.append((uploaded_file.name, uploaded_file.read()))

            novas_r_geral = {}
            cpfs_geral = set()
            r_memoria = rubricas_db.copy()

            for fname, fcontent in files_to_process:
                rows, nr, cpfs = process_xml_file(fcontent, fname, r_memoria)
                all_data.extend(rows)
                novas_r_geral.update(nr)
                r_memoria.update(nr)
                cpfs_geral.update(cpfs)

            if novas_r_geral:
                conn = get_db_connection(); c = conn.cursor()
                for cod, dados in novas_r_geral.items():
                    c.execute("INSERT OR IGNORE INTO rubricas VALUES (?, ?, ?)", (str(cod), str(dados['tipo']), str(dados['nome_personalizado'])))
                conn.commit(); conn.close()

            conn = get_db_connection(); c = conn.cursor(); nf = 0
            for cpf in cpfs_geral:
                c.execute("SELECT cpf FROM funcionarios WHERE cpf = ?", (str(cpf),))
                if not c.fetchone():
                    c.execute("INSERT INTO funcionarios VALUES (?, ?, ?)", (str(cpf), "", "Geral")); nf += 1
            conn.commit(); conn.close()
            
            if nf > 0: st.toast(f"{nf} novos funcs.", icon="👥")
            st.session_state['df_bruto'] = pd.DataFrame(all_data)
            st.rerun()

if 'df_bruto' in st.session_state:
    funcionarios_atualizado = carregar_funcionarios_db()
    df = st.session_state['df_bruto'].copy()
    
    if not funcionarios_atualizado.empty:
        db_temp = funcionarios_atualizado.rename(columns={'cpf': 'CPF'})
        df['CPF'] = df['CPF'].astype(str)
        db_temp['CPF'] = db_temp['CPF'].astype(str)
        df = df.merge(db_temp, on="CPF", how="left")
        df["nome"] = df["nome"].fillna(df["CPF"]); df["departamento"] = df["departamento"].fillna("Geral")
    else: df["nome"] = df["CPF"]; df["departamento"] = "Geral"

    rubricas_at = carregar_rubricas_db()
    def atualizar_descricao(row):
        cod = str(row['Rubrica'])
        if cod in rubricas_at:
            nm = rubricas_at[cod]['nome_personalizado']
            if nm: return nm
        return cod
    df['Descrição'] = df.apply(atualizar_descricao, axis=1)
    df['Ano'] = df['Competencia'].str.slice(0, 4)
    df['Mes'] = df['Competencia'].str.slice(5, 7)

    st.sidebar.divider(); st.sidebar.header("📅 Filtros")
    anos_d = sorted(df['Ano'].dropna().unique())
    meses_d = sorted(df['Mes'].dropna().unique())
    anos_s = st.sidebar.multiselect("Anos", anos_d, default=anos_d)
    meses_s = st.sidebar.multiselect("Meses", meses_d, default=meses_d)
    df_f = df[df['Ano'].isin(anos_s) & df['Mes'].isin(meses_s)]
    
    tab1, tab2, tab3 = st.tabs(["📊 Visão Gerencial", "👤 Contracheques", "⚙️ Configurações"])

    with tab1:
        st.subheader("Resumo Financeiro")
        deptos = ["Todos"] + list(df_f["departamento"].unique())
        f_depto = st.selectbox("Depto:", deptos)
        df_v = df_f if f_depto == "Todos" else df_f[df_f["departamento"] == f_depto]
        
        vis = st.radio("Agrupar:", ["Mês a Mês", "Acumulado"], horizontal=True)
        idx = ["departamento", "Competencia"] if vis == "Mês a Mês" else ["departamento"]
        
        res = df_v[df_v["Tipo"].isin(["Provento", "Desconto"])].pivot_table(index=idx, columns="Tipo", values="Valor", aggfunc="sum", fill_value=0).reset_index()
        if "Desconto" not in res.columns: res["Desconto"] = 0
        if "Provento" not in res.columns: res["Provento"] = 0
        res["Liquido"] = res["Provento"] - res["Desconto"]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Proventos", f"R$ {res['Provento'].sum():,.2f}")
        c2.metric("Descontos", f"R$ {res['Desconto'].sum():,.2f}")
        c3.metric("Líquido", f"R$ {res['Liquido'].sum():,.2f}")
        st.dataframe(res.style.format({"Provento": "R$ {:,.2f}", "Desconto": "R$ {:,.2f}", "Liquido": "R$ {:,.2f}"}), use_container_width=True)

    with tab2:
        st.subheader("Consulta Individual")
        c1, c2 = st.columns(2)
        with c1:
            opts = df_f[["CPF", "nome"]].drop_duplicates()
            opts["l"] = opts["nome"].astype(str) + " (" + opts["CPF"].astype(str) + ")"
            sel_f = st.selectbox("Func:", opts["l"]) if not opts.empty else None
            sel_cpf = opts[opts["l"] == sel_f]["CPF"].values[0] if sel_f else None
        with c2:
            if sel_cpf:
                cps = sorted(df_f[df_f["CPF"] == sel_cpf]["Competencia"].unique())
                sel_cp = st.multiselect("Comp:", cps, default=[cps[-1]] if cps else [])
            else: sel_cp = []
        
        # OPÇÃO DE AGRUPAMENTO (Padrão False para ver TUDO)
        agrupar = st.checkbox("Agrupar rubricas repetidas?", value=False)
        
        if sel_cpf and sel_cp:
            mask = (df_f["CPF"] == sel_cpf) & (df_f["Competencia"].isin(sel_cp))
            df_h = df_f[mask].copy()
            
            if agrupar:
                df_show = df_h.groupby(["Rubrica", "Descrição", "Tipo"])["Valor"].sum().reset_index()
                df_show["Referencia"] = "-"
            else:
                df_show = df_h[["Rubrica", "Descrição", "Referencia", "Tipo", "Valor"]].sort_values("Rubrica")

            tp = df_show[df_show["Tipo"] == "Provento"]["Valor"].sum()
            td = df_show[df_show["Tipo"] == "Desconto"]["Valor"].sum()
            
            st.divider()
            st.markdown(f"### {sel_f}")
            k1, k2, k3 = st.columns(3)
            k1.metric("Proventos", f"R$ {tp:,.2f}"); k2.metric("Descontos", f"R$ {td:,.2f}"); k3.metric("Líquido", f"R$ {tp - td:,.2f}")
            def cor(v): return 'color: red' if v == 'Desconto' else 'color: green' if v == 'Provento' else 'color: black'
            st.table(df_show.style.applymap(cor, subset=['Tipo']).format({"Valor": "{:.2f}"}))

    with tab3:
        st.header("⚙️ DB & Importação")
        st.subheader("Importar Planilha")
        ref_file = st.file_uploader("Upload Excel/CSV", type=["xlsx", "csv"])
        if ref_file:
            try:
                if ref_file.name.endswith('.csv'): df_ref = pd.read_csv(ref_file)
                else: df_ref = pd.read_excel(ref_file)
                st.success(f"Lido: {len(df_ref)} linhas.")
                tipo = st.radio("Tipo:", ["Funcionários", "Rubricas"], horizontal=True)
                cols = df_ref.columns.tolist()
                
                if "Func" in tipo:
                    c1, c2, c3 = st.columns(3)
                    cc = c1.selectbox("Col CPF:", cols)
                    cn = c2.selectbox("Col Nome:", ["(Ignorar)"] + cols)
                    cd = c3.selectbox("Col Depto:", ["(Ignorar)"] + cols)
                    if st.button("Importar Funcs"):
                        importar_referencia_funcionarios(df_ref, cc, cn if cn!="(Ignorar)" else None, cd if cd!="(Ignorar)" else None)
                        st.success("Feito!"); st.rerun()
                else:
                    c1, c2, c3 = st.columns(3)
                    cc = c1.selectbox("Col Cód:", cols)
                    cn = c2.selectbox("Col Nome:", ["(Ignorar)"] + cols)
                    ct = c3.selectbox("Col Tipo:", ["(Ignorar)"] + cols)
                    if st.button("Importar Rubricas"):
                        importar_referencia_rubricas(df_ref, cc, cn if cn!="(Ignorar)" else None, ct if ct!="(Ignorar)" else None)
                        st.success("Feito!"); st.rerun()
            except Exception as e: st.error(f"Erro: {e}.")

        st.divider(); c1, c2 = st.columns(2)
        with c1:
            st.subheader("Funcionários")
            df_f_ed = st.data_editor(carregar_funcionarios_db(), num_rows="dynamic", key="ed_f")
            if st.button("Salvar F"): salvar_alteracoes_funcionarios(df_f_ed); st.success("Salvo"); st.rerun()
        with c2:
            st.subheader("Rubricas")
            df_r_view = pd.DataFrame.from_dict(carregar_rubricas_db(), orient='index').reset_index().rename(columns={'index': 'codigo'})
            if df_r_view.empty: df_r_view = pd.DataFrame(columns=['codigo', 'tipo', 'nome_personalizado'])
            df_r_ed = st.data_editor(df_r_view, key="ed_r")
            if st.button("Salvar R"): salvar_alteracoes_rubricas(df_r_ed); st.success("Salvo"); st.rerun()
else:
    st.info("👈 Envie o XML.")
