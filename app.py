import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import re
import sqlite3

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Master", layout="wide", page_icon="🏢")

st.title("🏢 Gestor de Folha eSocial (Persistente)")
st.markdown("""
Sistema de auditoria com importação de referências, nomes personalizados e backup.
""")

# --- GERENCIAMENTO DO BANCO DE DADOS ---

def init_db():
    conn = sqlite3.connect('esocial_db.db')
    c = conn.cursor()
    # Cria tabelas se não existirem
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (
                    codigo TEXT PRIMARY KEY, 
                    tipo TEXT,
                    nome_personalizado TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (
                    cpf TEXT PRIMARY KEY, 
                    nome TEXT, 
                    departamento TEXT
                )''')
    
    # Migração segura
    try: c.execute("ALTER TABLE rubricas ADD COLUMN nome_personalizado TEXT")
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect('esocial_db.db')

def carregar_rubricas_db():
    conn = get_db_connection()
    try: 
        df = pd.read_sql("SELECT * FROM rubricas", conn)
    except: 
        df = pd.DataFrame(columns=["codigo", "tipo", "nome_personalizado"])
    conn.close()
    if not df.empty:
        return df.set_index("codigo")[["tipo", "nome_personalizado"]].to_dict('index')
    return {}

def carregar_funcionarios_db():
    conn = get_db_connection()
    try: df = pd.read_sql("SELECT * FROM funcionarios", conn)
    except: df = pd.DataFrame(columns=["cpf", "nome", "departamento"])
    conn.close()
    return df

def salvar_alteracoes_rubricas(df_edited):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM rubricas")
    for index, row in df_edited.iterrows():
        cod = str(row['codigo'])
        tipo = str(row['tipo'])
        nome = str(row['nome_personalizado']) if pd.notna(row['nome_personalizado']) else ""
        c.execute("INSERT INTO rubricas (codigo, tipo, nome_personalizado) VALUES (?, ?, ?)", (cod, tipo, nome))
    conn.commit()
    conn.close()

def salvar_alteracoes_funcionarios(df_edited):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM funcionarios")
    for index, row in df_edited.iterrows():
        cpf = str(row['cpf'])
        nome = str(row['nome']) if pd.notna(row['nome']) else ""
        depto = str(row['departamento']) if pd.notna(row['departamento']) else ""
        c.execute("INSERT INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", (cpf, nome, depto))
    conn.commit()
    conn.close()

def importar_referencia_funcionarios(df_ref, col_cpf, col_nome, col_depto):
    conn = get_db_connection()
    c = conn.cursor()
    count = 0
    for _, row in df_ref.iterrows():
        cpf = str(row[col_cpf])
        nome = str(row[col_nome]) if col_nome and pd.notna(row[col_nome]) else ""
        depto = str(row[col_depto]) if col_depto and pd.notna(row[col_depto]) else "Geral"
        
        # Upsert: Atualiza se existir, insere se não
        c.execute("INSERT OR REPLACE INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", (cpf, nome, depto))
        count += 1
    conn.commit()
    conn.close()
    return count

def importar_referencia_rubricas(df_ref, col_cod, col_nome, col_tipo):
    conn = get_db_connection()
    c = conn.cursor()
    count = 0
    # Carrega existentes para não perder o 'tipo' se a planilha só tiver 'nome'
    c.execute("SELECT codigo, tipo FROM rubricas")
    existentes = {row[0]: row[1] for row in c.fetchall()}

    for _, row in df_ref.iterrows():
        cod = str(row[col_cod])
        nome = str(row[col_nome]) if col_nome and pd.notna(row[col_nome]) else ""
        
        # Se a planilha tem coluna de tipo, usa. Se não, tenta manter o que já existe ou define padrão.
        if col_tipo and pd.notna(row[col_tipo]):
            tipo = str(row[col_tipo])
        else:
            tipo = existentes.get(cod, "Provento") # Padrão se for novo e sem tipo

        c.execute("INSERT OR REPLACE INTO rubricas (codigo, tipo, nome_personalizado) VALUES (?, ?, ?)", (cod, tipo, nome))
        count += 1
    conn.commit()
    conn.close()
    return count

init_db()

# --- SIDEBAR (BACKUP) ---
st.sidebar.header("💾 Backup e Restauração")
st.sidebar.info("Use isto para salvar seu trabalho antes de fechar.")
uploaded_db = st.sidebar.file_uploader("Restaurar Backup (.db)", type=["db"])
if uploaded_db:
    if st.sidebar.button("♻️ Restaurar Dados"):
        with open("esocial_db.db", "wb") as f: f.write(uploaded_db.getbuffer())
        st.success("Restaurado! Recarregando..."); st.rerun()

if st.sidebar.button("Preparar Download Backup"):
    with open("esocial_db.db", "rb") as f: st.sidebar.download_button("⬇️ Baixar .db", f.read(), "esocial_backup.db", "application/x-sqlite3")

st.sidebar.divider()

# --- LÓGICA PROCESSAMENTO XML ---
def clean_xml_content(xml_content):
    try:
        if isinstance(xml_content, bytes): xml_str = xml_content.decode('utf-8', errors='ignore')
        else: xml_str = xml_content
        xml_str = re.sub(r'\sxmlns(:[a-zA-Z0-9]+)?="[^"]+"', '', xml_str)
        xml_str = re.sub(r'<([a-zA-Z0-9]+):', '<', xml_str)
        xml_str = re.sub(r'</([a-zA-Z0-9]+):', '</', xml_str)
        return xml_str
    except: return xml_content

def estimar_tipo_rubrica_inicial(codigo):
    code_upper = str(codigo).upper()
    keywords_desc = ['INSS', 'IRRF', 'DESC', 'ADIANT', 'FALT', 'ATRASO', 'RETENCAO', 'VALE', 'VR', 'VT']
    keywords_info = ['BASE', 'FGTS']
    for k in keywords_info:
        if k in code_upper: return "Informativo"
    for k in keywords_desc:
        if k in code_upper: return "Desconto"
    return "Provento"

def process_xml_file(file_content, filename, rubricas_conhecidas):
    data_rows = []
    novas_rubricas = {} 
    cpfs_encontrados = set()
    try:
        clean_xml = clean_xml_content(file_content)
        root = ET.fromstring(clean_xml)
        eventos = root.findall(".//evtRemun")
        for evt in eventos:
            ide_evento = evt.find("ideEvento")
            per_apur = ide_evento.find("perApur").text if ide_evento else "N/A"
            ide_trab = evt.find("ideTrabalhador")
            cpf_val = ide_trab.find("cpfTrab").text if ide_trab is not None else "N/A"
            cpfs_encontrados.add(cpf_val)
            demonstrativos = evt.findall(".//dmDev")
            for dm in demonstrativos:
                itens = dm.findall(".//itensRemun")
                for item in itens:
                    cod_rubr = item.find("codRubr").text if item.find("codRubr") is not None else ""
                    vr_rubr = item.find("vrRubr").text if item.find("vrRubr") is not None else "0.00"
                    try: valor = float(vr_rubr)
                    except: valor = 0.00
                    
                    nome_final = ""
                    if cod_rubr in rubricas_conhecidas: 
                        tipo_final = rubricas_conhecidas[cod_rubr]['tipo']
                        nome_final = rubricas_conhecidas[cod_rubr]['nome_personalizado']
                    else:
                        tipo_final = estimar_tipo_rubrica_inicial(cod_rubr)
                        novas_rubricas[cod_rubr] = {'tipo': tipo_final, 'nome_personalizado': ''}
                        rubricas_conhecidas[cod_rubr] = {'tipo': tipo_final, 'nome_personalizado': ''}
                    
                    data_rows.append({
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "Rubrica": cod_rubr,
                        "Descrição": nome_final if nome_final else cod_rubr,
                        "Tipo": tipo_final,
                        "Valor": valor
                    })
    except: return [], {}, set()
    return data_rows, novas_rubricas, cpfs_encontrados

# --- INTERFACE ---
rubricas_db = carregar_rubricas_db()
funcionarios_db = carregar_funcionarios_db()

st.sidebar.header("📂 Arquivos eSocial")
uploaded_file = st.sidebar.file_uploader("Upload ZIP/XML", type=["zip", "xml"], accept_multiple_files=True)

if uploaded_file:
    if st.sidebar.button("🚀 Processar Arquivos"):
        with st.spinner('Lendo arquivos...'):
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

            novas_rubricas_geral = {}
            cpfs_geral = set()
            rubricas_memoria = rubricas_db.copy()

            for fname, fcontent in files_to_process:
                rows, novas_r, cpfs = process_xml_file(fcontent, fname, rubricas_memoria)
                all_data.extend(rows)
                novas_rubricas_geral.update(novas_r)
                rubricas_memoria.update(novas_r)
                cpfs_geral.update(cpfs)

            # Salva Novas Rubricas
            if novas_rubricas_geral:
                conn = get_db_connection()
                c = conn.cursor()
                for cod, dados in novas_rubricas_geral.items():
                    c.execute("INSERT OR IGNORE INTO rubricas (codigo, tipo, nome_personalizado) VALUES (?, ?, ?)", 
                              (str(cod), str(dados['tipo']), str(dados['nome_personalizado'])))
                conn.commit()
                conn.close()

            # Salva Novos Funcionários
            conn = get_db_connection()
            c = conn.cursor()
            novos_funcs = 0
            for cpf in cpfs_geral:
                c.execute("SELECT cpf FROM funcionarios WHERE cpf = ?", (str(cpf),))
                if not c.fetchone():
                    c.execute("INSERT INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", (str(cpf), "", "Geral"))
                    novos_funcs += 1
            conn.commit()
            conn.close()
            
            if novos_funcs > 0: st.toast(f"{novos_funcs} novos funcionários.", icon="👥")
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
        df["nome"] = df["nome"].fillna(df["CPF"]) 
        df["departamento"] = df["departamento"].fillna("Geral")
    else:
        df["nome"] = df["CPF"]; df["departamento"] = "Geral"

    rubricas_atualizadas = carregar_rubricas_db()
    def atualizar_descricao(row):
        cod = str(row['Rubrica'])
        if cod in rubricas_atualizadas:
            nome_personalizado = rubricas_atualizadas[cod]['nome_personalizado']
            if nome_personalizado: return nome_personalizado
        return cod
    df['Descrição'] = df.apply(atualizar_descricao, axis=1)
    df['Ano'] = df['Competencia'].str.slice(0, 4)
    df['Mes'] = df['Competencia'].str.slice(5, 7)

    st.sidebar.divider()
    st.sidebar.header("📅 Filtros")
    anos_disp = sorted(df['Ano'].dropna().unique())
    meses_disp = sorted(df['Mes'].dropna().unique())
    anos_sel = st.sidebar.multiselect("Anos", anos_disp, default=anos_disp)
    meses_sel = st.sidebar.multiselect("Meses", meses_disp, default=meses_disp)
    df_filtrado = df[df['Ano'].isin(anos_sel) & df['Mes'].isin(meses_sel)]
    
    tab1, tab2, tab3 = st.tabs(["📊 Visão Gerencial", "👤 Contracheques", "⚙️ Configurações"])

    with tab1:
        st.subheader("Resumo Financeiro")
        deptos = ["Todos"] + list(df_filtrado["departamento"].unique())
        filtro_depto = st.selectbox("Filtrar Departamento:", deptos)
        df_view = df_filtrado if filtro_depto == "Todos" else df_filtrado[df_filtrado["departamento"] == filtro_depto]
        
        visao = st.radio("Agrupar:", ["Mês a Mês", "Acumulado"], horizontal=True)
        idx = ["departamento", "Competencia"] if visao == "Mês a Mês" else ["departamento"]
        
        resumo = df_view[df_view["Tipo"].isin(["Provento", "Desconto"])].pivot_table(
            index=idx, columns="Tipo", values="Valor", aggfunc="sum", fill_value=0
        ).reset_index()
        
        if "Desconto" not in resumo.columns: resumo["Desconto"] = 0
        if "Provento" not in resumo.columns: resumo["Provento"] = 0
        resumo["Liquido"] = resumo["Provento"] - resumo["Desconto"]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Proventos", f"R$ {resumo['Provento'].sum():,.2f}")
        c2.metric("Descontos", f"R$ {resumo['Desconto'].sum():,.2f}")
        c3.metric("Líquido", f"R$ {resumo['Liquido'].sum():,.2f}")
        st.dataframe(resumo.style.format({"Provento": "R$ {:,.2f}", "Desconto": "R$ {:,.2f}", "Liquido": "R$ {:,.2f}"}), use_container_width=True)

    with tab2:
        st.subheader("Consulta Individual")
        c1, c2 = st.columns(2)
        with c1:
            opts = df_filtrado[["CPF", "nome"]].drop_duplicates()
            opts["lbl"] = opts["nome"].astype(str) + " (" + opts["CPF"].astype(str) + ")"
            func_sel = st.selectbox("Funcionário:", opts["lbl"]) if not opts.empty else None
            cpf_sel = opts[opts["lbl"] == func_sel]["CPF"].values[0] if func_sel else None
        with c2:
            if cpf_sel:
                comps = sorted(df_filtrado[df_filtrado["CPF"] == cpf_sel]["Competencia"].unique())
                comp_sel = st.multiselect("Competências:", comps, default=[comps[-1]] if comps else [])
            else: comp_sel = []
        
        if cpf_sel and comp_sel:
            mask = (df_filtrado["CPF"] == cpf_sel) & (df_filtrado["Competencia"].isin(comp_sel))
            df_h = df_filtrado[mask].copy()
            df_g = df_h.groupby(["Rubrica", "Descrição", "Tipo"])["Valor"].sum().reset_index()
            
            tot_p = df_g[df_g["Tipo"] == "Provento"]["Valor"].sum()
            tot_d = df_g[df_g["Tipo"] == "Desconto"]["Valor"].sum()
            
            st.divider()
            st.markdown(f"### {func_sel}")
            col1, col2, col3 = st.columns(3)
            col1.metric("Proventos", f"R$ {tot_p:,.2f}")
            col2.metric("Descontos", f"R$ {tot_d:,.2f}")
            col3.metric("Líquido", f"R$ {tot_p - tot_d:,.2f}")
            
            def color(val): return 'color: red' if val == 'Desconto' else 'color: green' if val == 'Provento' else 'color: black'
            st.table(df_g[["Rubrica", "Descrição", "Tipo", "Valor"]].style.applymap(color, subset=['Tipo']).format({"Valor": "{:.2f}"}))

    with tab3:
        st.header("⚙️ Banco de Dados")
        
        st.subheader("📥 Importar Planilha de Referência")
        st.markdown("Use sua planilha `.xlsx` ou `.csv` para preencher os nomes de CPFs ou Eventos em massa.")
        
        ref_file = st.file_uploader("Upload Planilha de Referência", type=["xlsx", "csv"])
        
        if ref_file:
            try:
                if ref_file.name.endswith('.csv'): df_ref = pd.read_csv(ref_file)
                else: df_ref = pd.read_excel(ref_file)
                
                st.success(f"Planilha carregada! {len(df_ref)} linhas.")
                st.dataframe(df_ref.head(3))
                
                tipo_import = st.radio("O que você quer importar?", ["Funcionários (CPF/Nome)", "Rubricas (Códigos/Eventos)"], horizontal=True)
                
                cols = df_ref.columns.tolist()
                
                if "Funcionários" in tipo_import:
                    c1, c2, c3 = st.columns(3)
                    col_cpf = c1.selectbox("Coluna CPF:", cols)
                    col_nome = c2.selectbox("Coluna Nome:", ["(Ignorar)"] + cols)
                    col_depto = c3.selectbox("Coluna Departamento:", ["(Ignorar)"] + cols)
                    
                    if st.button("Importar Funcionários"):
                        n_col = col_nome if col_nome != "(Ignorar)" else None
                        d_col = col_depto if col_depto != "(Ignorar)" else None
                        qtd = importar_referencia_funcionarios(df_ref, col_cpf, n_col, d_col)
                        st.success(f"{qtd} funcionários atualizados!")
                        st.rerun()
                        
                else:
                    c1, c2, c3 = st.columns(3)
                    col_cod = c1.selectbox("Coluna Código Rubrica:", cols)
                    col_nome = c2.selectbox("Coluna Nome do Evento:", ["(Ignorar)"] + cols)
                    col_tipo = c3.selectbox("Coluna Tipo (Provento/Desconto):", ["(Ignorar)"] + cols)
                    
                    if st.button("Importar Rubricas"):
                        n_col = col_nome if col_nome != "(Ignorar)" else None
                        t_col = col_tipo if col_tipo != "(Ignorar)" else None
                        qtd = importar_referencia_rubricas(df_ref, col_cod, n_col, t_col)
                        st.success(f"{qtd} rubricas atualizadas!")
                        st.rerun()
            except Exception as e:
                st.error(f"Erro ao ler arquivo: {e}")

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("📝 Editar Funcionários")
            df_f = carregar_funcionarios_db()
            df_f_ed = st.data_editor(df_f, num_rows="dynamic", key="ed_f")
            if st.button("Salvar Funcionários"):
                salvar_alteracoes_funcionarios(df_f_ed); st.success("Salvo!"); st.rerun()
        with c2:
            st.subheader("🏷️ Editar Rubricas")
            df_r = carregar_rubricas_db()
            # Converte dicionário para DF para edição
            df_r_view = pd.DataFrame.from_dict(df_r, orient='index').reset_index().rename(columns={'index': 'codigo'})
            if df_r_view.empty: df_r_view = pd.DataFrame(columns=['codigo', 'tipo', 'nome_personalizado'])
            
            df_r_ed = st.data_editor(df_r_view, key="ed_r")
            if st.button("Salvar Rubricas"):
                salvar_alteracoes_rubricas(df_r_ed); st.success("Salvo!"); st.rerun()
else:
    st.info("👈 Comece enviando os XMLs na barra lateral.")
