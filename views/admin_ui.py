import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px
from db.queries import DBQueries
from logic.formulas import get_kgh_torsion, get_n_optimo_rew

def show_admin():
    st.title("🛡️ Panel de Administración")
    
    db = DBQueries()
    
    tabs = st.tabs(["📊 Vista de Producción", "⚙️ Configuración Maquinaria", "📅 Calendario de Turnos"])
    
    with tabs[0]:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("Programación Actual")
            # Logic to show current production status
            st.info("Visualización del plan de producción activo...")
            
            # Mock data for demonstration
            machines = ["Torsionadora 01", "Torsionadora 02", "Torsionadora 03", "Torsionadora 04", "Torsionadora 05"]
            status = ["Produciendo", "Configuración", "Produciendo", "Produciendo", "Mantenimiento"]
            completion = [75, 20, 45, 90, 0]
            
            df_status = pd.DataFrame({
                "Máquina": machines,
                "Estado": status,
                "Avance": completion
            })
            st.table(df_status)
    
    with tabs[1]:
        st.subheader("Configuración Técnica")
        
        # Add new denier option
        with st.expander("➕ Añadir Nuevo Denier al Catálogo"):
            col_a, col_b = st.columns(2)
            new_denier_name = col_a.text_input("Nombre Denier (ej: 7500)")
            new_denier_cycle = col_b.number_input("Ciclo Estándar (seg)", value=37.0)
            if st.button("Guardar Nuevo Denier"):
                if new_denier_name:
                    db.create_denier(new_denier_name, new_denier_cycle)
                    st.success(f"Denier {new_denier_name} añadido correctamente")
                    st.rerun()

        st.divider()
        
        # Torsion Machine Configuration
        st.markdown("### 🧵 Parámetros de Torsión")
        machines = db.get_machines_torsion()
        machine_ids = [m['id'] for m in machines]
        
        sel_machine = st.selectbox("Seleccionar Máquina para Configurar", machine_ids)
        
        if sel_machine:
            st.write(f"Configurando **{sel_machine}**")
            
            denier_options = ["2000", "2500", "3000", "4000", "6000", "6000 expo", "9000", "12000", "12000 expo", "18000"]
            
            # Fetch existing configs for this machine
            current_configs = db.get_config_for_machine(sel_machine)
            config_dict = {c['denier']: c for c in current_configs}
            
            # Create a grid/table for input
            st.markdown("""
                <style>
                .config-header { font-weight: bold; color: #4e73df; }
                </style>
            """, unsafe_allow_html=True)
            
            cols = st.columns([1.5, 1.5, 1.5, 1.5, 2])
            cols[0].markdown("**Denier**")
            cols[1].markdown("**RPM**")
            cols[2].markdown("**T/m**")
            cols[3].markdown("**Husos**")
            cols[4].markdown("**Calculado (Kg/h)**")
            
            updated_data = {}
            
            for denier in denier_options:
                c = config_dict.get(denier, {})
                
                with st.container():
                    r_cols = st.columns([1.5, 1.5, 1.5, 1.5, 2])
                    r_cols[0].write(f"**{denier}**")
                    rpm = r_cols[1].number_input("RPM", value=int(c.get('rpm', 0)), key=f"rpm_{sel_machine}_{denier}", label_visibility="collapsed")
                    tm = r_cols[2].number_input("T/m", value=int(c.get('torsiones_metro', 0)), key=f"tm_{sel_machine}_{denier}", label_visibility="collapsed")
                    husos = r_cols[3].number_input("Husos", value=int(c.get('husos', 0)), key=f"husos_{sel_machine}_{denier}", label_visibility="collapsed")
                    
                    # Calculate Kg/h
                    if rpm > 0 and tm > 0 and husos > 0:
                        # Extract numeric part (e.g., "6000 expo" -> 6000)
                        try:
                            denier_val = float(denier.split(' ')[0])
                            kgh = get_kgh_torsion(denier_val, rpm, tm, husos)
                            r_cols[4].info(f"{kgh:.2f} Kg/h")
                            updated_data[denier] = {"rpm": rpm, "torsiones": tm, "husos": husos}
                        except ValueError:
                            r_cols[4].write("-")
                    else:
                        r_cols[4].write("-")
            
            if st.button(f"💾 Guardar Cambios para {sel_machine}", type="primary"):
                for den, vals in updated_data.items():
                    db.upsert_machine_denier_config(sel_machine, den, vals['rpm'], vals['torsiones'], vals['husos'])
                st.success(f"Configuración de {sel_machine} actualizada")
                st.rerun()

        st.divider()
        
        # Rewinder Configuration
        st.markdown("### 🔄 Parámetros de Rewinder")
        rewinder_configs = db.get_rewinder_denier_configs()
        rew_dict = {c['denier']: c for c in rewinder_configs}
        
        rew_cols = st.columns([2, 2, 2, 2, 2])
        rew_cols[0].markdown("**Denier**")
        rew_cols[1].markdown("**Mp (seg)**")
        rew_cols[2].markdown("**Tm (min)**")
        rew_cols[3].markdown("**N (Máq/Op)**")
        rew_cols[4].markdown("**Kg/h (80%)**")
        
        rew_updates = {}
        for denier in denier_options:
            rc = rew_dict.get(denier, {})
            with st.container():
                rc_cols = st.columns([2, 2, 2, 2, 2])
                rc_cols[0].write(f"**{denier}**")
                mp = rc_cols[1].number_input("Mp", value=float(rc.get('mp_segundos', 37.0)), key=f"mp_{denier}", label_visibility="collapsed")
                tm = rc_cols[2].number_input("Tm", value=float(rc.get('tm_minutos', 0.0)), key=f"tm_{denier}", label_visibility="collapsed")
                
                if tm > 0:
                    n = get_n_optimo_rew(tm, mp)
                    kgh_r = (60 / tm) * 0.8
                    rc_cols[3].write(f"{n}")
                    rc_cols[4].info(f"{kgh_r:.1f}")
                    rew_updates[denier] = {"mp": mp, "tm": tm}
                else:
                    rc_cols[3].write("-")
                    rc_cols[4].write("-")
        
        if st.button("💾 Guardar Cambios Rewinder", type="primary"):
            for den, vals in rew_updates.items():
                db.upsert_rewinder_denier_config(den, vals['mp'], vals['tm'])
            st.success("Configuración Rewinder actualizada")
            st.rerun()

    with tabs[2]:
        st.subheader("Calendario de Disponibilidad")
        st.write("Defina las horas de operación de la planta para los próximos 15 días.")
        
        # Get start/end dates
        today = datetime.now()
        dates = [today + timedelta(days=i) for i in range(15)]
        
        # Fetch existing shifts
        shifts_db = db.get_shifts(dates[0].strftime('%Y-%m-%d'), dates[-1].strftime('%Y-%m-%d'))
        shifts_dict = {s['date']: s['working_hours'] for s in shifts_db}
        
        # Create form for shifts
        with st.form("shifts_form"):
            cols = st.columns(5)
            new_shifts = {}
            
            for i, d in enumerate(dates):
                col_idx = i % 5
                d_str = d.strftime('%Y-%m-%d')
                with cols[col_idx]:
                    st.write(f"**{d.strftime('%a %d/%m')}**")
                    current_h = shifts_dict.get(d_str, 24)
                    new_h = st.selectbox(
                        "Horas", 
                        options=[0, 8, 12, 16, 24], 
                        index=[0, 8, 12, 16, 24].index(current_h),
                        key=f"shift_{d_str}",
                        label_visibility="collapsed"
                    )
                    new_shifts[d_str] = new_h
            
            if st.form_submit_button("Guardar Calendario de Turnos"):
                for d_str, h in new_shifts.items():
                    db.upsert_shift(d_str, h)
                st.success("Calendario actualizado correctamente")
                st.rerun()
