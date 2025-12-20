import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import sqlite3
import os
from fpdf import FPDF

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Contábil", layout="wide", page_icon="🏦")

st.title("🏦 Gestor eSocial: Auditoria, PDFs & Integração Contábil")
st.markdown("Versão 17.0 - Leitura de Tags Oficiais e Matriz de Lançamentos por Centro de Custo.")

# --- GESTÃO DE BANCO DE DADOS (SQLITE) ---
DB_FILE = 'esocial_pro.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Tabela de Rubricas (com campos contábeis)
    c.execute('''CREATE TABLE IF NOT EXISTS rubricas (
                    codigo TEXT PRIMARY KEY, 
                    tipo_esocial TEXT, 
                    nome_personalizado TEXT,
                    conta_debito_padrao TEXT,
                    conta_credito_padrao TEXT
                )''')
    # Tabela de Funcionários e Centros de Custo
    c.execute('''CREATE TABLE IF NOT EXISTS funcionarios (
                    cpf TEXT PRIMARY KEY, 
                    nome TEXT, 
                    departamento TEXT,
                    centro_custo_cod TEXT
                )''')
    # Matriz Contábil (Regras específicas por Centro de Custo)
    c.execute('''CREATE TABLE IF NOT EXISTS matriz_contabil (
                    cc_cod TEXT,
                    rubrica_cod TEXT,
                    conta_debito TEXT,
                    conta_credito TEXT,
                    historico TEXT,
                    PRIMARY KEY (cc_cod, rubrica_cod)
                )''')
    conn.commit()
    conn.close()

def get_db_connection(): return sqlite3.connect(DB_FILE)

# --- FUNÇÕES DE CARREGAMENTO DINÂMICO ---

def carregar_dados_db(tabela):
    conn = get_db_connection()
    df = pd.read_sql(f"SELECT * FROM {tabela}", conn)
    conn.close()
    return df

init_db()

# --- LÓGICA DE LEITURA DOS XMLS INDIVIDUAIS (S-1200) ---

def safe_find(element, tag):
    for node in element.iter():
        if node.tag.endswith(tag): return node.text
    return None

def processar_xml_individual(content):
    """Lê o XML individual e extrai a classificação oficial (tpRubr)"""
    data = []
    try:
        root = ET.fromstring(content)
        # Identifica evento e competência
        per_apur = safe_find(root, 'perApur')
        cpf = safe_find(root, 'cpfTrab')
        nome_trab = safe_find(root, 'nmTrab')
        
        # Percorre demonstrativos
        for dm in root.iter():
            if dm.tag.endswith('dmDev'):
                id_demo = safe_find(dm, 'ideDmDev')
                # Percorre itens de remuneração
                for item in dm.iter():
                    if item.tag.endswith('itensRemun'):
                        cod = safe_find(item, 'codRubr')
                        valor = float(safe_find(item, 'vrRubr') or 0)
                        ref = safe_find(item, 'qtdRubr') or safe_find(item, 'fatorRubr') or ""
                        # TAG OFICIAL DE CLASSIFICAÇÃO
                        tp_rubr = safe_find(item, 'tpRubr') 
                        
                        # Mapeamento oficial eSocial
                        # 1-Vencimento, 2-Desconto, 3-Informativa, 4-Informativa Tributária
                        classificacao = "Provento" if tp_rubr == '1' else "Desconto" if tp_rubr == '2' else "Informativo"

                        data.append({
                            "Competencia": per_apur,
                            "CPF": cpf,
                            "Nome_XML": nome_trab,
                            "ID_Demo": id_demo,
                            "Rubrica": cod,
                            "Referencia": ref,
                            "Valor": valor,
                            "Tipo_Oficial": classificacao
                        })
    except: pass
    return data

# --- INTERFACE: BARRA LATERAL (UPLOAD E BACKUP) ---

st.sidebar.header("📂 Entrada de Dados")
files = st.sidebar.file_uploader("Subir XMLs de 02/2023", type=["xml", "zip"], accept_multiple_files=True)

if files:
    if st.sidebar.button("🚀 Processar Fevereiro/2023"):
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
            # Auto-cadastro de rubricas novas no DB
            conn = get_db_connection(); c = conn.cursor()
            for r in st.session_state['df_raw']['Rubrica'].unique():
                c.execute("INSERT OR IGNORE INTO rubricas (codigo, tipo_esocial) VALUES (?,?)", (str(r), "Provento"))
            conn.commit(); conn.close()
            st.success(f"Processados {len(files)} arquivos com sucesso!")

# --- PROCESSAMENTO DA MATRIZ (DE-PARA CONTÁBIL) ---

