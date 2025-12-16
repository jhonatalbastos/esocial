import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import re
import sqlite3

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Pro", layout="wide", page_icon="🏢")

st.title("🏢 Gestor de Folha eSocial (Anual & Mensal)")
st.markdown("""
Sistema inteligente com filtros temporais para análise de competências acumuladas.
""")

# --- GERENCIAMENTO DO BANCO DE DADOS (SQLite) ---

def init_db():
    conn = sqlite3.connect('esocial_db.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (codigo TEXT PRIMARY KEY, tipo TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (cpf TEXT PRIMARY KEY, nome TEXT, departamento TEXT)''')
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect('esocial_db.db')

def carregar_rubricas_db():
    conn = get_db_connection()
    try: df = pd.read_sql("SELECT * FROM rubricas", conn)
    except: df = pd.DataFrame(columns=["codigo", "tipo"])
    conn.close()
    if not df.empty: return df.set_index("codigo")["tipo"].to_dict()
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
        c.execute("INSERT INTO rubricas (codigo, tipo) VALUES (?, ?)", (row['codigo'], row['tipo']))
    conn.commit()
    conn.close()

def salvar_alteracoes_funcionarios(df_edited):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM funcionarios")
    for index, row in df_edited.iterrows():
        c.execute("INSERT INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", 
                  (row['cpf'], row['nome'], row['departamento']))
    conn.commit()
    conn.close()

init_db()

# --- LÓGICA DE PROCESSAMENTO ---

def clean_xml_content(xml_content):
    try:
        if isinstance(xml_content, bytes): xml_str = xml_content.decode('utf-8', errors='ignore')
        else: xml_str = xml_content
        xml_str = re.sub(r'\sxmlns(:[a-zA-Z0-9]+)?="[^"]+"', '', xml_str)
        xml_str = re.sub(r'<([a-zA-Z0-9]+):', '<', xml_str)
        xml_str = re.sub(r'</([a-zA-Z0-9]+):', '</', xml_str)
        return xml_str
    except Exception: return xml_content

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
            per_apur = ide_evento.find("perApur").text if ide_evento is not None else "N/A"
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
                    
                    if cod_rubr in rubricas_conhecidas: tipo_final = rubricas_conhecidas[cod_rubr]
                    else:
                        tipo_final = estimar_tipo_rubrica_inicial(cod_rubr)
                        novas_rubricas[cod_rubr] = tipo_final 
                        rubricas_conhecidas[cod_rubr] = tipo_final 
                    
                    data_rows.append({
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "Rubrica": cod_rubr,
                        "Tipo": tipo_final,
                        "Valor": valor
                    })
    except Exception: return [], {}, set()
    return data_rows, novas_rubricas, cpfs_encontrados

# --- INTERFACE E FLUXO ---

rubricas_db = carregar_rubricas_db()
funcionarios_db = carregar_funcionarios_db()

# Barra Lateral de Upload
st.sidebar.header("📂 Arquivos")
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
            
            for fname, fcontent in files_to_process:
                rows, novas_r, cpfs = process_xml_file(fcontent, fname, rubricas_db.copy())
                all_data.extend(rows)
                novas_rubricas_geral.update(novas_r)
                cpfs_geral.update(cpfs)

            # Atualização DB
            if novas_rubricas_geral:
                conn = get_db_connection()
                c = conn.cursor()
                for cod, tipo in novas_rubricas_geral.items():
                    c.execute("INSERT OR IGNORE INTO rubricas (codigo, tipo) VALUES (?, ?)", (cod, tipo))
                conn.commit()
                conn.close()

            conn = get_db_connection()
            c = conn.cursor()
            novos_funcs = 0
            for cpf in cpfs_geral:
                c.execute("SELECT cpf FROM funcionarios WHERE cpf = ?", (cpf,))
                if not c.fetchone():
                    c.execute("INSERT INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", (cpf, "", "Geral"))
                    novos_funcs += 1
            conn.commit()
            conn.close()
            
            if novos_funcs > 0: st.toast(f"{novos_funcs} novos funcionários.", icon="👥")

            st.session_state['df_bruto'] = pd.DataFrame(all_data)
            st.rerun()

