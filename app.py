import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import re

# Configuração da página
st.set_page_config(page_title="Leitor eSocial XML 2.0", layout="wide")

st.title("📂 Extrator de Dados do eSocial (S-1200/S-1210)")
st.markdown("""
**Versão Corrigida:** Limpeza profunda de namespaces para garantir leitura de arquivos de retorno.
1. Envie o arquivo **.ZIP** ou XMLs soltos.
2. O sistema limpará os cabeçalhos para encontrar os dados de remuneração.
""")

def clean_xml_content(xml_content):
    """
    Função robusta para limpar namespaces e prefixos do XML.
    Isso permite que o Python encontre as tags apenas pelo nome, 
    independente da versão do eSocial.
    """
    try:
        # 1. Garante que é string
        if isinstance(xml_content, bytes):
            xml_str = xml_content.decode('utf-8', errors='ignore')
        else:
            xml_str = xml_content
            
        # 2. Remove declarações de namespace (xmlns="..." e xmlns:prefix="...")
        # Removemos de forma GLOBAL (sem count=1) para pegar namespaces aninhados
        xml_str = re.sub(r'\sxmlns(:[a-zA-Z0-9]+)?="[^"]+"', '', xml_str)
        
        # 3. Remove prefixos de tags (ex: <esocial:evtRemun> vira <evtRemun>)
        xml_str = re.sub(r'<([a-zA-Z0-9]+):', '<', xml_str)
        xml_str = re.sub(r'</([a-zA-Z0-9]+):', '</', xml_str)
        
        return xml_str
    except Exception as e:
        return xml_content

def process_xml_file(file_content, filename):
    data_rows = []
    
    try:
        clean_xml = clean_xml_content(file_content)
        root = ET.fromstring(clean_xml)
        
        # Busca recursiva por 'evtRemun' (S-1200) em qualquer profundidade
        eventos = root.findall(".//evtRemun")
        
        # Debug: Se não achar, tenta ver se é um S-1210 (Pagamentos) só para avisar
        if not eventos:
             # Se quiser expandir no futuro para S-1210, a lógica seria aqui
             return []

        for evt in eventos:
            # --- Cabeçalho do Evento ---
            # Busca segura (tenta caminhos diferentes caso a estrutura varie)
            ide_evento = evt.find("ideEvento")
            per_apur = ide_evento.find("perApur").text if ide_evento is not None and ide_evento.find("perApur") is not None else "N/A"
            
            ide_trab = evt.find("ideTrabalhador")
            cpf_val = ide_trab.find("cpfTrab").text if ide_trab is not None and ide_trab.find("cpfTrab") is not None else "N/A"
            
            # --- Loop pelos Demonstrativos (ideDmDev) ---
            # Um XML pode ter múltiplos demonstrativos (Ex: Férias + Salário)
            demonstrativos = evt.findall(".//dmDev")
            
            for dm in demonstrativos:
                ide_dm = dm.find("ideDmDev")
                id_demo = ide_dm.text if ide_dm is not None else ""
                
                # Identifica tipo de folha baseado no ID do demonstrativo
                tipo_folha = "Outros"
                if "FO" in id_demo: tipo_folha = "Mensal"       # Folha Normal
                elif "FE" in id_demo: tipo_folha = "Férias"     # Férias Gozadas
                elif "FA" in id_demo: tipo_folha = "Ant. Férias" # Férias Anterior
                elif "13" in id_demo: tipo_folha = "13º Salário"

                # --- Itens de Remuneração (Rubricas) ---
                itens = dm.findall(".//itensRemun")
                
                for item in itens:
                    cod_rubr = item.find("codRubr").text if item.find("codRubr") is not None else ""
                    
                    # Valor da rubrica
                    vr_rubr = item.find("vrRubr").text if item.find("vrRubr") is not None else "0.00"
                    
                    # Referência (Qtde Horas ou Fator) - Opcional
                    qtd_rubr = item.find("qtdRubr")
                    ref_valor = qtd_rubr.text if qtd_rubr is not None else ""
                    
                    try:
                        valor = float(vr_rubr)
                    except:
                        valor = 0.00

                    data_rows.append({
                        "Arquivo": filename,
                        "Competencia": per_apur,
                        "CPF": cpf_val,
                        "Tipo Folha": tipo_folha,
                        "ID Demonstrativo": id_demo,
                        "Cod Rubrica": cod_rubr,
                        "Referencia": ref_valor,
                        "Valor": valor
                    })
                    
    except Exception as e:
        # print(f"Erro no arquivo {filename}: {e}") # Opcional para debug
        return []

    return data_rows

# --- Interface ---
uploaded_file = st.file_uploader("Arraste o arquivo ZIP ou XMLs aqui", 
                                 type=["zip", "xml"], 
                                 accept_multiple_files=True)

all_data = []

if uploaded_file:
    if st.button("Processar Arquivos"):
        with st.spinner('Processando...'):
            
            # Lógica para múltiplos arquivos ou ZIP
            files_to_process = []
            
            # Se for lista (multiplos arquivos upados)
            if isinstance(uploaded_file, list):
                for f in uploaded_file:
                    if f.name.endswith('.xml'):
                        files_to_process.append((f.name, f.read()))
                    elif f.name.endswith('.zip'):
                        with zipfile.ZipFile(f) as z:
                            for name in z.namelist():
                                if name.endswith('.xml'):
                                    files_to_process.append((name, z.read(name)))
            
            # Se for um único arquivo (não lista)
            else:
                if uploaded_file.name.endswith('.zip'):
                    with zipfile.ZipFile(uploaded_file) as z:
                        for name in z.namelist():
                            if name.endswith('.xml'):
                                files_to_process.append((name, z.read(name)))
                elif uploaded_file.name.endswith('.xml'):
                    files_to_process.append((uploaded_file.name, uploaded_file.read()))

            # Processamento real
            progress_bar = st.progress(0)
            total_files = len(files_to_process)
            
            for i, (fname, fcontent) in enumerate(files_to_process):
                rows = process_xml_file(fcontent, fname)
                all_data.extend(rows)
                progress_bar.progress((i + 1) / total_files)

        # --- Resultados ---
        if all_data:
            df = pd.DataFrame(all_data)
            
            st.success(f"Sucesso! {len(df)} rubricas extraídas de {total_files} arquivos.")
            
            # Preview
            st.dataframe(df.head(10))
            
            # Tabela Dinâmica (Pivot) para visualização rápida
            if not df.empty:
                st.subheader("Resumo Rápido (Soma por Rubrica)")
                pivot = df.groupby(['Competencia', 'Cod Rubrica'])['Valor'].sum().reset_index().sort_values('Valor', ascending=False)
                st.dataframe(pivot)

            # Download
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Base_Completa')
                if not df.empty:
                    pivot.to_excel(writer, index=False, sheet_name='Resumo_Rubricas')
            
            st.download_button(
                label="📥 Baixar Excel Processado",
                data=output.getvalue(),
                file_name="Relatorio_eSocial_S1200.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.error("Ainda não foi possível encontrar dados. Verifique se os arquivos são do tipo S-1200 (Remuneração).")
