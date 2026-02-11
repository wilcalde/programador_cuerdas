import os
import secrets
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from db.queries import DBQueries
from logic.formulas import get_n_optimo_rew, get_kgh_torsion, get_mezcla_torsion, get_mezcla_torsion_v2
from typing import List, Dict, Any, Tuple
from datetime import datetime
import json

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    code = getattr(e, 'code', 500)
    # Ensure code is an integer for comparison
    if isinstance(code, int) and code == 404:
        return render_template('generic.html', 
                             title="Página no encontrada",
                             message="Lo sentimos, la página que buscas no existe."), 404
    
    # Generic error handler
    error_msg = str(e)
    return render_template('generic.html', 
                         title="Error Interno",
                         message=f"Ha ocurrido un error inesperado: {error_msg}"), 500

@app.route('/')
def index():
    return render_template('index.html', active_page='home', title='Inicio')

@app.route('/backlog')
def backlog():
    db = DBQueries()
    orders = db.get_orders()
    deniers = db.get_deniers()
    
    # Sorting logic for deniers (natural sort for 6000 expo, etc)
    def denier_sort_key(d):
        name = d['name']
        try:
            # Try to get the leading number
            num_part = ""
            for char in name:
                if char.isdigit():
                    num_part += char
                else:
                    break
            return (float(num_part) if num_part else 0.0, name)
        except:
            return (0.0, name)
            
    deniers.sort(key=denier_sort_key)
    
    pending_requirements = db.get_pending_requirements()
    inventarios_cabuyas = db.get_inventarios_cabuyas()
    
    return render_template('backlog.html', 
                         active_page='backlog', 
                         title='Backlog', 
                         orders=orders, 
                         deniers=deniers, 
                         pending_requirements=pending_requirements,
                         inventarios_cabuyas=inventarios_cabuyas)

@app.route('/backlog/add', methods=['POST'])
def add_backlog():
    db = DBQueries()
    denier_id = request.form.get('denier_id')
    kg = float(request.form.get('kg', 0))
    required_date = request.form.get('required_date')
    cabuya_codigo = request.form.get('cabuya_codigo')
    
    if denier_id and kg > 0:
        db.add_order(denier_id, kg, required_date, cabuya_codigo)
        flash("Pedido añadido correctamente", "success")
    else:
        flash("Datos inválidos para el pedido", "error")
        
    return redirect(url_for('backlog'))

@app.route('/backlog/edit', methods=['POST'])
def edit_backlog():
    db = DBQueries()
    order_id = request.form.get('order_id')
    denier_id = request.form.get('denier_id')
    kg = float(request.form.get('kg', 0))
    required_date = request.form.get('required_date')
    cabuya_codigo = request.form.get('cabuya_codigo')
    
    if order_id and denier_id and kg > 0:
        db.update_order(order_id, denier_id, kg, required_date, cabuya_codigo)
        flash("Pedido actualizado correctamente", "success")
    else:
        flash("Datos inválidos para la actualización", "error")
        
    return redirect(url_for('backlog'))

@app.route('/backlog/delete/<order_id>', methods=['POST'])
def delete_backlog(order_id):
    db = DBQueries()
    db.delete_order(order_id)
    flash("Pedido eliminado", "success")
    return redirect(url_for('backlog'))

@app.route('/supervisor')
def supervisor():
    return render_template('supervisor.html', active_page='supervisor', title='Supervisor')

@app.route('/planning')
def planning():
    db = DBQueries()
    deniers = db.get_deniers()
    
    # Sorting logic for deniers
    def denier_sort_key(d):
        name = d['name']
        try:
            num_part = ""
            for char in name:
                if char.isdigit():
                    num_part += char
                else: break
            return (float(num_part) if num_part else 0.0, name)
        except: return (0.0, name)
    deniers.sort(key=denier_sort_key)
    
    return render_template('planning.html', active_page='planning', title='Planeación', deniers=deniers)

@app.route('/api/optimize', methods=['POST'])
def optimize():
    data = request.json
    selected_deniers = data.get('deniers', [])
    orders = data.get('orders', {})
    
    # Conversion of kg to float
    for d_id in orders:
        orders[d_id] = float(orders[d_id])
        
    # Example logic for optimization result
    result = {
        "status": "success",
        "plan": [],
        "summary": {}
    }
    
    # Calculation per denier
    for d_id in selected_deniers:
        kg_total = orders.get(d_id, 0)
        if kg_total > 0:
            # Formulas application
            n_optimo = get_n_optimo_rew(kg_total, 120) # example with 120h
            kgh = get_kgh_torsion(float(d_id.replace('d','')), 2000) # dummy denier extract
            
            result["plan"].append({
                "denier": d_id,
                "kg": kg_total,
                "n_machines": n_optimo,
                "kgh": kgh
            })
            
    return jsonify(result)

@app.route('/config')
def config():
    db = DBQueries()
    cabuyas = db.get_inventarios_cabuyas()
    return render_template('config.html', active_page='config', title='Configuración', cabuyas=cabuyas)

@app.route('/config/cabuyas/update', methods=['POST'])
def update_cabuya_config():
    db = DBQueries()
    codigo = request.form.get('codigo')
    inventory_security = request.form.get('inventory_security')
    
    if codigo and inventory_security is not None:
        try:
            db.supabase.table("inventarios_cabuyas").update({
                "inventario_seguridad": float(inventory_security)
            }).eq("codigo", codigo).execute()
            flash(f"Configuración actualizada para {codigo}", "success")
        except Exception as e:
            flash(f"Error: {str(e)}", "error")
        
    return redirect(url_for('config'))

@app.route('/config/cabuyas/priority', methods=['POST'])
def update_cabuya_priority():
    db = DBQueries()
    data = request.json
    codigo = data.get('codigo')
    prioridad = data.get('prioridad')
    
    if codigo is not None:
        try:
            db.update_cabuya_priority(codigo, bool(prioridad))
            return jsonify(success=True)
        except Exception as e:
            return jsonify(success=False, error=str(e)), 500
    return jsonify(success=False, error="Missing data"), 400

@app.route('/reports')
def reports():
    return render_template('reports.html', active_page='reports', title='Reportes')

if __name__ == '__main__':
    app.run(debug=True)
