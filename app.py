import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import re
import sqlite3
import shutil

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Master", layout="wide", page_icon="🏢")

st.title("🏢 Gestor de Folha eSocial (Persistente)")
st.markdown("""
Sistema de auditoria com nomes personalizados e sistema de Backup para não perder dados no Streamlit Cloud.
""")

# --- GERENCIAMENTO DO BANCO DE DADOS ---

def init_db():
    conn = sqlite3.connect('esocial_db.db')
    c = conn.cursor()
    # Adicionado campo 'nome_personalizado'
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
    
    # Migração segura para quem já tem o DB antigo sem a coluna nova
    try:
        c.execute("ALTER TABLE rubricas ADD COLUMN nome_personalizado TEXT")
    except sqlite3.OperationalError:
        pass # Coluna já existe

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
        # Retorna um dicionário completo com tipo e nome
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
    # Atualiza ou insere (Upsert simplificado deletando antes)
    c.execute("DELETE FROM rubricas")
    for index, row in df_edited.iterrows():
        # Garante que nome_personalizado não seja NaN
        nome_pers = row['nome_personalizado'] if pd.notna(row['nome_personalizado']) else ""
        c.execute("INSERT INTO rubricas (codigo, tipo, nome_personalizado) VALUES (?, ?, ?)", 
                  (row['codigo'], row['tipo'], nome_pers))
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

# Inicializa DB
init_db()

# --- BACKUP E RESTAURAÇÃO (SIDEBAR) ---
st.sidebar.header("💾 Backup do Banco de Dados")
st.sidebar.info("O Streamlit Cloud apaga os dados ao reiniciar. Baixe o backup sempre que terminar o trabalho e suba novamente ao iniciar.")

# Upload do Banco (Restaurar)
uploaded_db = st.sidebar.file_uploader("Restaurar Backup (.db)", type=["db"])
if uploaded_db:
    if st.sidebar.button("♻️ Restaurar Dados"):
        with open("esocial_db.db", "wb") as f:
            f.write(uploaded_db.getbuffer())
        st.success("Banco de dados restaurado! A página será recarregada.")
        st.rerun()

# Download do Banco (Salvar)
if st.sidebar.button("Preparar Download"):
    with open("esocial_db.db", "rb") as f:
        db_bytes = f.read()
    st.sidebar.download_button(
        label="⬇️ Baixar Backup Agora",
        data=db_bytes,
        file_name="esocial_backup.db",
        mime="application/x-sqlite3"
    )

st.sidebar.divider()

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
                    
                    # Lógica de Classificação e Nomeação
                    nome_final = ""
                    if cod_rubr in rubricas_conhecidas: 
                        tipo_final = rubricas_conhecidas[cod_rubr]['tipo']
                        nome_final = rubricas_conhecidas[cod_rubr]['nome_personalizado']
                    else:
                        tipo_final = estimar_tipo_rubrica_inicial(cod_rubr)
                        # Salva na lista de novas para inserir no DB depois
                        novas_rubricas[cod_rubr] = {'tipo': tipo_final, 'nome_personalizado': ''}
                        # Atualiza memória local
                        rubricas_conhecidas[cod_rubr] = {'tipo': tipo_final, 'nome_personalizado': ''}
                    
                    # Se não tiver nome personalizado, usa o código como fallback visual
                    display_name = nome_final if nome_final else cod_rubr

                    data_rows.append({
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "Rubrica": cod_rubr,
                        "Descrição": display_name, # Nova coluna para visualização
                        "Tipo": tipo_final,
                        "Valor": valor
                    })
    except Exception: return [], {}, set()
    return data_rows, novas_rubricas, cpfs_encontrados

