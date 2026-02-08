import streamlit as st
import pandas as pd
from datetime import datetime
from db.queries import DBQueries

def show_backlog():
    st.title("📋 Backlog de Pedidos")
    db = DBQueries()
    
    if 'editing_order_id' not in st.session_state:
        st.session_state.editing_order_id = None
    
    # Logic for order management...
    st.info("Gestiona aquí los pedidos pendientes.")