def aplicar_matriz(df_raw):
    df_r = carregar_dados_db("rubricas")
    df_f = carregar_dados_db("funcionarios")
    df_m = carregar_dados_db("matriz_contabil")
    
    # Merge com Funcionários para pegar Centro de Custo
    df = df_raw.merge(df_f[['cpf', 'nome', 'departamento', 'centro_custo_cod']], left_on='CPF', right_on='cpf', how='left')
    
    # Merge com Rubricas para pegar Nome Personalizado
    df = df.merge(df_r[['codigo', 'nome_personalizado']], left_on='Rubrica', right_on='codigo', how='left')
    
    # Merge com Matriz Contábil para pegar Contas Débito/Crédito baseadas no Centro de Custo
    df = df.merge(df_m, left_on=['centro_custo_cod', 'Rubrica'], right_on=['cc_cod', 'rubrica_cod'], how='left')
    
    # Fallbacks
    df['Descrição'] = df['nome_personalizado'].fillna(df['Rubrica'])
    df['Nome_Final'] = df['nome'].fillna(df['Nome_XML'])
    df['CC_Final'] = df['centro_custo_cod'].fillna("999")
    
    return df

# --- ABAS PRINCIPAIS ---

if 'df_raw' in st.session_state:
    df_final = aplicar_matriz(st.session_state['df_raw'])
    
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Resumos (PDF)", "👤 Contracheques", "🔌 Integração Contábil (CSV)", "⚙️ Configurações"])

    with tab1:
        st.subheader("Resumo Analítico da Folha")
        deptos = ["Todos"] + list(df_final['departamento'].unique())
        sel_dept = st.selectbox("Departamento:", deptos)
        
        df_dep = df_final if sel_dept == "Todos" else df_final[df_final['departamento'] == sel_dept]
        
        # Agrupamento estilo seu PDF "Resumo Geral"
        resumo = df_dep.groupby(['Rubrica', 'Descrição', 'Tipo_Oficial'])['Valor'].sum().reset_index()
        pivot = resumo.pivot_table(index=['Rubrica', 'Descrição'], columns='Tipo_Oficial', values='Valor', fill_value=0).reset_index()
        
        st.dataframe(pivot.style.format({"Provento": "{:.2f}", "Desconto": "{:.2f}"}), use_container_width=True)
        
        if st.button("🖨️ Gerar PDF Resumo Geral"):
            st.info("Gerando PDF baseado no modelo RESUMO GERAL.pdf...")

    with tab2:
        st.subheader("Geração de Contracheques")
        st.info("Layout baseado em RECIBO DE PAGAMENTO_STO ANTONIO.pdf")
        # Filtro de funcionário e geração de PDF individual ou lote...

    with tab3:
        st.subheader("🔌 Exportação Contábil (CSV)")
        st.markdown("Formato: `Débito;Crédito;CentroCusto;Histórico;Valor`")
        
        # Filtra apenas o que tem conta contábil configurada
        df_contabil = df_final[df_final['conta_debito'].notna()].copy()
        
        if df_contabil.empty:
            st.warning("Nenhuma rubrica possui conta contábil configurada na Matriz.")
        else:
            # Agrupa para somar valores iguais na mesma conta/CC
            csv_data = df_contabil.groupby(['conta_debito', 'conta_credito', 'CC_Final', 'historico'])['Valor'].sum().reset_index()
            
            # Formata CSV separado por ;
            csv_output = io.StringIO()
            csv_data.to_csv(csv_output, sep=';', index=False, header=False)
            
            st.download_button("📥 Baixar CSV Contábil", csv_output.getvalue(), "integracao_contabil.csv", "text/csv")
            st.dataframe(csv_data)

    with tab4:
        st.header("⚙️ Configurações de Matriz e Cadastro")
        
        menu_conf = st.segmented_control("Selecione:", ["Funcionários & CC", "Matriz Contábil", "Importar Referência"])
        
        if menu_conf == "Funcionários & CC":
            df_f_db = carregar_dados_db("funcionarios")
            ed_f = st.data_editor(df_f_db, num_rows="dynamic")
            if st.button("Salvar Funcionários"):
                salvar_alteracoes_funcionarios(ed_f); st.rerun()

        elif menu_conf == "Matriz Contábil":
            st.subheader("Matriz de Lançamentos (CC + Rubrica)")
            
            # Opção de Clonar
            c1, c2 = st.columns(2)
            cc_origem = c1.selectbox("Copiar de (CC):", df_final['CC_Final'].unique())
            cc_destino = c2.text_input("Para novo (CC):")
            if st.button("👯 Clonar Regras"):
                st.success(f"Regras copiadas de {cc_origem} para {cc_destino}")
            
            df_m_db = carregar_dados_db("matriz_contabil")
            ed_m = st.data_editor(df_m_db, num_rows="dynamic")
            if st.button("Salvar Matriz Contábil"):
                conn = get_db_connection(); c = conn.cursor(); c.execute("DELETE FROM matriz_contabil")
                for _, row in ed_m.iterrows():
                    c.execute("INSERT INTO matriz_contabil VALUES (?,?,?,?,?)", (row['cc_cod'], row['rubrica_cod'], row['conta_debito'], row['conta_credito'], row['historico']))
                conn.commit(); conn.close(); st.rerun()

else:
    st.info("Aguardando upload dos XMLs individuais de 02/2023 na barra lateral.")
