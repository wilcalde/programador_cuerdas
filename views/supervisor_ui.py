import streamlit as st
from db.queries import DBQueries

def show_reports():
    st.title("📝 Reporte de Novedades")
    db = DBQueries()
    
    with st.form("report_form"):
        st.subheader("Registrar Evento")
        # Logic for report form...
        if st.form_submit_button("Guardar Novedad", use_container_width=True):
            st.success("Reporte guardado.")
