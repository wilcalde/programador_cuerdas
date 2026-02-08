import streamlit as st
from streamlit_option_menu import option_menu
from views import auth_ui, dashboard_ui, admin_ui, supervisor_ui, ai_consultant_ui, backlog_ui

# Page Config
st.set_page_config(
    page_title="Ciplas AI-Master Cord",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize dark mode state
if 'dark_mode' not in st.session_state:
    st.session_state.dark_mode = True

# Apply theme-specific CSS
if st.session_state.dark_mode:
    # Dark theme
    theme_css = """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600;700&display=swap');
        
        .stApp {
            background-color: #0F172A;
            color: #F8FAFC;
            font-family: 'Montserrat', sans-serif;
        }
        section[data-testid="stSidebar"] {
            background-color: #020617;
        }
        .stButton > button {
            background: linear-gradient(135deg, #38BDF8, #6366F1);
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] {
            color: #38BDF8;
            font-size: 1.8rem;
            font-weight: 700;
        }
        [data-testid="stMetricLabel"] {
            color: #94A3B8;
            text-transform: uppercase;
        }
        h1, h2, h3 {
            color: #38BDF8;
            font-family: 'Montserrat', sans-serif;
            font-weight: 700;
        }
    </style>
    """
else:
    # Light theme
    theme_css = """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600;700&display=swap');
        
        .stApp {
            background-color: #FFFFFF;
            color: #1E293B;
            font-family: 'Montserrat', sans-serif;
        }
        section[data-testid="stSidebar"] {
            background-color: #F1F5F9;
        }
        .stButton > button {
            background: linear-gradient(135deg, #0EA5E9, #3B82F6);
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] {
            color: #0EA5E9;
            font-size: 1.8rem;
            font-weight: 700;
        }
        [data-testid="stMetricLabel"] {
            color: #64748B;
            text-transform: uppercase;
        }
        h1, h2, h3 {
            color: #0EA5E9;
            font-family: 'Montserrat', sans-serif;
            font-weight: 700;
        }
    </style>
    """

st.markdown(theme_css, unsafe_allow_html=True)

def main():
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'dark_mode' not in st.session_state:
        st.session_state.dark_mode = True

    if not st.session_state.authenticated:
        auth_ui.show_login()
    else:
        with st.sidebar:
            st.image("logo ciplas.jpg", width=150)
            
            # Theme toggle
            theme_label = "🌙 Modo Oscuro" if st.session_state.dark_mode else "☀️ Modo Claro"
            if st.button(theme_label, use_container_width=True):
                st.session_state.dark_mode = not st.session_state.dark_mode
                st.rerun()
            
            st.divider()
            selected = option_menu(
                "Menu Principal",
                ["Dashboard", "Backlog", "Programación", "Configuración", "Reportes", "Consultoría IA"],
                icons=["speedometer2", "list-task", "calendar-check", "gear", "clipboard-data", "robot"],
                menu_icon="cast",
                default_index=0,
            )
            
            if st.button("Cerrar Sesión"):
                st.session_state.authenticated = False
                st.rerun()

        if selected == "Dashboard":
            dashboard_ui.show_dashboard()
        elif selected == "Backlog":
            backlog_ui.show_backlog()
        elif selected == "Programación":
            admin_ui.show_programming()
        elif selected == "Configuración":
            admin_ui.show_config()
        elif selected == "Reportes":
            supervisor_ui.show_reports()
        elif selected == "Consultoría IA":
            ai_consultant_ui.show_ai_consultant()

if __name__ == "__main__":
    main()
