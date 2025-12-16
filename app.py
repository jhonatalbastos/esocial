import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import re
import sqlite3

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Inteligente", layout="wide", page_icon="🏢")

st.title("🏢 Gestor de Folha eSocial (Com Banco de Dados)")
st.markdown("""
Sistema inteligente que processa XMLs, aprende a classificação das rubricas e gerencia departamentos.
""")

# --- GERENCIAMENTO DO BANCO DE DADOS (SQLite) ---

def init_db():
    """Cria o banco de dados e as tabelas se não existirem."""
    conn = sqlite3.connect('esocial_db.db')
    c = conn.cursor()
    
    # Tabela de Rubricas (Memória de Classificação)
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (
                    codigo TEXT PRIMARY KEY, 
                    tipo TEXT
                )''')
    
    # Tabela de Funcionários (Cadastro)
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (
                    cpf TEXT PRIMARY KEY, 
                    nome TEXT, 
                    departamento TEXT
                )''')
    
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect('esocial_db.db')

def carregar_rubricas_db():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM rubricas", conn)
    conn.close()
    return df.set_index("codigo")["tipo"].to_dict()

def carregar_funcionarios_db():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM funcionarios", conn)
    conn.close()
    return df

def salvar_alteracoes_rubricas(df_edited):
    """Salva as edições feitas na tela de configuração."""
    conn = get_db_connection()
    # Limpa e recria para garantir atualização total
    c = conn.cursor()
    c.execute("DELETE FROM rubricas")
    for index, row in df_edited.iterrows():
        c.execute("INSERT INTO rubricas (codigo, tipo) VALUES (?, ?)", (row['codigo'], row['tipo']))
    conn.commit()
    conn.close()

