import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import sqlite3
from fpdf import FPDF

# --- Configuração da Página ---
st.set_page_config(page_title="Gestor eSocial Auditor", layout="wide", page_icon="🏢")

st.title("🏢 Gestor eSocial: Auditoria & Contracheques")

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

init_db()

# --- GERADOR DE PDF (CONTRACHEQUE) ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, 'DEMONSTRATIVO DE PAGAMENTO DE SALÁRIO', 0, 1, 'C')
        self.ln(5)

def gerar_pdf_contracheque(dados_func, df_itens):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    
    # Cabeçalho
    pdf.cell(0, 10, f"Funcionário: {dados_func['Nome']}", ln=True)
    pdf.cell(0, 10, f"CPF: {dados_func['CPF']} | Competência: {dados_func['Comp']}", ln=True)
    pdf.ln(5)
    
    # Tabela
    pdf.set_fill_color(200, 220, 255)
    pdf.cell(20, 10, "Cód", 1, 0, 'C', 1)
    pdf.cell(80, 10, "Descrição", 1, 0, 'C', 1)
    pdf.cell(30, 10, "Vencimentos", 1, 0, 'C', 1)
    pdf.cell(30, 10, "Descontos", 1, 1, 'C', 1)
    
    total_v = 0
    total_d = 0
    
    for _, row in df_itens.iterrows():
        pdf.cell(20, 8, str(row['Rubrica']), 1)
        pdf.cell(80, 8, str(row['Descrição']), 1)
        
        v = row['Valor'] if row['Classificação'] == 'Vencimento' else 0
        d = row['Valor'] if row['Classificação'] == 'Desconto' else 0
        
        pdf.cell(30, 8, f"{v:,.2f}" if v > 0 else "", 1, 0, 'R')
        pdf.cell(30, 8, f"{d:,.2f}" if d > 0 else "", 1, 1, 'R')
        
        total_v += v
        total_d += d

    pdf.ln(5)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(130, 10, "VALOR LÍQUIDO:", 0, 0, 'R')
    pdf.cell(30, 10, f"R$ {total_v - total_d:,.2f}", 1, 1, 'C', 1)
    
    return pdf.output(dest='S').encode('latin-1')

# --- PROCESSAMENTO XML ---
def safe_find(element, tag):
    for node in element.iter():
        if node.tag.endswith(tag): return node.text
    return None

def processar_xml_v19(content):
    data = []
    root = ET.fromstring(content)
    per_apur = safe_find(root, 'perApur')
    cpf = safe_find(root, 'cpfTrab')
    nome_trab = safe_find(root, 'nmTrab')
    
    for dm in root.iter():
        if dm.tag.endswith('dmDev'):
            for item in dm.iter():
                if item.tag.endswith('itensRemun'):
                    cod = safe_find(item, 'codRubr')
                    valor = float(safe_find(item, 'vrRubr') or 0)
                    tp = safe_find(item, 'tpRubr')
                    # Busca descrição da rubrica se existir no XML
                    desc = safe_find(item, 'dscRubr') or cod 
                    
                    classe = "Vencimento" if tp == '1' else "Desconto" if tp == '2' else "Informativo"
                    
                    data.append({
                        "Competencia": per_apur, "CPF": cpf, "Nome": nome_trab,
                        "Rubrica": cod, "Descrição": desc, "Valor": valor, "Classificação": classe
                    })
    return data

# --- INTERFACE ---
st.sidebar.header("📂 Upload de Fevereiro/2023")
files = st.sidebar.file_uploader("Suba os XMLs", type=["xml", "zip"], accept_multiple_files=True)

if files:
    if st.sidebar.button("🚀 Processar Dados"):
        all_rows = []
        for f in files:
            if f.name.endswith('.zip'):
                with zipfile.ZipFile(f) as z:
                    for name in z.namelist():
                        if name.endswith('.xml'): all_rows.extend(processar_xml_v19(z.read(name)))
            else:
                all_rows.extend(processar_xml_v19(f.read()))
        st.session_state['df_v19'] = pd.DataFrame(all_rows)

if 'df_v19' in st.session_state:
    df = st.session_state['df_v19']
    
    tab1, tab2 = st.tabs(["📊 Auditoria de Rubricas", "👤 Gerar Contracheques"])

    with tab1:
        st.subheader("Resumo por Evento e Classificação")
        # Pivot para mostrar colunas claras de Vencimento, Desconto e Informativo com seus VALORES
        resumo = df.pivot_table(index=['Rubrica', 'Descrição'], 
                                columns='Classificação', 
                                values='Valor', 
                                aggfunc='sum', 
                                fill_value=0).reset_index()
        st.dataframe(resumo, use_container_width=True)

    with tab2:
        st.subheader("Selecione o funcionário para baixar o PDF")
        lista_funcs = df['Nome'].unique()
        func_sel = st.selectbox("Colaborador:", lista_funcs)
        
        if func_sel:
            df_func = df[df['Nome'] == func_sel]
            dados_pessoais = {
                "Nome": func_sel, 
                "CPF": df_func['CPF'].iloc[0], 
                "Comp": df_func['Competencia'].iloc[0]
            }
            
            st.table(df_func[['Rubrica', 'Descrição', 'Classificação', 'Valor']])
            
            pdf_bytes = gerar_pdf_contracheque(dados_pessoais, df_func)
            st.download_button(label="⬇️ Baixar Contracheque em PDF",
                               data=pdf_bytes,
                               file_name=f"Contracheque_{func_sel}.pdf",
                               mime="application/pdf")
