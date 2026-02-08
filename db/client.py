import os
import streamlit as st
from supabase import create_client

def get_supabase_client():
    """
    Obtiene el cliente de Supabase buscando credenciales en:
    1. st.secrets (Para cuando la app corre en Streamlit Cloud)
    2. os.environ (Para cuando corre localmente con .env)
    """
    
    # Intentar obtener de los Secrets de Streamlit Cloud primero
    # st.secrets funciona como un diccionario con las llaves que guardaste
    url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")

    # Si después de buscar en ambos lados no están, informamos al usuario
    if not url or not key:
        st.error("⚠️ Error de configuración: No se encontraron las credenciales de Supabase.")
        st.info("Verifica que las variables SUPABASE_URL y SUPABASE_KEY estén en los Secrets de Streamlit Cloud.")
        raise ValueError("Credenciales de Supabase no definidas.")

    return create_client(url, key)
