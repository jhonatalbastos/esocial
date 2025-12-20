import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import sqlite3
import os

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Pro", layout="wide", page_icon="🏢")

st.title("🏢 Gestor de Folha eSocial (Com Relatório Analítico)")
st.markdown("Sistema com atualização dinâmica e relatórios analíticos por departamento.")

# --- BANCO DE DADOS E PERSISTÊNCIA ---
DB_FILE = 'esocial_db.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (codigo TEXT PRIMARY KEY, tipo TEXT, nome_personalizado TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (cpf TEXT PRIMARY KEY, nome TEXT, departamento TEXT)''')
    try: c.execute("ALTER TABLE rubricas ADD COLUMN nome_personalizado TEXT")
    except sqlite3.OperationalError: pass
    conn.commit(); conn.close()

    # --- AUTO-LOAD DO GITHUB/ARQUIVO LOCAL ---
    if os.path.exists("config_padrao.xlsx"):
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT count(*) FROM rubricas")
            if c.fetchone()[0] == 0:
                df_padrao = pd.read_excel("config_padrao.xlsx")
                for _, row in df_padrao.iterrows():
                    c.execute("INSERT OR IGNORE INTO rubricas VALUES (?, ?, ?)", 
                              (str(row['codigo']), str(row['tipo']), str(row['nome_personalizado'])))
                conn.commit()
            conn.close()
        except Exception as e: print(f"Erro config_padrao: {e}")

def get_db_connection(): return sqlite3.connect(DB_FILE)

# Funções de Carregamento
def carregar_rubricas_db():
    conn = get_db_connection()
    try: df = pd.read_sql("SELECT * FROM rubricas", conn)
    except: df = pd.DataFrame(columns=["codigo", "tipo", "nome_personalizado"])
    conn.close()
    if not df.empty: return df
    return pd.DataFrame(columns=["codigo", "tipo", "nome_personalizado"])

def carregar_funcionarios_db():
    conn = get_db_connection()
    try: df = pd.read_sql("SELECT * FROM funcionarios", conn)
    except: df = pd.DataFrame(columns=["cpf", "nome", "departamento"])
    conn.close()
    return df

# Funções de Salvamento
def salvar_alteracoes_rubricas(df_edited):
    conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM rubricas")
    for _, row in df_edited.iterrows():
        c.execute("INSERT INTO rubricas VALUES (?, ?, ?)", 
                  (str(row['codigo']), str(row['tipo']), str(row['nome_personalizado']) if pd.notna(row['nome_personalizado']) else ""))
    conn.commit(); conn.close()

def salvar_alteracoes_funcionarios(df_edited):
    conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM funcionarios")
    for _, row in df_edited.iterrows():
        c.execute("INSERT INTO funcionarios VALUES (?, ?, ?)", 
                  (str(row['cpf']), str(row['nome']) if pd.notna(row['nome']) else "", str(row['departamento']) if pd.notna(row['departamento']) else ""))
    conn.commit(); conn.close()

# Importadores
def importar_referencia_xlsx(df_ref, tipo_import, map_cols):
    conn = get_db_connection(); c = conn.cursor(); count = 0
    if tipo_import == "func":
        for _, row in df_ref.iterrows():
            cpf = str(row[map_cols['cpf']])
            nome = str(row[map_cols['nome']]) if map_cols['nome'] else ""
            depto = str(row[map_cols['depto']]) if map_cols['depto'] else "Geral"
            c.execute("INSERT OR REPLACE INTO funcionarios VALUES (?, ?, ?)", (cpf, nome, depto)); count += 1
    else:
        c.execute("SELECT codigo, tipo FROM rubricas"); existentes = {row[0]: row[1] for row in c.fetchall()}
        for _, row in df_ref.iterrows():
            cod = str(row[map_cols['cod']])
            nome = str(row[map_cols['nome']]) if map_cols['nome'] else ""
            tipo = str(row[map_cols['tipo']]) if map_cols['tipo'] else existentes.get(cod, "Provento")
            c.execute("INSERT OR REPLACE INTO rubricas VALUES (?, ?, ?)", (cod, tipo, nome)); count += 1
    conn.commit(); conn.close(); return count

