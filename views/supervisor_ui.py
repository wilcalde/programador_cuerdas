import streamlit as st
from db.queries import DBQueries

def show_reports():
    st.title("📝 Reporte de Novedades")
    db = DBQueries()
    
    with st.form("report_form"):
        st.subheader("Registrar Evento")
        
        machines = db.get_machines_torsion()
        machine_ids = [m['id'] for m in machines] if machines else ["T11", "T12", "T14", "T15", "T16"]
        
        col1, col2 = st.columns(2)
        with col1:
            machine_sel = st.selectbox("Máquina", machine_ids)
            report_type = st.selectbox("Tipo de Novedad", [
                "Husos Dañados", 
                "Paro por Operario", 
                "Limpieza", 
                "Falta de Materia Prima",
                "Falla Mecánica"
            ])
        with col2:
            impact = st.number_input("Horas de Afectación", min_value=0.0, step=0.5)
            
        description = st.text_area("Descripción de la novedad")
        
        if st.form_submit_button("Guardar Novedad", use_container_width=True):
            db.create_report(machine_sel, report_type, description, impact)
            st.success("Reporte guardado. El plan se recalibrará automáticamente.")

    st.divider()
    st.subheader("Historico de Novedades (Hoy)")
    # Logic to fetch and show today's reports
    st.info("Visualización de reportes recientes.")
