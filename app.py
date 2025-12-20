import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import sqlite3
import os

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Contábil", layout="wide", page_icon="🏦")

st.title("🏦 Gestor eSocial: Auditoria & Integração Contábil")
st.markdown("Auditoria baseada em XMLs individuais com classificação oficial do eSocial.")

# --- BANCO DE DADOS ---
DB_FILE = 'esocial_pro.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (
                    codigo TEXT PRIMARY KEY, tipo_esocial TEXT, nome_personalizado TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (
                    cpf TEXT PRIMARY KEY, nome TEXT, departamento TEXT, centro_custo_cod TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS matriz_contabil (
                    cc_cod TEXT, rubrica_cod TEXT, conta_debito TEXT, conta_credito TEXT, historico TEXT,
                    PRIMARY KEY (cc_cod, rubrica_cod))''')
    conn.commit(); conn.close()

def get_db_connection(): return sqlite3.connect(DB_FILE)
def carregar_dados_db(tabela):
    conn = get_db_connection()
    df = pd.read_sql(f"SELECT * FROM {tabela}", conn)
    conn.close()
    return df

init_db()

# --- LÓGICA DE EXTRAÇÃO XML ---
def safe_find(element, tag):
    for node in element.iter():
        if node.tag.endswith(tag): return node.text
    return None

def processar_xml_individual(content):
    data = []
    try:
        root = ET.fromstring(content)
        per_apur = safe_find(root, 'perApur')
        cpf = safe_find(root, 'cpfTrab')
        nome_trab = safe_find(root, 'nmTrab')
        
        for dm in root.iter():
            if dm.tag.endswith('dmDev'):
                id_demo = safe_find(dm, 'ideDmDev') or "Mensal"
                for item in dm.iter():
                    if item.tag.endswith('itensRemun'):
                        cod = safe_find(item, 'codRubr')
                        valor = float(safe_find(item, 'vrRubr') or 0)
                        ref = safe_find(item, 'qtdRubr') or safe_find(item, 'fatorRubr') or ""
                        tp_rubr = safe_find(item, 'tpRubr')
                        
                        classificacao = "Provento" if tp_rubr == '1' else "Desconto" if tp_rubr == '2' else "Informativo"

                        data.append({
                            "Competencia": per_apur, "CPF": cpf, "Nome_XML": nome_trab,
                            "ID_Demo": id_demo, "Rubrica": cod, "Referencia": ref,
                            "Valor": valor, "Tipo_Oficial": classificacao
                        })
    except: pass
    return data

# --- SIDEBAR: UPLOAD E BACKUP ---
st.sidebar.header("📂 Arquivos XML (Fevereiro/2023)")
files = st.sidebar.file_uploader("Suba os XMLs individuais ou ZIP", type=["xml", "zip"], accept_multiple_files=True)

if files:
    if st.sidebar.button("🚀 Processar e Auditar"):
        all_rows = []
        for f in files:
            if f.name.endswith('.zip'):
                with zipfile.ZipFile(f) as z:
                    for name in z.namelist():
                        if name.endswith('.xml'): all_rows.extend(processar_xml_individual(z.read(name)))
            else:
                all_rows.extend(processar_xml_individual(f.read()))
        
        if all_rows:
            st.session_state['df_raw'] = pd.DataFrame(all_rows)
            st.rerun()

# --- EXIBIÇÃO ---
if 'df_raw' in st.session_state:
    df_raw = st.session_state['df_raw']
    df_f = carregar_dados_db("funcionarios")
    df_r = carregar_dados_db("rubricas")
    df_m = carregar_dados_db("matriz_contabil")

    # Merge para enriquecimento de dados
    df_final = df_raw.merge(df_f, left_on='CPF', right_on='cpf', how='left')
    df_final = df_final.merge(df_r[['codigo', 'nome_personalizado']], left_on='Rubrica', right_on='codigo', how='left')
    df_final['Descrição'] = df_final['nome_personalizado'].fillna(df_final['Rubrica'])
    df_final['Nome_Final'] = df_final['nome'].fillna(df_final['Nome_XML'])

    tab1, tab2, tab3 = st.tabs(["📊 Auditoria de Folha", "🔌 Integração Contábil", "⚙️ Configurações Didáticas"])

    with tab1:
        # --- VERIFICADOR DE AUSÊNCIAS ---
        cpfs_processados = set(df_final['CPF'].unique())
        cpfs_cadastrados = set(df_f['cpf'].unique())
        faltantes = cpfs_cadastrados - cpfs_processados
        
        if faltantes:
            st.error(f"⚠️ Atenção: {len(faltantes)} funcionários do cadastro não possuem XML neste lote.")
            with st.expander("Ver lista de contracheques não encontrados"):
                st.write(df_f[df_f['cpf'].isin(faltantes)][['cpf', 'nome', 'departamento']])
        else:
            st.success("✅ Todos os funcionários cadastrados foram processados.")

        st.subheader("Resumo Analítico")
        resumo = df_final.groupby(['Rubrica', 'Descrição', 'Tipo_Oficial'])['Valor'].sum().reset_index()
        st.dataframe(resumo.pivot_table(index=['Rubrica', 'Descrição'], columns='Tipo_Oficial', values='Valor', fill_value=0), use_container_width=True)

    with tab2:
        st.subheader("Geração de CSV (Separado por ;)")
        # Lógica de Matriz Contábil
        df_integracao = df_final.merge(df_m, left_on=['centro_custo_cod', 'Rubrica'], right_on=['cc_cod', 'rubrica_cod'], how='left')
        
        if df_integracao['conta_debito'].isna().any():
            st.warning("Existem rubricas sem conta contábil definida na matriz.")
        
        csv_ready = df_integracao.dropna(subset=['conta_debito'])
        if not csv_ready.empty:
            csv_data = csv_ready.groupby(['conta_debito', 'conta_credito', 'centro_custo_cod', 'historico'])['Valor'].sum().reset_index()
            st.table(csv_data)
            csv_output = io.StringIO()
            csv_data.to_csv(csv_output, sep=';', index=False, header=False)
            st.download_button("📥 Baixar CSV para Contabilidade", csv_output.getvalue(), "folha_contabil.csv", "text/csv")

    with tab3:
        st.header("Configurações Didáticas")
        col_a, col_b = st.columns(2)
        
        with col_a:
            st.subheader("🏢 Centro de Custos & Funcionários")
            st.caption("Associe cada CPF ao seu respectivo código de Centro de Custo.")
            ed_f = st.data_editor(df_f, num_rows="dynamic", key="edf")
            if st.button("Salvar Cadastro"):
                conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM funcionarios")
                for _, r in ed_f.iterrows():
                    c.execute("INSERT INTO funcionarios VALUES (?,?,?,?)", (str(r['cpf']), str(r['nome']), str(r['departamento']), str(r['centro_custo_cod'])))
                conn.commit(); conn.close(); st.success("Salvo!"); st.rerun()

        with col_b:
            st.subheader("🧾 Matriz de Classificação Contábil")
            st.caption("Defina o destino contábil das rubricas por Centro de Custo.")
            ed_m = st.data_editor(df_m, num_rows="dynamic", key="edm")
            if st.button("Salvar Matriz"):
                conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM matriz_contabil")
                for _, r in ed_m.iterrows():
                    c.execute("INSERT INTO matriz_contabil VALUES (?,?,?,?,?)", (str(r['cc_cod']), str(r['rubrica_cod']), str(r['conta_debito']), str(r['conta_credito']), str(r['historico'])))
                conn.commit(); conn.close(); st.success("Salvo!"); st.rerun()

else:
    st.info("Aguardando upload dos XMLs individuais na barra lateral para iniciar a auditoria.")
