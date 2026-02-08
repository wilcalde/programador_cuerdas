import streamlit as st
import pandas as pd
from db.queries import DBQueries
import plotly.express as px

def show_dashboard():
    st.title("📊 Dashboard de Control")
    db = DBQueries()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Cumplimiento Plan", "85%", "+2%")
    col2.metric("OEE Global", "78%", "-1%")
    col3.metric("Kg Producidos Today", "1,250 Kg")
    col4.metric("Backlog Pendiente", "4,300 Kg")
    
    st.divider()
    st.subheader("Estado de Torsión (T11-T16)")
    # Logic for machine status display...
