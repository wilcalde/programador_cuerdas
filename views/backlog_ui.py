import streamlit as st
import pandas as pd
from datetime import datetime
from db.queries import DBQueries

def show_backlog():
    st.title("📋 Backlog de Pedidos")
    db = DBQueries()
    
    # Initialize session state for edit mode
    if 'editing_order_id' not in st.session_state:
        st.session_state.editing_order_id = None
    
    # Order form section (Create or Edit)
    if st.session_state.editing_order_id:
        expander_title = "✏️ Editar Pedido"
        expanded = True
    else:
        expander_title = "➕ Añadir Nuevo Pedido"
        expanded = False
    
    with st.expander(expander_title, expanded=expanded):
        denier_options = ["2000", "2500", "3000", "4000", "6000", "6000 expo", "9000", "12000", "12000 expo", "18000"]
        deniers = db.get_deniers()
        
        # If editing, get the order data
        if st.session_state.editing_order_id:
            orders = db.get_orders()
            current_order = next((o for o in orders if o['id'] == st.session_state.editing_order_id), None)
            if current_order:
                # Get denier name from denier_id
                denier_name = next((d['name'] for d in deniers if d['id'] == current_order['denier_id']), "6000")
                default_denier_index = denier_options.index(denier_name) if denier_name in denier_options else 4
                default_kg = current_order['total_kg']
                # Parse date string to date object
                try:
                    default_date = datetime.strptime(current_order['required_date'], '%Y-%m-%d').date()
                except:
                    default_date = datetime.now().date()
            else:
                st.session_state.editing_order_id = None
                st.rerun()
        else:
            default_denier_index = 4
            default_kg = 1.0
            default_date = datetime.now().date()
        
        col1, col2 = st.columns(2)
        with col1:
            denier_sel = st.selectbox("Seleccionar Denier", denier_options, index=default_denier_index, key="order_denier")
            kg_totales = st.number_input("KG Totales", min_value=1.0, step=100.0, value=default_kg, key="order_kg")
        with col2:
            fecha = st.date_input("Fecha Requerida", value=default_date, key="order_date")
        
        button_cols = st.columns([1, 1, 3])
        
        if st.session_state.editing_order_id:
            # Update button
            if button_cols[0].button("💾 Actualizar", use_container_width=True):
                denier_id = next((d['id'] for d in deniers if d['name'] == denier_sel), None)
                if not denier_id:
                    try:
                        db.create_denier(denier_sel, 37.0)
                        new_deniers = db.get_deniers()
                        denier_id = next((d['id'] for d in new_deniers if d['name'] == denier_sel), None)
                    except Exception as e:
                        st.error(f"Error al crear denier: {e}")
                
                if denier_id:
                    db.update_order(st.session_state.editing_order_id, denier_id, kg_totales, str(fecha))
                    st.success("✅ Pedido actualizado correctamente")
                    st.session_state.editing_order_id = None
                    st.rerun()
            
            # Cancel button
            if button_cols[1].button("❌ Cancelar", use_container_width=True):
                st.session_state.editing_order_id = None
                st.rerun()
        else:
            # Create button
            if button_cols[0].button("💾 Guardar Pedido", use_container_width=True):
                denier_id = next((d['id'] for d in deniers if d['name'] == denier_sel), None)
                
                if not denier_id:
                    try:
                        db.create_denier(denier_sel, 37.0)
                        new_deniers = db.get_deniers()
                        denier_id = next((d['id'] for d in new_deniers if d['name'] == denier_sel), None)
                    except Exception as e:
                        st.error(f"Error al crear denier: {e}")
                
                if denier_id:
                    db.create_order(denier_id, kg_totales, str(fecha))
                    st.success(f"✅ Pedido de {denier_sel} deniers añadido")
                    st.rerun()
    
    st.divider()
    
    # Fetch pending orders from database
    orders = db.get_orders()
    
    if orders and len(orders) > 0:
        # Convert to DataFrame for better display
        df = pd.DataFrame(orders)
        
        # Display summary metrics
        col1, col2 = st.columns(2)
        with col1:
            total_orders = len(df)
            st.metric("Total Pedidos", total_orders)
        with col2:
            if 'total_kg' in df.columns:
                total_kg = df['total_kg'].sum()
                st.metric("Total KG Pendientes", f"{total_kg:,.0f}")
        
        st.divider()
        
        # Display orders table with actions
        st.subheader("Detalle de Pedidos")
        
        # Create a more interactive table with edit/delete buttons
        for idx, order in enumerate(orders):
            with st.container():
                cols = st.columns([3, 2, 2, 2, 1, 1])
                
                # Get denier name
                denier_name = next((d['name'] for d in deniers if d['id'] == order.get('denier_id')), 'N/A')
                
                cols[0].write(f"**Denier {denier_name}**")
                cols[1].write(f"{order.get('total_kg', 0):,.0f} kg")
                cols[2].write(f"{order.get('required_date', 'N/A')}")
                cols[3].write(f"Producido: {order.get('produced_kg', 0):,.0f} kg")
                
                # Edit button
                if cols[4].button("✏️", key=f"edit_{order['id']}", help="Editar"):
                    st.session_state.editing_order_id = order['id']
                    st.rerun()
                
                # Delete button with confirmation
                if cols[5].button("🗑️", key=f"delete_{order['id']}", help="Eliminar"):
                    # Use a confirmation dialog
                    st.session_state[f"confirm_delete_{order['id']}"] = True
                
                # Show confirmation dialog if delete was clicked
                if st.session_state.get(f"confirm_delete_{order['id']}", False):
                    st.warning(f"⚠️ ¿Estás seguro de eliminar el pedido de {denier_name} ({order.get('total_kg', 0)} kg)?")
                    confirm_cols = st.columns([1, 1, 3])
                    if confirm_cols[0].button("✅ Sí, eliminar", key=f"confirm_yes_{order['id']}"):
                        db.delete_order(order['id'])
                        st.success(f"✅ Pedido eliminado correctamente")
                        st.session_state[f"confirm_delete_{order['id']}"] = False
                        st.rerun()
                    if confirm_cols[1].button("❌ Cancelar", key=f"confirm_no_{order['id']}"):
                        st.session_state[f"confirm_delete_{order['id']}"] = False
                        st.rerun()
                
                st.divider()
        
    else:
        st.info("No hay pedidos pendientes en el backlog.")
        st.write("Todos los pedidos han sido completados o no hay órdenes registradas.")
