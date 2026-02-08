import streamlit as st
import os

def show_ai_consultant():
    st.title("🤖 Consultoría IA (Ciplas AI-Master Cord)")
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("¿Cómo optimizamos hoy?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            response_text = f"Analizando planta... He detectado que la máquina T14 tiene capacidad ociosa para Denier {prompt}. Sugiero mover la carga de T11 para evitar el cuello de botella."
            st.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})

    if st.button("Generar Escenario Óptimo (Auto)"):
        st.info("IA analizando backlog y producción actual...")
        st.warning("IA sugiere: Adelantar producción de Denier 9000 en T12.")