def salvar_alteracoes_funcionarios(df_edited):
    """Salva os nomes e departamentos."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM funcionarios")
    for index, row in df_edited.iterrows():
        c.execute("INSERT INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", 
                  (row['cpf'], row['nome'], row['departamento']))
    conn.commit()
    conn.close()

# Inicializa o banco ao abrir o app
init_db()

# --- LÓGICA DE PROCESSAMENTO ---

def clean_xml_content(xml_content):
    try:
        if isinstance(xml_content, bytes):
            xml_str = xml_content.decode('utf-8', errors='ignore')
        else:
            xml_str = xml_content
        xml_str = re.sub(r'\sxmlns(:[a-zA-Z0-9]+)?="[^"]+"', '', xml_str)
        xml_str = re.sub(r'<([a-zA-Z0-9]+):', '<', xml_str)
        xml_str = re.sub(r'</([a-zA-Z0-9]+):', '</', xml_str)
        return xml_str
    except Exception:
        return xml_content

def estimar_tipo_rubrica_inicial(codigo):
    """Adivinha o tipo apenas se não estiver no banco."""
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
    novas_rubricas = {} # Armazena rubricas que ainda não estão no DB para salvar depois
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
                id_demo = dm.find("ideDmDev").text if dm.find("ideDmDev") is not None else ""
                itens = dm.findall(".//itensRemun")
                
                for item in itens:
                    cod_rubr = item.find("codRubr").text if item.find("codRubr") is not None else ""
                    vr_rubr = item.find("vrRubr").text if item.find("vrRubr") is not None else "0.00"
                    try: valor = float(vr_rubr)
                    except: valor = 0.00
                    
                    # Lógica de Classificação: DB > Estimativa
                    if cod_rubr in rubricas_conhecidas:
                        tipo_final = rubricas_conhecidas[cod_rubr]
                    else:
                        tipo_final = estimar_tipo_rubrica_inicial(cod_rubr)
                        novas_rubricas[cod_rubr] = tipo_final # Guarda para cadastrar depois
                        rubricas_conhecidas[cod_rubr] = tipo_final # Atualiza memória local
                    
                    data_rows.append({
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "Rubrica": cod_rubr,
                        "Tipo": tipo_final,
                        "Valor": valor
                    })
    except Exception:
        return [], {}, set()
    return data_rows, novas_rubricas, cpfs_encontrados

# --- INTERFACE E FLUXO ---

# Carrega configurações do Banco
rubricas_db = carregar_rubricas_db()
funcionarios_db = carregar_funcionarios_db()

uploaded_file = st.sidebar.file_uploader("📂 Upload ZIP/XML eSocial", type=["zip", "xml"], accept_multiple_files=True)

if uploaded_file:
    if st.sidebar.button("🚀 Processar Arquivos"):
        with st.spinner('Processando e consultando banco de dados...'):
            all_data = []
            files_to_process = []
            
            # Preparação dos arquivos
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

            # Processamento
            novas_rubricas_geral = {}
            cpfs_geral = set()
            
            for fname, fcontent in files_to_process:
                rows, novas_r, cpfs = process_xml_file(fcontent, fname, rubricas_db.copy())
                all_data.extend(rows)
                novas_rubricas_geral.update(novas_r)
                cpfs_geral.update(cpfs)

            # --- ATUALIZAÇÃO AUTOMÁTICA DO BANCO ---
            
            # 1. Salvar novas rubricas encontradas
            if novas_rubricas_geral:
                conn = get_db_connection()
                c = conn.cursor()
                for cod, tipo in novas_rubricas_geral.items():
                    # Só insere se não existir (IGNORE)
                    c.execute("INSERT OR IGNORE INTO rubricas (codigo, tipo) VALUES (?, ?)", (cod, tipo))
                conn.commit()
                conn.close()
                st.toast(f"{len(novas_rubricas_geral)} novas rubricas cadastradas!", icon="💾")

            # 2. Verificar novos funcionários e adicionar pré-cadastro
            conn = get_db_connection()
            c = conn.cursor()
            novos_funcs = 0
            for cpf in cpfs_geral:
                # Verifica se CPF existe
                c.execute("SELECT cpf FROM funcionarios WHERE cpf = ?", (cpf,))
                if not c.fetchone():
                    # Insere vazio para o usuário preencher depois
                    c.execute("INSERT INTO funcionarios (cpf, nome, departamento) VALUES (?, ?, ?)", (cpf, "", "Geral"))
                    novos_funcs += 1
            conn.commit()
            conn.close()
            
            if novos_funcs > 0:
                st.toast(f"{novos_funcs} novos funcionários detectados.", icon="busts_in_silhouette")

            # Salva dados processados na sessão
            st.session_state['df_bruto'] = pd.DataFrame(all_data)
            st.rerun() # Recarrega a página para pegar os dados novos do banco

# --- EXIBIÇÃO ---

if 'df_bruto' in st.session_state:
    # Recarrega DB para garantir dados frescos (nomes e tipos editados)
    funcionarios_atualizado = carregar_funcionarios_db()
    
    df = st.session_state['df_bruto'].copy()
    
    # Faz o MERGE dos dados do XML com os Nomes/Deptos do Banco
    df = df.merge(funcionarios_atualizado, on="CPF", how="left")
    df["nome"] = df["nome"].fillna(df["CPF"]) # Se não tiver nome, usa CPF
    df["departamento"] = df["departamento"].fillna("Geral")
    
    # Tabs
    tab1, tab2, tab3 = st.tabs(["📊 Visão Gerencial", "👤 Contracheques", "⚙️ Configurações & Cadastro"])

    with tab1:
        st.subheader("Resumo da Folha")
        
        # Filtros
        deptos = ["Todos"] + list(df["departamento"].unique())
        filtro_depto = st.selectbox("Filtrar por Departamento:", deptos)
        
        df_view = df if filtro_depto == "Todos" else df[df["departamento"] == filtro_depto]
        
        # Resumo Financeiro
        resumo = df_view[df_view["Tipo"].isin(["Provento", "Desconto"])].pivot_table(
            index=["departamento", "Competencia"], 
            columns="Tipo", 
            values="Valor", 
            aggfunc="sum", 
            fill_value=0
        ).reset_index()
        
        if "Desconto" not in resumo.columns: resumo["Desconto"] = 0
        if "Provento" not in resumo.columns: resumo["Provento"] = 0
        resumo["Liquido"] = resumo["Provento"] - resumo["Desconto"]
        
        st.dataframe(resumo.style.format({"Provento": "R$ {:,.2f}", "Desconto": "R$ {:,.2f}", "Liquido": "R$ {:,.2f}"}), use_container_width=True)

    with tab2:
        col_sel1, col_sel2 = st.columns(2)
        with col_sel1:
            # Dropdown mostra Nome (CPF)
            opcoes_func = df[["CPF", "nome"]].drop_duplicates()
            opcoes_func["label"] = opcoes_func["nome"] + " (" + opcoes_func["CPF"] + ")"
            
            func_selecionado = st.selectbox("Selecione o Funcionário:", opcoes_func["label"])
            cpf_selecionado = opcoes_func[opcoes_func["label"] == func_selecionado]["CPF"].values[0]

        with col_sel2:
            comps = sorted(df[df["CPF"] == cpf_selecionado]["Competencia"].unique())
            comp_selecionada = st.selectbox("Competência:", comps) if comps else None
        
        if comp_selecionada:
            mask = (df["CPF"] == cpf_selecionado) & (df["Competencia"] == comp_selecionada)
            df_holerite = df[mask].copy()
            
            # Cards
            tot_prov = df_holerite[df_holerite["Tipo"] == "Provento"]["Valor"].sum()
            tot_desc = df_holerite[df_holerite["Tipo"] == "Desconto"]["Valor"].sum()
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Proventos", f"R$ {tot_prov:,.2f}")
            c2.metric("Descontos", f"R$ {tot_desc:,.2f}")
            c3.metric("Líquido", f"R$ {tot_prov - tot_desc:,.2f}")
            
            st.table(df_holerite[["Rubrica", "Tipo", "Valor"]].style.format({"Valor": "{:.2f}"}))

    with tab3:
        st.header("⚙️ Banco de Dados")
        st.info("As alterações feitas aqui ficam salvas para sempre.")
        
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("📝 Cadastro de Funcionários")
            df_funcs = carregar_funcionarios_db()
            
            # Editor de Dados
            df_funcs_editado = st.data_editor(
                df_funcs, 
                num_rows="dynamic",
                column_config={
                    "cpf": st.column_config.TextColumn("CPF", disabled=True),
                    "nome": "Nome Completo",
                    "departamento": "Departamento"
                },
                key="editor_funcs"
            )
            
            if st.button("Salvar Funcionários"):
                salvar_alteracoes_funcionarios(df_funcs_editado)
                st.success("Cadastro atualizado!")
                st.rerun()

        with c2:
            st.subheader("🏷️ Configuração de Rubricas")
            conn = get_db_connection()
            df_rubs = pd.read_sql("SELECT * FROM rubricas", conn)
            conn.close()
            
            df_rubs_editado = st.data_editor(
                df_rubs,
                column_config={
                    "codigo": st.column_config.TextColumn("Cód. Rubrica", disabled=True),
                    "tipo": st.column_config.SelectboxColumn(
                        "Tipo (Classificação)",
                        options=["Provento", "Desconto", "Informativo"],
                        required=True
                    )
                },
                key="editor_rubs"
            )
            
            if st.button("Salvar Rubricas"):
                salvar_alteracoes_rubricas(df_rubs_editado)
                st.success("Rubricas atualizadas!")
                st.rerun()

else:
    st.info("👈 Faça o upload dos arquivos XML na barra lateral para começar.")