init_db()

# --- BACKUP E CONFIGURAÇÃO ---
with st.sidebar.expander("💾 Backup e Persistência", expanded=False):
    st.info("O Streamlit Cloud reseta o sistema ao reiniciar. Use as opções abaixo.")
    with open(DB_FILE, "rb") as f:
        st.download_button("⬇️ Baixar Banco de Dados (.db)", f.read(), "esocial_backup.db", "application/x-sqlite3")
    
    uploaded_db = st.file_uploader("Restaurar Banco (.db)", type=["db"])
    if uploaded_db:
        if st.button("♻️ Restaurar DB"):
            with open(DB_FILE, "wb") as f: f.write(uploaded_db.getbuffer())
            st.success("Restaurado! Recarregando..."); st.rerun()
            
    st.divider()
    st.markdown("**Configuração Permanente:**")
    df_r_export = carregar_rubricas_db()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer: df_r_export.to_excel(writer, index=False)
    st.download_button("⬇️ Baixar Configuração (.xlsx)", output.getvalue(), "config_padrao.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.sidebar.divider()

# --- HELPER: LETRAS EXCEL ---
def get_col_letter(n):
    string = ""
    while n >= 0: string = chr(n % 26 + 65) + string; n = n // 26 - 1
    return string

# --- PROCESSAMENTO XML ---
def safe_find_text(element, tag_suffix):
    for child in element.iter():
        if child.tag.endswith(tag_suffix): return child.text
    return None

def process_xml_file(file_content, filename):
    data_rows = []
    comps_arquivo = set()
    try:
        root = ET.fromstring(file_content)
        eventos = [e for e in root.iter() if e.tag.endswith('evtRemun')]
        for evt in eventos:
            per_apur = safe_find_text(evt, 'perApur') or "N/A"
            comps_arquivo.add(per_apur)
            
            ind_apuracao = safe_find_text(evt, 'indApuracao')
            tipo_folha = "13º Salário" if (ind_apuracao == '2' or per_apur.endswith('-13')) else "Mensal"
            cpf_val = safe_find_text(evt, 'cpfTrab') or "N/A"

            demonstrativos = [d for d in evt.iter() if d.tag.endswith('dmDev')]
            for dm in demonstrativos:
                id_demo = "N/A"
                for child in dm:
                    if child.tag.endswith('ideDmDev'): id_demo = child.text; break
                
                itens = [i for i in dm.iter() if i.tag.endswith('itensRemun')]
                idx = 0
                for item in itens:
                    idx += 1
                    cod_rubr = ""; vr_rubr = "0.00"; referencia = ""
                    for sub in item:
                        if sub.tag.endswith('codRubr'): cod_rubr = sub.text
                        if sub.tag.endswith('vrRubr'): vr_rubr = sub.text
                        if sub.tag.endswith('qtdRubr'): referencia = sub.text
                        if sub.tag.endswith('fatorRubr'): referencia = sub.text
                    
                    if not cod_rubr: continue
                    try: valor = float(vr_rubr)
                    except: valor = 0.00
                    
                    data_rows.append({
                        "Unique_ID": f"{filename}_{cpf_val}_{id_demo}_{idx}_{cod_rubr}",
                        "Competencia": per_apur,
                        "Tipo_Folha": tipo_folha,
                        "CPF": cpf_val,
                        "Rubrica": cod_rubr,
                        "Referencia": referencia,
                        "Valor": valor
                    })
    except Exception as e: print(f"Erro XML: {e}"); return [], set()
    return data_rows, comps_arquivo

# --- APLICAÇÃO DINÂMICA ---
def aplicar_configuracoes_dinamicas(df_bruto):
    if df_bruto.empty: return df_bruto
    df_rubricas_db = carregar_rubricas_db()
    df_funcs_db = carregar_funcionarios_db()
    
    if df_rubricas_db.empty:
        df_bruto['Tipo'] = 'Provento'; df_bruto['Descrição'] = df_bruto['Rubrica']
    else:
        df_bruto['Rubrica'] = df_bruto['Rubrica'].astype(str)
        df_rubricas_db['codigo'] = df_rubricas_db['codigo'].astype(str)
        df_merged = df_bruto.merge(df_rubricas_db, left_on='Rubrica', right_on='codigo', how='left')
        
        def estimar_tipo(row):
            if pd.notna(row['tipo']): return row['tipo']
            code = str(row['Rubrica']).upper()
            if any(x in code for x in ['DESC', 'INSS', 'IRRF']): return 'Desconto'
            return 'Provento'

        def definir_nome(row):
            if pd.notna(row['nome_personalizado']) and row['nome_personalizado'] != "": return row['nome_personalizado']
            return row['Rubrica']

        df_merged['Tipo'] = df_merged.apply(estimar_tipo, axis=1)
        df_merged['Descrição'] = df_merged.apply(definir_nome, axis=1)
        df_bruto = df_merged.drop(columns=['codigo', 'tipo', 'nome_personalizado'])

    if df_funcs_db.empty:
        df_bruto['nome'] = df_bruto['CPF']; df_bruto['departamento'] = 'Geral'
    else:
        df_bruto['CPF'] = df_bruto['CPF'].astype(str)
        df_funcs_db['cpf'] = df_funcs_db['cpf'].astype(str)
        df_merged_f = df_bruto.merge(df_funcs_db.rename(columns={'cpf':'CPF_KEY'}), left_on='CPF', right_on='CPF_KEY', how='left')
        df_merged_f['nome'] = df_merged_f['nome'].fillna(df_merged_f['CPF'])
        df_merged_f['departamento'] = df_merged_f['departamento'].fillna('Geral')
        df_bruto = df_merged_f.drop(columns=['CPF_KEY'])

    df_bruto['Ano'] = df_bruto['Competencia'].str.slice(0, 4)
    df_bruto['Mes'] = df_bruto['Competencia'].str.slice(5, 7)
    return df_bruto

# --- INTERFACE ---
st.sidebar.header("📂 Upload")
uploaded_file = st.sidebar.file_uploader("ZIP/XML", type=["zip", "xml"], accept_multiple_files=True)

if uploaded_file:
    if st.sidebar.button("🚀 Processar"):
        with st.spinner('Lendo XMLs...'):
            all_data = []
            files = []
            if isinstance(uploaded_file, list):
                for f in uploaded_file:
                    if f.name.endswith('.xml'): files.append((f.name, f.read()))
                    elif f.name.endswith('.zip'):
                        with zipfile.ZipFile(f) as z:
                            for n in z.namelist(): 
                                if n.endswith('.xml'): files.append((n, z.read(n)))
            else: pass

            comps_total = set()
            rubricas_encontradas = set()
            cpfs_encontrados = set()

            for fname, fcontent in files:
                rows, comps = process_xml_file(fcontent, fname)
                all_data.extend(rows)
                comps_total.update(comps)
                for r in rows:
                    rubricas_encontradas.add(r['Rubrica']); cpfs_encontrados.add(r['CPF'])

            conn = get_db_connection(); c = conn.cursor()
            for cod in rubricas_encontradas: c.execute("INSERT OR IGNORE INTO rubricas (codigo, tipo, nome_personalizado) VALUES (?, ?, ?)", (str(cod), 'Provento', ''))
            for cpf in cpfs_encontrados: c.execute("INSERT OR IGNORE INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", (str(cpf), '', 'Geral'))
            conn.commit(); conn.close()

            st.session_state['df_raw'] = pd.DataFrame(all_data)
            st.session_state['comps_msg'] = sorted(list(comps_total))
            st.rerun()

# --- EXIBIÇÃO ---
if 'df_raw' in st.session_state:
    if 'comps_msg' in st.session_state:
        st.success(f"Dados Carregados! Competências: {', '.join(st.session_state['comps_msg'])}")

    df_completo = aplicar_configuracoes_dinamicas(st.session_state['df_raw'].copy())

    st.sidebar.divider(); st.sidebar.header("📅 Filtros")
    anos = sorted(df_completo['Ano'].unique())
    meses = sorted(df_completo['Mes'].unique())
    anos_sel = st.sidebar.multiselect("Ano", anos, default=anos)
    meses_sel = st.sidebar.multiselect("Mês", meses, default=meses)
    tipos_folha = sorted(df_completo['Tipo_Folha'].unique())
    tipos_sel = st.sidebar.multiselect("Tipo", tipos_folha, default=tipos_folha)

    df_filtered = df_completo[df_completo['Ano'].isin(anos_sel) & df_completo['Mes'].isin(meses_sel) & df_completo['Tipo_Folha'].isin(tipos_sel)]

    tab1, tab2, tab3 = st.tabs(["📊 Visão Gerencial", "👤 Contracheques", "⚙️ Configurações"])

    with tab1:
        deptos = ["Todos"] + list(df_filtered["departamento"].unique())
        sel_depto = st.selectbox("Selecione o Departamento para Análise:", deptos)
        df_v = df_filtered if sel_depto == "Todos" else df_filtered[df_filtered["departamento"] == sel_depto]
        
        st.subheader(f"Resumo Financeiro - {sel_depto}")
        
        # 1. TOTAIS (CARDS)
        resumo_geral = df_v[df_v["Tipo"].isin(["Provento", "Desconto"])].groupby("Tipo")["Valor"].sum()
        tot_p = resumo_geral.get("Provento", 0); tot_d = resumo_geral.get("Desconto", 0)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Proventos", f"R$ {tot_p:,.2f}")
        c2.metric("Total Descontos", f"R$ {tot_d:,.2f}")
        c3.metric("Líquido", f"R$ {tot_p - tot_d:,.2f}")
        
        st.divider()
        
        # 2. TABELA ANALÍTICA (ESTILO PDF)
        st.markdown("### 📋 Detalhamento por Rubrica (Resumo da Folha)")
        
        # Filtra apenas proventos e descontos para o relatório
        df_analitico = df_v[df_v["Tipo"].isin(["Provento", "Desconto"])].copy()
        
        # Pivot table: Agrupa por Rubrica e Descrição, colunas são Provento/Desconto
        resumo_rubricas = df_analitico.pivot_table(
            index=["Rubrica", "Descrição"], 
            columns="Tipo", 
            values="Valor", 
            aggfunc="sum", 
            fill_value=0
        ).reset_index()

        # Garante colunas
        if "Provento" not in resumo_rubricas.columns: resumo_rubricas["Provento"] = 0.0
        if "Desconto" not in resumo_rubricas.columns: resumo_rubricas["Desconto"] = 0.0
        
        # Ordena pelo código da rubrica
        resumo_rubricas.sort_values("Rubrica", inplace=True)

        # Exibição
        st.dataframe(
            resumo_rubricas.style.format({"Provento": "R$ {:,.2f}", "Desconto": "R$ {:,.2f}"}),
            use_container_width=True,
            height=500
        )
        
        # Botão de Download do Relatório Analítico
        output_analitico = io.BytesIO()
        with pd.ExcelWriter(output_analitico, engine='xlsxwriter') as writer:
            resumo_rubricas.to_excel(writer, index=False, sheet_name=f"Resumo_{sel_depto[:20]}")
        
        st.download_button(
            label=f"📥 Baixar Resumo Analítico ({sel_depto}) em Excel",
            data=output_analitico.getvalue(),
            file_name=f"Resumo_Analitico_{sel_depto}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            opts = df_filtered[["CPF", "nome"]].drop_duplicates()
            opts["label"] = opts["nome"] + " (" + opts["CPF"] + ")"
            sel_func_l = st.selectbox("Funcionário", opts["label"]) if not opts.empty else None
            sel_cpf = opts[opts["label"] == sel_func_l]["CPF"].values[0] if sel_func_l else None
        with c2:
            if sel_cpf:
                df_filtered['C_Label'] = df_filtered['Competencia'] + " (" + df_filtered['Tipo_Folha'] + ")"
                cps = sorted(df_filtered[df_filtered["CPF"] == sel_cpf]["C_Label"].unique())
                sel_comp = st.multiselect("Competência", cps, default=[cps[-1]] if cps else [])
            else: sel_comp = []

        agrupar = st.checkbox("Agrupar repetidos (Somar)", value=False)
        if sel_cpf and sel_comp:
            df_h = df_filtered[(df_filtered["CPF"] == sel_cpf) & (df_filtered["C_Label"].isin(sel_comp))].copy()
            if agrupar:
                df_show = df_h.groupby(["Rubrica", "Descrição", "Tipo"])["Valor"].sum().reset_index(); df_show["Referencia"] = "-"
            else:
                df_show = df_h[["Rubrica", "Descrição", "Referencia", "Tipo", "Valor"]].sort_values("Rubrica")
            
            t_p = df_show[df_show["Tipo"] == "Provento"]["Valor"].sum(); t_d = df_show[df_show["Tipo"] == "Desconto"]["Valor"].sum()
            st.divider(); st.markdown(f"### {sel_func_l}"); k1, k2, k3 = st.columns(3)
            k1.metric("Proventos", f"R$ {t_p:,.2f}"); k2.metric("Descontos", f"R$ {t_d:,.2f}"); k3.metric("Líquido", f"R$ {t_p - t_d:,.2f}")
            def color(v): return 'color: red' if v == 'Desconto' else 'color: green' if v == 'Provento' else 'color: black'
            st.table(df_show.style.applymap(color, subset=['Tipo']).format({"Valor": "{:.2f}"}))

    with tab3:
        st.header("⚙️ Configurações (Edição Real-Time)")
        with st.expander("📥 Importar Referência (Excel)", expanded=True):
            f_ref = st.file_uploader("Arquivo .xlsx", type=["xlsx"])
            if f_ref:
                df_ref = pd.read_excel(f_ref); cols = df_ref.columns.tolist()
                opts_col = [f"{get_col_letter(i)} - {c}" for i, c in enumerate(cols)]; map_idx = {o: c for o, c in zip(opts_col, cols)}
                t_imp = st.radio("Tipo", ["Funcionários", "Rubricas"], horizontal=True)
                c1, c2, c3 = st.columns(3)
                if t_imp == "Funcionários":
                    sc = c1.selectbox("CPF", opts_col); sn = c2.selectbox("Nome", ["(Ignorar)"]+opts_col); sd = c3.selectbox("Depto", ["(Ignorar)"]+opts_col)
                    if st.button("Importar"):
                        importar_referencia_xlsx(df_ref, "func", {'cpf': map_idx[sc], 'nome': map_idx[sn] if sn != "(Ignorar)" else None, 'depto': map_idx[sd] if sd != "(Ignorar)" else None})
                        st.success("Importado!"); st.rerun()
                else:
                    sc = c1.selectbox("Código", opts_col); sn = c2.selectbox("Nome Evento", ["(Ignorar)"]+opts_col); st_type = c3.selectbox("Tipo", ["(Ignorar)"]+opts_col)
                    if st.button("Importar"):
                        importar_referencia_xlsx(df_ref, "rubr", {'cod': map_idx[sc], 'nome': map_idx[sn] if sn != "(Ignorar)" else None, 'tipo': map_idx[st_type] if st_type != "(Ignorar)" else None})
                        st.success("Importado!"); st.rerun()
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Funcionários"); ed_f = st.data_editor(carregar_funcionarios_db(), num_rows="dynamic", key="edf")
            if st.button("Salvar Funcionários"): salvar_alteracoes_funcionarios(ed_f); st.success("Salvo!"); st.rerun()
        with c2:
            st.subheader("Rubricas"); ed_r = st.data_editor(carregar_rubricas_db(), num_rows="dynamic", key="edr")
            if st.button("Salvar Rubricas"): salvar_alteracoes_rubricas(ed_r); st.success("Salvo!"); st.rerun()
else:
    st.info("👈 Envie seus XMLs para começar.")
