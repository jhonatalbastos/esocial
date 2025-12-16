import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io

# Configuração da página
st.set_page_config(page_title="Leitor eSocial XML", layout="wide")

st.title("📂 Extrator de Dados do eSocial (S-1200/S-1210)")
st.markdown("""
Esta ferramenta transforma os arquivos **XML** do eSocial em uma planilha Excel organizada.
1. Faça o download do pacote de eventos no portal eSocial.
2. Envie o arquivo **.ZIP** ou selecione múltiplos arquivos **.XML** abaixo.
""")

def remove_namespaces(xml_content):
    """
    Remove namespaces do XML para facilitar a busca das tags, 
    independente da versão do layout do eSocial.
    """
    try:
        # Tenta decodificar se for bytes
        if isinstance(xml_content, bytes):
            xml_str = xml_content.decode('utf-8', errors='ignore')
        else:
            xml_str = xml_content
            
        # Remove declarações de namespace simples
        import re
        xml_str = re.sub(r'\sxmlns="[^"]+"', '', xml_str, count=1)
        return xml_str
    except Exception as e:
        return xml_content

def process_xml_file(file_content, filename):
    """
    Processa um único arquivo XML e extrai dados de remuneração (S-1200).
    """
    data_rows = []
    
    try:
        # Limpeza básica de namespace para facilitar parsing
        clean_xml = remove_namespaces(file_content)
        root = ET.fromstring(clean_xml)
        
        # Como removemos o namespace principal, buscamos as tags diretamente
        # A estrutura pode variar se for XML puro ou dentro de <retornoProcessamentoDownload>
        # Vamos buscar recursivamente as tags principais
        
        eventos = root.findall(".//evtRemun")
        
        if not eventos:
            # Tenta buscar com namespace curinga ou estrutura direta se a limpeza falhou
            return []

        for evt in eventos:
            # Dados Gerais
            per_apur = evt.find(".//ideEvento/perApur")
            per_apur = per_apur.text if per_apur is not None else "N/A"
            
            cpf = evt.find(".//ideTrabalhador/cpfTrab")
            cpf_val = cpf.text if cpf is not None else "N/A"
            
            # Loop pelos Demonstrativos de Pagamento (ideDmDev)
            # Um funcionário pode ter Férias (FE) e Folha (FO) no mesmo arquivo
            demonstrativos = evt.findall(".//dmDev")
            
            for dm in demonstrativos:
                ide_dm_dev = dm.find("ideDmDev")
                id_demo = ide_dm_dev.text if ide_dm_dev is not None else ""
                
                # Identifica o tipo pelo código (FO=Folha, FE=Férias, FA=Férias Ant.)
                tipo_folha = "Outros"
                if "FO" in id_demo: tipo_folha = "Mensal"
                elif "FE" in id_demo: tipo_folha = "Férias"
                elif "FA" in id_demo: tipo_folha = "Antec. Férias"
                elif "13" in id_demo: tipo_folha = "13º Salário"

                # Itens de Remuneração (Rubricas)
                itens = dm.findall(".//itensRemun")
                
                for item in itens:
                    cod_rubr = item.find("codRubr").text if item.find("codRubr") is not None else ""
                    vr_rubr = item.find("vrRubr").text if item.find("vrRubr") is not None else "0.00"
                    
                    # Tenta converter valor para float
                    try:
                        valor = float(vr_rubr)
                    except:
                        valor = 0.00

                    # Adiciona linha na lista final
                    data_rows.append({
                        "Arquivo": filename,
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "ID Demonstrativo": id_demo,
                        "Tipo Folha": tipo_folha,
                        "Cod Rubrica": cod_rubr,
                        "Valor": valor
                    })
                    
    except Exception as e:
        # Se der erro em um arquivo, registra para debug mas não para o processo
        print(f"Erro ao processar {filename}: {e}")
        return []

    return data_rows

# --- Interface de Upload ---
uploaded_file = st.file_uploader("Arraste o arquivo ZIP ou selecione XMLs", 
                                 type=["zip", "xml"], 
                                 accept_multiple_files=True)

all_data = []

if uploaded_file:
    # Botão para iniciar processamento
    if st.button("Processar Arquivos"):
        with st.spinner('Lendo arquivos... Isso pode levar alguns instantes.'):
            
            # Caso 1: Usuário enviou múltiplos XMLs soltos
            if isinstance(uploaded_file, list):
                for file in uploaded_file:
                    if file.name.endswith(".xml"):
                        content = file.read()
                        rows = process_xml_file(content, file.name)
                        all_data.extend(rows)
            
            # Caso 2: Usuário enviou um único arquivo (pode ser ZIP ou XML único)
            else:
                if uploaded_file.name.endswith(".zip"):
                    with zipfile.ZipFile(uploaded_file) as z:
                        for filename in z.namelist():
                            if filename.endswith(".xml"):
                                with z.open(filename) as f:
                                    content = f.read()
                                    rows = process_xml_file(content, filename)
                                    all_data.extend(rows)
                elif uploaded_file.name.endswith(".xml"):
                    content = uploaded_file.read()
                    rows = process_xml_file(content, uploaded_file.name)
                    all_data.extend(rows)

        # --- Exibição dos Resultados ---
        if all_data:
            df = pd.DataFrame(all_data)
            
            # Formatação visual
            st.success(f"Processamento concluído! {len(all_data)} registros encontrados.")
            
            st.subheader("Prévia dos Dados")
            st.dataframe(df.head(50))
            
            # Resumo por Competência e Tipo (Pivot Table simples)
            st.subheader("Resumo por Tipo de Folha")
            if not df.empty:
                resumo = df.groupby(['Competencia', 'Tipo Folha'])['Valor'].sum().reset_index()
                st.dataframe(resumo)

            # --- Botão de Download ---
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Detalhado')
                if not df.empty:
                    resumo.to_excel(writer, index=False, sheet_name='Resumo')
            
            st.download_button(
                label="📥 Baixar Planilha Excel Completa",
                data=output.getvalue(),
                file_name="Folha_eSocial_Consolidada.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("Nenhum dado de remuneração (S-1200) encontrado nos arquivos enviados.")

else:
    st.info("Aguardando upload de arquivos...")
