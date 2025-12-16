import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import re

# --- Configuração da Página ---
st.set_page_config(page_title="Auditoria Folha eSocial", layout="wide", page_icon="📊")

st.title("📊 Auditoria de Folha eSocial (S-1200)")
st.markdown("""
Esta ferramenta processa os XMLs de remuneração, classifica proventos e descontos (estimativa) 
e gera demonstrativos individuais.
""")

# --- Funções de Lógica (Mantêm-se iguais) ---

def clean_xml_content(xml_content):
    """Limpeza profunda de namespaces."""
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

def estimar_tipo_rubrica(codigo, valor):
    """Tenta adivinhar se é Provento, Desconto ou Informativo."""
    code_upper = str(codigo).upper()
    keywords_desc = ['INSS', 'IRRF', 'DESC', 'ADIANT', 'FALT', 'ATRASO', 'RETENCAO', 'VALE', 'VR', 'VT']
    keywords_info = ['BASE', 'FGTS']
    
    for k in keywords_info:
        if k in code_upper: return "Informativo"
    for k in keywords_desc:
        if k in code_upper: return "Desconto"
    return "Provento"

def process_xml_file(file_content, filename):
    data_rows = []
    try:
        clean_xml = clean_xml_content(file_content)
        root = ET.fromstring(clean_xml)
        eventos = root.findall(".//evtRemun")
        
        for evt in eventos:
            ide_evento = evt.find("ideEvento")
            per_apur = ide_evento.find("perApur").text if ide_evento is not None else "N/A"
            ide_trab = evt.find("ideTrabalhador")
            cpf_val = ide_trab.find("cpfTrab").text if ide_trab is not None else "N/A"
            
            demonstrativos = evt.findall(".//dmDev")
            for dm in demonstrativos:
                id_demo = dm.find("ideDmDev").text if dm.find("ideDmDev") is not None else ""
                itens = dm.findall(".//itensRemun")
                for item in itens:
                    cod_rubr = item.find("codRubr").text if item.find("codRubr") is not None else ""
                    vr_rubr = item.find("vrRubr").text if item.find("vrRubr") is not None else "0.00"
                    try: valor = float(vr_rubr)
                    except: valor = 0.00
                        
                    tipo_estimado = estimar_tipo_rubrica(cod_rubr, valor)
                    data_rows.append({
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "ID Demonstrativo": id_demo,
                        "Rubrica": cod_rubr,
                        "Tipo (Est.)": tipo_estimado,
                        "Valor": valor,
                        "Arquivo Origem": filename
                    })
    except Exception:
        return []
    return data_rows

# --- Interface Principal ---

uploaded_file = st.file_uploader("📂 Arraste o ZIP ou XMLs do eSocial", type=["zip", "xml"], accept_multiple_files=True)

# Lógica de Botão e Session State (Correção do Erro de Reload)
if uploaded_file:
    # Se clicar no botão, processa e SALVA no estado
    if st.button("🚀 Processar Folha"):
        with st.spinner('Lendo e estruturando dados...'):
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

            for fname, fcontent in files_to_process:
                all_data.extend(process_xml_file(fcontent, fname))
            
            # Salva no Session State para não perder ao clicar em outros botões
            if all_data:
                st.session_state['df_folha'] = pd.DataFrame(all_data)
                st.success("Dados processados com sucesso!")
            else:
                st.warning("Nenhum dado encontrado.")

# --- Exibição dos Dados (Lê direto da Memória/Session State) ---
if 'df_folha' in st.session_state:
    df = st.session_state['df_folha']
    
    # --- TABS DE VISUALIZAÇÃO ---
    tab1, tab2, tab3 = st.tabs(["📋 Resumo da Folha", "👤 Contracheques Individuais", "📥 Download"])
    
    with tab1:
        st.subheader("Visão Geral por Competência")
        resumo = df[df["Tipo (Est.)"].isin(["Provento", "Desconto"])].pivot_table(
            index="Competencia", 
            columns="Tipo (Est.)", 
            values="Valor", 
            aggfunc="sum", 
            fill_value=0
        )
        if "Desconto" not in resumo.columns: resumo["Desconto"] = 0
        if "Provento" not in resumo.columns: resumo["Provento"] = 0
        
        resumo["Líquido Estimado"] = resumo["Provento"] - resumo["Desconto"]
        st.dataframe(resumo.style.format("R$ {:,.2f}"))

    with tab2:
        st.subheader("Visualizador de Contracheque")
        
        col1, col2 = st.columns(2)
        with col1:
            cpfs = sorted(df["CPF"].unique())
            selected_cpf = st.selectbox("Selecione o CPF:", cpfs)
        with col2:
            # Filtra competências disponíveis para este CPF
            comps = sorted(df[df["CPF"] == selected_cpf]["Competencia"].unique())
            if comps:
                selected_comp = st.selectbox("Selecione a Competência:", comps)
            else:
                selected_comp = None

        if selected_cpf and selected_comp:
            # Filtrar dados
            mask = (df["CPF"] == selected_cpf) & (df["Competencia"] == selected_comp)
            df_func = df[mask]
            
            # Separar totais
            total_prov = df_func[df_func["Tipo (Est.)"] == "Provento"]["Valor"].sum()
            total_desc = df_func[df_func["Tipo (Est.)"] == "Desconto"]["Valor"].sum()
            total_liq = total_prov - total_desc
            
            st.divider()
            st.markdown(f"### 📄 CPF: {selected_cpf} | Mês: {selected_comp}")
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Total Proventos", f"R$ {total_prov:,.2f}")
            c2.metric("Total Descontos", f"R$ {total_desc:,.2f}", delta_color="inverse")
            c3.metric("Líquido Estimado", f"R$ {total_liq:,.2f}")
            
            st.write("**Detalhamento das Rubricas:**")
            
            def color_rows(val):
                return 'color: red' if val == 'Desconto' else 'color: green' if val == 'Provento' else 'color: gray'
            
            st.dataframe(
                df_func[["Rubrica", "Tipo (Est.)", "Valor"]]
                .style.applymap(color_rows, subset=['Tipo (Est.)'])
                .format({"Valor": "R$ {:.2f}"}),
                use_container_width=True
            )
        else:
            st.info("Selecione um CPF e Competência para visualizar.")

    with tab3:
        st.subheader("Baixar Relatórios")
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Base_Completa')
            if not df.empty:
                resumo_func = df.pivot_table(
                    index=["Competencia", "CPF"], 
                    columns="Tipo (Est.)", 
                    values="Valor", 
                    aggfunc="sum",
                    fill_value=0
                ).reset_index()
                resumo_func["Liquido"] = resumo_func.get("Provento", 0) - resumo_func.get("Desconto", 0)
                resumo_func.to_excel(writer, index=False, sheet_name='Resumo_Por_Funcionario')
        
        st.download_button(
            label="📥 Baixar Planilha Completa (Excel)",
            data=output.getvalue(),
            file_name="Folha_eSocial_Detalhada.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