# --- EXIBIÇÃO ---

if 'df_bruto' in st.session_state:
    funcionarios_atualizado = carregar_funcionarios_db()
    df = st.session_state['df_bruto'].copy()
    
    # Merge seguro
    if not funcionarios_atualizado.empty:
        db_temp = funcionarios_atualizado.rename(columns={'cpf': 'CPF'})
        df = df.merge(db_temp, on="CPF", how="left")
        df["nome"] = df["nome"].fillna(df["CPF"]) 
        df["departamento"] = df["departamento"].fillna("Geral")
    else:
        df["nome"] = df["CPF"]
        df["departamento"] = "Geral"

    # --- NOVIDADE: PREPARAÇÃO DE DATAS PARA FILTROS ---
    # Extrai Ano e Mês da string "YYYY-MM"
    df['Ano'] = df['Competencia'].str.slice(0, 4)
    df['Mes'] = df['Competencia'].str.slice(5, 7)

    # --- FILTROS LATERAIS (SIDEBAR) ---
    st.sidebar.divider()
    st.sidebar.header("📅 Filtros de Período")
    
    anos_disponiveis = sorted(df['Ano'].unique())
    meses_disponiveis = sorted(df['Mes'].unique())
    
    # Por padrão, seleciona todos
    anos_sel = st.sidebar.multiselect("Selecione os Anos", anos_disponiveis, default=anos_disponiveis)
    meses_sel = st.sidebar.multiselect("Selecione os Meses", meses_disponiveis, default=meses_disponiveis)
    
    # Aplica filtro ao DataFrame Principal
    df_filtrado = df[df['Ano'].isin(anos_sel) & df['Mes'].isin(meses_sel)]
    
    if df_filtrado.empty:
        st.warning("Nenhum dado encontrado para o período selecionado.")
    else:
        # Tabs
        tab1, tab2, tab3 = st.tabs(["📊 Visão Gerencial (Acumulado)", "👤 Contracheques", "⚙️ Configurações"])

        with tab1:
            st.subheader(f"Resumo Financeiro ({', '.join(anos_sel)})")
            
            deptos = ["Todos"] + list(df_filtrado["departamento"].unique())
            filtro_depto = st.selectbox("Filtrar por Departamento:", deptos)
            
            df_view = df_filtrado if filtro_depto == "Todos" else df_filtrado[df_filtrado["departamento"] == filtro_depto]
            
            # Opção de Visão: Por Competência ou Total Acumulado
            visao_agrupamento = st.radio("Agrupar valores por:", ["Mês a Mês (Detalhado)", "Total do Período (Acumulado)"], horizontal=True)

            index_pivot = ["departamento", "Competencia"] if visao_agrupamento == "Mês a Mês (Detalhado)" else ["departamento"]

            resumo = df_view[df_view["Tipo"].isin(["Provento", "Desconto"])].pivot_table(
                index=index_pivot, 
                columns="Tipo", 
                values="Valor", 
                aggfunc="sum", 
                fill_value=0
            ).reset_index()
            
            if "Desconto" not in resumo.columns: resumo["Desconto"] = 0
            if "Provento" not in resumo.columns: resumo["Provento"] = 0
            resumo["Liquido"] = resumo["Provento"] - resumo["Desconto"]
            
            # Totais Gerais no topo
            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("Total Proventos Filtrados", f"R$ {resumo['Provento'].sum():,.2f}")
            col_t2.metric("Total Descontos Filtrados", f"R$ {resumo['Desconto'].sum():,.2f}")
            col_t3.metric("Líquido Total Filtrados", f"R$ {resumo['Liquido'].sum():,.2f}")
            
            st.dataframe(resumo.style.format({"Provento": "R$ {:,.2f}", "Desconto": "R$ {:,.2f}", "Liquido": "R$ {:,.2f}"}), use_container_width=True)

        with tab2:
            st.subheader("Consulta Individual")
            st.info("Os dados abaixo respeitam os filtros de Ano/Mês selecionados na barra lateral.")

            col_sel1, col_sel2 = st.columns(2)
            with col_sel1:
                opcoes_func = df_filtrado[["CPF", "nome"]].drop_duplicates()
                opcoes_func["label"] = opcoes_func["nome"].astype(str) + " (" + opcoes_func["CPF"].astype(str) + ")"
                
                if not opcoes_func.empty:
                    func_selecionado = st.selectbox("Selecione o Funcionário:", opcoes_func["label"])
                    cpf_selecionado = opcoes_func[opcoes_func["label"] == func_selecionado]["CPF"].values[0]
                else:
                    cpf_selecionado = None

            with col_sel2:
                if cpf_selecionado:
                    # Permite selecionar Múltiplas competências para ver um resumo do funcionário
                    comps = sorted(df_filtrado[df_filtrado["CPF"] == cpf_selecionado]["Competencia"].unique())
                    # Por padrão, seleciona a última disponível
                    default_comp = [comps[-1]] if comps else []
                    comps_selecionadas = st.multiselect("Selecionar Competências (Pode somar várias):", comps, default=default_comp)
                else:
                    comps_selecionadas = []
            
            if cpf_selecionado and comps_selecionadas:
                mask = (df_filtrado["CPF"] == cpf_selecionado) & (df_filtrado["Competencia"].isin(comps_selecionadas))
                df_holerite = df_filtrado[mask].copy()
                
                # Agrupa por Rubrica (caso tenha selecionado varios meses, soma a rubrica)
                df_holerite_agrupado = df_holerite.groupby(["Rubrica", "Tipo"])["Valor"].sum().reset_index()
                
                tot_prov = df_holerite_agrupado[df_holerite_agrupado["Tipo"] == "Provento"]["Valor"].sum()
                tot_desc = df_holerite_agrupado[df_holerite_agrupado["Tipo"] == "Desconto"]["Valor"].sum()
                
                st.divider()
                st.markdown(f"### 📄 Resumo: {func_selecionado}")
                st.caption(f"Competências Somadas: {', '.join(comps_selecionadas)}")
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Proventos", f"R$ {tot_prov:,.2f}")
                c2.metric("Descontos", f"R$ {tot_desc:,.2f}")
                c3.metric("Líquido", f"R$ {tot_prov - tot_desc:,.2f}")
                
                st.table(df_holerite_agrupado[["Rubrica", "Tipo", "Valor"]].style.format({"Valor": "{:.2f}"}))
            elif not opcoes_func.empty:
                st.info("Selecione as competências acima para ver os valores.")

        with tab3:
            st.header("⚙️ Banco de Dados")
            
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📝 Cadastro de Funcionários")
                df_funcs = carregar_funcionarios_db()
                df_funcs_editado = st.data_editor(
                    df_funcs, num_rows="dynamic",
                    column_config={"cpf": st.column_config.TextColumn("CPF", disabled=True)},
                    key="editor_funcs"
                )
                if st.button("Salvar Funcionários"):
                    salvar_alteracoes_funcionarios(df_funcs_editado)
                    st.success("Salvo!")
                    st.rerun()

            with c2:
                st.subheader("🏷️ Configuração de Rubricas")
                try: 
                    conn = get_db_connection()
                    df_rubs = pd.read_sql("SELECT * FROM rubricas", conn)
                    conn.close()
                except: df_rubs = pd.DataFrame(columns=["codigo", "tipo"])
                
                df_rubs_editado = st.data_editor(
                    df_rubs,
                    column_config={
                        "codigo": st.column_config.TextColumn("Cód.", disabled=True),
                        "tipo": st.column_config.SelectboxColumn("Tipo", options=["Provento", "Desconto", "Informativo"], required=True)
                    },
                    key="editor_rubs"
                )
                if st.button("Salvar Rubricas"):
                    salvar_alteracoes_rubricas(df_rubs_editado)
                    st.success("Salvo!")
                    st.rerun()
else:
    st.info("👈 Faça o upload dos arquivos XML na barra lateral para começar.")
