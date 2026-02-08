import streamlit as st
from db.client import get_supabase_client

def show_login():
    st.container()
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("Ciplas AI-Master Cord")
        st.subheader("Acceso al Sistema")
        
        email = st.text_input("Correo Electrónico")
        password = st.text_input("Contraseña", type="password")
        
        if st.button("Iniciar Sesión", use_container_width=True):
            if email == "admin@ciplas.com" and password == "admin123":
                st.session_state.authenticated = True
                st.session_state.user_role = "admin"
                st.success("¡Bienvenido!")
                st.rerun()
            else:
                st.error("Credenciales incorrectas")

        st.info("Solo personal autorizado del Master Planning o Supervisión.")
