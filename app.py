import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import sqlite3
import os

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Contábil", layout="wide", page_icon="🏦")

st.title("🏦 Gestor eSocial: Auditoria & Integração Contábil (V18)")
st.markdown("Classificação oficial baseada em XMLs individuais S-1200.")

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

# --- LÓGICA DE EXTRAÇÃO XML APERFEIÇOADA ---
def safe_find(element, tag):
    for node in element.iter():
        if node.tag.endswith(tag): return node.text
    return None

def processar_xml_individual(content, filename):
    data = []
    try:
        root = ET.fromstring(content)
        per_apur = safe_find(root, 'perApur')
        cpf = safe_find(root, 'cpfTrab')
        nome_trab = safe_find(root, 'nmTrab') or filename.replace('.xml', '')
        
        for dm in root.iter():
            if dm.tag.endswith('dmDev'):
                id_demo = safe_find(dm, 'ideDmDev') or "Mensal"
                for item in dm.iter():
                    if item.tag.endswith('itensRemun'):
                        cod = safe_find(item, 'codRubr')
                        valor = float(safe_find(item, 'vrRubr') or 0)
                        ref = safe_find(item, 'qtdRubr') or safe_find(item, 'fatorRubr') or ""
                        tp_rubr = safe_find(item, 'tpRubr') # TAG CHAVE: 1, 2 ou 3
                        
                        # Tradução oficial eSocial
                        if tp_rubr == '1': classificacao = "Vencimento"
                        elif tp_rubr == '2': classificacao = "Desconto"
                        else: classificacao = "Informativo"

                        data.append({
                            "Competencia": per_apur, "CPF": cpf, "Nome_Funcionario": nome_trab,
                            "Tipo_Folha": id_demo, "Rubrica": cod, "Referencia": ref,
                            "Valor": valor, "Classificação": classificacao
                        })
    except: pass
    return data

# --- BARRA LATERAL ---
st.sidebar.header("📂 Upload de Arquivos")
files = st.sidebar.file_uploader("Suba XMLs individuais ou ZIP", type=["xml", "zip"], accept_multiple_files=True)

if files:
    if st.sidebar.button("🚀 Processar e Classificar"):
        all_rows = []
        for f in files:
            if f.name.endswith('.zip'):
                with zipfile.ZipFile(f) as z:
                    for name in z.namelist():
                        if name.endswith('.xml'): all_rows.extend(processar_xml_individual(z.read(name), name))
            else:
                all_rows.extend(processar_xml_individual(f.read(), f.name))
        
        if all_rows:
            st.session_state['df_raw'] = pd.DataFrame(all_rows)
            st.rerun()

# --- EXIBIÇÃO ---
if 'df_raw' in st.session_state:
    df_raw = st.session_state['df_raw']
    df_f = carregar_dados_db("funcionarios")
    df_r = carregar_dados_db("rubricas")
    df_m = carregar_dados_db("matriz_contabil")

    # Merge para enriquecimento
    df_final = df_raw.merge(df_f, left_on='CPF', right_on='cpf', how='left')
    df_final = df_final.merge(df_r[['codigo', 'nome_personalizado']], left_on='Rubrica', right_on='codigo', how='left')
    df_final['Descrição'] = df_final['nome_personalizado'].fillna(df_final['Rubrica'])

    tab1, tab2, tab3 = st.tabs(["📊 Auditoria de Folha", "🔌 Integração Contábil", "⚙️ Configurações Contábeis"])

    with tab1:
        st.subheader("Visualização por Classificação")
        
        # Verificador de Contracheques Ausentes
        cpfs_no_xml = set(df_final['CPF'].dropna().unique())
        cpfs_no_db = set(df_f['cpf'].dropna().unique())
        faltantes = cpfs_no_db - cpfs_no_xml
        
        if faltantes:
            st.error(f"⚠️ Atenção: {len(faltantes)} funcionários cadastrados não possuem XML neste lote.")
            with st.expander("Ver quem está faltando"):
                st.write(df_f[df_f['cpf'].isin(faltantes)][['cpf', 'nome']])
        
        # Resumo Analítico (Vencimentos / Despesas / Informativos)
        resumo = df_final.pivot_table(index=['Rubrica', 'Descrição'], columns='Classificação', values='Valor', aggfunc='sum', fill_value=0).reset_index()
        st.dataframe(resumo.style.format({c: "{:.2f}" for c in resumo.columns if c not in ['Rubrica', 'Descrição']}), use_container_width=True)

    with tab2:
        st.subheader("Geração do Arquivo para Contabilidade")
        df_integracao = df_final.merge(df_m, left_on=['centro_custo_cod', 'Rubrica'], right_on=['cc_cod', 'rubrica_cod'], how='left')
        
        csv_ready = df_integracao.dropna(subset=['conta_debito', 'conta_credito'])
        if not csv_ready.empty:
            csv_data = csv_ready.groupby(['conta_debito', 'conta_credito', 'centro_custo_cod', 'historico'])['Valor'].sum().reset_index()
            # Ordenação de 5 dígitos como solicitado
            st.table(csv_data)
            csv_str = csv_data.to_csv(sep=';', index=False, header=False)
            st.download_button("📥 Baixar CSV Contábil", csv_str, "folha_contabil.csv", "text/csv")
        else:
            st.warning("Configure as contas contábeis na aba Configurações para gerar o arquivo.")

    with tab3:
        st.header("Configurações e De-Para")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🏢 Centro de Custos")
            ed_f = st.data_editor(df_f, num_rows="dynamic", key="ed_f_v18")
            if st.button("Salvar Funcionários/CC"):
                conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM funcionarios")
                for _, r in ed_f.iterrows():
                    c.execute("INSERT INTO funcionarios VALUES (?,?,?,?)", (str(r['cpf']), str(r['nome']), str(r['departamento']), str(r['centro_custo_cod'])))
                conn.commit(); conn.close(); st.rerun()

        with c2:
            st.subheader("🧾 Matriz Contábil por CC")
            ed_m = st.data_editor(df_m, num_rows="dynamic", key="ed_m_v18")
            if st.button("Salvar Matriz Contábil"):
                conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM matriz_contabil")
                for _, r in ed_m.iterrows():
                    c.execute("INSERT INTO matriz_contabil VALUES (?,?,?,?,?)", (str(r['cc_cod']), str(r['rubrica_cod']), str(r['conta_debito']), str(r['conta_credito']), str(r['historico'])))
                conn.commit(); conn.close(); st.rerun()

else:
    st.info("Aguardando upload dos XMLs individuais para iniciar.")