# --- INTERFACE E FLUXO ---

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
            
            # Passa uma cópia das rubricas para não corromper a leitura durante o loop
            for fname, fcontent in files_to_process:
                rows, novas_r, cpfs = process_xml_file(fcontent, fname, rubricas_db.copy())
                all_data.extend(rows)
                novas_rubricas_geral.update(novas_r)
                cpfs_geral.update(cpfs)

            # 1. Salvar Novas Rubricas no DB
            if novas_rubricas_geral:
                conn = get_db_connection()
                c = conn.cursor()
                for cod, dados in novas_rubricas_geral.items():
                    c.execute("INSERT OR IGNORE INTO rubricas (codigo, tipo, nome_personalizado) VALUES (?, ?, ?)", 
                              (cod, dados['tipo'], dados['nome_personalizado']))
                conn.commit()
                conn.close()

            # 2. Salvar Novos Funcionários no DB
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
    
    # Merge Funcionários
    if not funcionarios_atualizado.empty:
        db_temp = funcionarios_atualizado.rename(columns={'cpf': 'CPF'})
        df = df.merge(db_temp, on="CPF", how="left")
        df["nome"] = df["nome"].fillna(df["CPF"]) 
        df["departamento"] = df["departamento"].fillna("Geral")
    else:
        df["nome"] = df["CPF"]
        df["departamento"] = "Geral"

    # Merge para pegar nomes ATUALIZADOS das rubricas (caso o usuário tenha acabado de editar)
    rubricas_atualizadas = carregar_rubricas_db()
    
    # Função auxiliar para atualizar a descrição na visualização
    def atualizar_descricao(row):
        cod = row['Rubrica']
        if cod in rubricas_atualizadas:
            nome_personalizado = rubricas_atualizadas[cod]['nome_personalizado']
            if nome_personalizado:
                return nome_personalizado
        return cod

    df['Descrição'] = df.apply(atualizar_descricao, axis=1)

    # Filtros de Tempo
    df['Ano'] = df['Competencia'].str.slice(0, 4)
    df['Mes'] = df['Competencia'].str.slice(5, 7)

    st.sidebar.divider()
    st.sidebar.header("📅 Filtros")
    anos_disponiveis = sorted(df['Ano'].unique())
    meses_disponiveis = sorted(df['Mes'].unique())
    anos_sel = st.sidebar.multiselect("Anos", anos_disponiveis, default=anos_disponiveis)
    meses_sel = st.sidebar.multiselect("Meses", meses_disponiveis, default=meses_disponiveis)
    
    df_filtrado = df[df['Ano'].isin(anos_sel) & df['Mes'].isin(meses_sel)]
    
    if df_filtrado.empty:
        st.warning("Nenhum dado encontrado para o período.")
    else:
        tab1, tab2, tab3 = st.tabs(["📊 Visão Gerencial", "👤 Contracheques", "⚙️ Configurações"])

        with tab1:
            st.subheader(f"Resumo Financeiro")
            deptos = ["Todos"] + list(df_filtrado["departamento"].unique())
            filtro_depto = st.selectbox("Filtrar Departamento:", deptos)
            df_view = df_filtrado if filtro_depto == "Todos" else df_filtrado[df_filtrado["departamento"] == filtro_depto]
            
            visao_agrupamento = st.radio("Agrupar:", ["Mês a Mês", "Acumulado"], horizontal=True)
            index_pivot = ["departamento", "Competencia"] if visao_agrupamento == "Mês a Mês" else ["departamento"]

            resumo = df_view[df_view["Tipo"].isin(["Provento", "Desconto"])].pivot_table(
                index=index_pivot, columns="Tipo", values="Valor", aggfunc="sum", fill_value=0
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
            c_sel1, c_sel2 = st.columns(2)
            with c_sel1:
                opcoes_func = df_filtrado[["CPF", "nome"]].drop_duplicates()
                opcoes_func["label"] = opcoes_func["nome"].astype(str) + " (" + opcoes_func["CPF"].astype(str) + ")"
                if not opcoes_func.empty:
                    func_selecionado = st.selectbox("Funcionário:", opcoes_func["label"])
                    cpf_selecionado = opcoes_func[opcoes_func["label"] == func_selecionado]["CPF"].values[0]
                else: cpf_selecionado = None
            with c_sel2:
                if cpf_selecionado:
                    comps = sorted(df_filtrado[df_filtrado["CPF"] == cpf_selecionado]["Competencia"].unique())
                    default_comp = [comps[-1]] if comps else []
                    comps_selecionadas = st.multiselect("Competências:", comps, default=default_comp)
                else: comps_selecionadas = []
            
            if cpf_selecionado and comps_selecionadas:
                mask = (df_filtrado["CPF"] == cpf_selecionado) & (df_filtrado["Competencia"].isin(comps_selecionadas))
                df_holerite = df_filtrado[mask].copy()
                
                # Agrupa usando a 'Descrição' (Nome do Evento) em vez do código cru
                df_holerite_agrupado = df_holerite.groupby(["Rubrica", "Descrição", "Tipo"])["Valor"].sum().reset_index()
                
                tot_prov = df_holerite_agrupado[df_holerite_agrupado["Tipo"] == "Provento"]["Valor"].sum()
                tot_desc = df_holerite_agrupado[df_holerite_agrupado["Tipo"] == "Desconto"]["Valor"].sum()
                
                st.divider()
                st.markdown(f"### 📄 {func_selecionado}")
                col_h1, col_h2, col_h3 = st.columns(3)
                col_h1.metric("Proventos", f"R$ {tot_prov:,.2f}")
                col_h2.metric("Descontos", f"R$ {tot_desc:,.2f}")
                col_h3.metric("Líquido", f"R$ {tot_prov - tot_desc:,.2f}")
                st.table(df_holerite_agrupado[["Rubrica", "Descrição", "Tipo", "Valor"]].style.format({"Valor": "{:.2f}"}))

        with tab3:
            st.header("⚙️ Banco de Dados")
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📝 Funcionários")
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
                st.subheader("🏷️ Rubricas (Eventos)")
                try: 
                    conn = get_db_connection()
                    df_rubs = pd.read_sql("SELECT * FROM rubricas", conn)
                    conn.close()
                except: df_rubs = pd.DataFrame(columns=["codigo", "tipo", "nome_personalizado"])
                
                df_rubs_editado = st.data_editor(
                    df_rubs,
                    column_config={
                        "codigo": st.column_config.TextColumn("Cód.", disabled=True),
                        "tipo": st.column_config.SelectboxColumn("Tipo", options=["Provento", "Desconto", "Informativo"], required=True),
                        # NOVA COLUNA EDITÁVEL
                        "nome_personalizado": st.column_config.TextColumn("Nome do Evento (Holerite)")
                    },
                    key="editor_rubs"
                )
                if st.button("Salvar Rubricas"):
                    salvar_alteracoes_rubricas(df_rubs_editado)
                    st.success("Rubricas atualizadas!")
                    st.rerun()
else:
    st.info("👈 Faça o upload dos XMLs na barra lateral.")
