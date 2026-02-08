import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from db.queries import DBQueries
from integrations.openai_ia import generate_production_schedule, get_ai_optimization_scenario
import pandas as pd
import json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ciplas_master_cord_secret")

# Helper to check auth
def is_authenticated():
    return session.get('authenticated', False)

@app.before_request
def check_auth():
    if request.endpoint and 'static' not in request.endpoint and request.endpoint != 'login' and not is_authenticated():
        return redirect(url_for('login'))

@app.route('/')
def dashboard():
    db = DBQueries()
    return render_template('dashboard.html', active_page='dashboard', title='Dashboard')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Simple simulation as per previous app logic
        if email == "admin@ciplas.com" and password == "admin123":
            session['authenticated'] = True
            session['user_email'] = email
            session['theme'] = 'dark'
            return redirect(url_for('dashboard'))
        else:
            flash("Credenciales incorrectas", "error")
            
    return render_template('login.html', title='Inicia Sesión')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/toggle-theme', methods=['POST'])
def toggle_theme():
    current_theme = session.get('theme', 'dark')
    session['theme'] = 'light' if current_theme == 'dark' else 'dark'
    return jsonify(success=True)

@app.route('/backlog')
def backlog():
    db = DBQueries()
    orders = db.get_orders()
    return render_template('backlog.html', active_page='backlog', title='Backlog', orders=orders)

@app.route('/programming')
def programming():
    db = DBQueries()
    sc_data = db.get_all_scheduling_data()
    return render_template('programming.html', active_page='programming', title='Programación', sc_data=sc_data)

@app.route('/config')
def config():
    db = DBQueries()
    return render_template('config.html', active_page='config', title='Configuración')

@app.route('/reports')
def reports():
    return render_template('reports.html', active_page='reports', title='Reportes')

@app.route('/ai')
def ai_consultancy():
    return render_template('ai.html', active_page='ai', title='Consultoría IA')

# Error handler for 404
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
