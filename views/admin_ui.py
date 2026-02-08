import streamlit as st
import pandas as pd
from datetime import datetime
from db.queries import DBQueries
from logic.formulas import get_kgh_torsion, get_rafia_input
from integrations.openai_ia import generate_production_schedule
import plotly.express as px

def show_programming():
    st.title("📅 Programación de Producción")
    db = DBQueries()
    
    if 'scheduling_results' not in st.session_state:
        st.session_state.scheduling_results = None
    if 'last_scheduling_update' not in st.session_state:
        st.session_state.last_scheduling_update = None

    st.info("💡 La programación es generada por IA basándose en el backlog y la capacidad de los 28 puestos de Rewinder disponible.")
    
    col1, col2 = st.columns([2, 1])
    sc_data = db.get_all_scheduling_data()
    orders = sc_data['orders']
    capacities = sc_data['rewinder_capacities']
    
    denier_groups = {}
    if orders:
        for o in orders:
            d_name = o.get('deniers', {}).get('name', 'Unknown')
            denier_groups[d_name] = denier_groups.get(d_name, 0) + o.get('total_kg', 0)
    
    backlog_summary = {}
    total_req_h = 0
    for d_name, kg in denier_groups.items():
        cap = capacities.get(d_name, {})
        kg_h = cap.get('kg_per_hour', 0)
        hours_req = kg / kg_h if kg_h > 0 else 0
        total_req_h += hours_req
        backlog_summary[d_name] = {"kg_total": kg, "hours_req": hours_req}
    
    for d_name in backlog_summary:
        share = (backlog_summary[d_name]['hours_req'] / total_req_h * 100) if total_req_h > 0 else 0
        backlog_summary[d_name]['share_pct'] = round(share, 1)

    with col1:
        if st.button("🔄 Recalcular Programación con IA", use_container_width=True, type="primary"):
            with st.spinner("🤖 IA analizando backlog y capacidades..."):
                if not orders:
                    st.warning("⚠️ No hay pedidos en el backlog para programar.")
                else:
                    results = generate_production_schedule(
                        orders, 
                        capacities,
                        total_rewinders=28,
                        shifts=sc_data.get('shifts', []),
                        backlog_summary=backlog_summary
                    )
                    if "error" in results:
                        st.error(f"❌ Error de la IA: {results['error']}")
                    else:
                        st.session_state.scheduling_results = results
                        st.session_state.last_scheduling_update = datetime.now().strftime("%H:%M:%S")
                        st.success(f"✅ Programación actualizada a las {st.session_state.last_scheduling_update}")
                        st.rerun()
    
    if orders:
        st.subheader("📋 Análisis de Capacidad y Backlog")
        st.write("Datos de backlog cargados.")

def show_config():
    st.title("⚙️ Configuración del Sistema")
    st.markdown("---")
    
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Torsión", "🔄 Rewinder", "📖 Catálogo Deniers", "🕒 Turnos"])
    
    with tab1:
        st.header("Configuración por Máquina y Denier")
        maquinas = ["T14", "T15", "T16", "T11", "T12"]
        
        for m in maquinas:
            with st.expander(f"🏭 Máquina {m}", expanded=False):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.number_input(f"Eficiencia (%)", value=90.0, key=f"eff_{m}")
                with c2:
                    st.number_input(f"Velocidad (m/min)", value=150.0, key=f"vel_{m}")
                with c3:
                    st.number_input(f"Capacidad (Kg/h)", value=25.0, key=f"cap_{m}")
        
        if st.button("💾 Guardar Cambios de Torsión", use_container_width=True):
            st.toast("Configuración de torsión guardada temporalmente.")

    with tab2:
        st.subheader("Capacidad de Puestos")
        st.metric("Rewinders Totales", "28 Puestos")
        st.slider("Puestos Operativos hoy", 0, 28, 28, help="Ajuste según disponibilidad de personal.")

    st.success("✅ Interfaz de configuración sincronizada.")
