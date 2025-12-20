import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io

# --- Configuração da Página ---
st.set_page_config(page_title="Extrator eSocial Pro", layout="wide", page_icon="📑")

st.title("📑 Extrator de Eventos eSocial (S-1200)")
st.markdown("Extração de códigos, descrições e classificações oficiais direto dos XMLs.")

# --- FUNÇÃO DE PROCESSAMENTO ---
def processar_xml_esocial(content):
    data = []
    try:
        root = ET.fromstring(content)
        # Namespace do eSocial costuma variar, buscamos pelo final da tag
        def find_tag(parent, suffix):
            for child in parent.iter():
                if child.tag.endswith(suffix): return child.text
            return None

        per_apur = find_tag(root, 'perApur')
        cpf = find_tag(root, 'cpfTrab')
        nome_trab = find_tag(root, 'nmTrab')

        # Percorre demonstrativos e itens
        for item in root.iter():
            if item.tag.endswith('itensRemun'):
                cod = find_tag(item, 'codRubr')
                desc = find_tag(item, 'dscRubr')
                valor = float(find_tag(item, 'vrRubr') or 0)
                tp = find_tag(item, 'tpRubr')
                
                # Classificação Oficial eSocial
                if tp == '1': classe = "Vencimento"
                elif tp == '2': classe = "Desconto"
                elif tp in ['3', '4']: classe = "Informativo"
                else: classe = "Outros"

                data.append({
                    "Competência": per_apur,
                    "CPF": cpf,
                    "Nome": nome_trab,
                    "Código": cod,
                    "Descrição": desc,
                    "Classificação": classe,
                    "Valor": valor
                })
    except Exception as e:
        pass 
    return data

# --- INTERFACE ---
uploaded_zip = st.file_uploader("Suba o arquivo ZIP com os XMLs", type=["zip"])

if uploaded_zip:
    all_data = []
    with zipfile.ZipFile(uploaded_zip) as z:
        xml_files = [f for f in z.namelist() if f.endswith('.xml')]
        st.info(f"Encontrados {len(xml_files)} arquivos XML no ZIP.")
        
        for file_name in xml_files:
            with z.open(file_name) as f:
                content = f.read()
                all_data.extend(processar_xml_esocial(content))

    if all_data:
        df = pd.DataFrame(all_data)
        
        st.subheader("📊 Relação de Eventos Extraída")
        st.dataframe(df, use_container_width=True)

        # --- ÁREA DE DOWNLOAD ---
        st.divider()
        col1, col2 = st.columns(2)
        
        with col1:
            # Gerar Excel em memória
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Eventos_eSocial')
            
            st.download_button(
                label="📥 Baixar Relação Completa (Excel)",
                data=output.getvalue(),
                file_name=f"Relacao_eSocial_{df['Competência'].iloc[0]}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
        with col2:
            # Resumo por Classificação para conferência rápida
            resumo = df.groupby('Classificação')['Valor'].sum().reset_index()
            st.write("**Resumo de Conferência:**")
            st.table(resumo.style.format({"Valor": "R$ {:,.2f}"}))

else:
    st.info("Aguardando o upload do arquivo ZIP contendo os XMLs individuais.")
