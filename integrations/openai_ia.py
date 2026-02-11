from openai import OpenAI
import os
import json
from typing import List, Dict, Any
import math
from datetime import datetime, timedelta

def get_ai_optimization_scenario(backlog: List[Dict[str, Any]], reports: List[Dict[str, Any]]) -> str:
    """
    Sends plant status to GPT-4o mini to generate an optimization scenario.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "Error: OPENAI_API_KEY no configurada."

    client = OpenAI(api_key=api_key)
    
    context = f"""
    Eres un experto en optimización de plantas industriales. 
    Actúas como consultor para Ciplas.
    Datos actuales:
    - Backlog: {backlog}
    - Novedades hoy: {reports}
    
    Genera un plan de acción breve y directo para maximizar la producción.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Consultor Senior de Procesos Industriales."},
                {"role": "user", "content": context}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error al consultar la IA: {e}"

from datetime import datetime, timedelta
import math

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None, strategy: str = 'kg') -> Dict[str, Any]:
    """
    Motor de Programación Refactorizado: Simulación de Continuidad de Masa.
    Regla de Oro: Suministro Torsión == Consumo Rewinder.
    """
    
    # 1. Mapeo de velocidades por máquina
    kgh_lookup = {}
    all_machines = ["T11", "T12", "T14", "T15", "T16"]
    for denier, d_data in torsion_capacities.items():
        for m in d_data.get('machines', []):
            kgh_lookup[(m['machine_id'], denier)] = m['kgh']

    # 2. Preparar Backlog (A nivel de Referencia/Producto)
    backlog = []
    if not backlog_summary:
        # Fallback to pure denier aggregation if summary is missing (safety)
        # But we primarily expect backlog_summary from app.py
        temp = {}
        for o in orders:
            ref = o.get('deniers', {}).get('name', 'N/A')
            kg = o.get('total_kg', 0) - (o.get('produced_kg', 0) or 0)
            if kg > 0.1:
                temp[ref] = temp.get(ref, 0) + kg
        for ref, kg in temp.items():
            rw_rate = rewinder_capacities.get(ref, {}).get('kg_per_hour', 0)
            n_optimo = rewinder_capacities.get(ref, {}).get('n_optimo', 1)
            backlog.append({
                "code": ref,
                "ref": ref, 
                "denier": ref,
                "kg_pendientes": float(kg), 
                "kg_total_inicial": float(kg),
                "is_priority": False,
                "rw_rate": rw_rate,
                "n_optimo": n_optimo
            })
    else:
        for code, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0.1:
                ref_name = f"{code} ({data.get('description', '')})"
                denier_name = data.get('denier')
                
                # Get Rewinder Rate for this denier
                rw_rate = rewinder_capacities.get(denier_name, {}).get('kg_per_hour', 0)
                n_optimo = rewinder_capacities.get(denier_name, {}).get('n_optimo', 1)

                backlog.append({
                    "code": code,
                    "ref": ref_name,
                    "denier": denier_name,
                    "kg_pendientes": float(data['kg_total']),
                    "kg_total_inicial": float(data['kg_total']),
                    "is_priority": data.get('is_priority', False),
                    "rw_rate": rw_rate,
                    "n_optimo": n_optimo
                })

    # Aplicar Criterio de Ordenamiento (Estrategia de Negocio)
    if strategy == 'priority':
        # 1. Prioridades primero, luego volumen
        backlog.sort(key=lambda x: (not x['is_priority'], -x['kg_pendientes']))
        comentario_adicional = "Priorizando referencias marcadas como PRIORIDAD ⭐."
    else:
        # 1. Maximizar Kg: Priorizar referencias con mayor tasa de salida (Kg/h) en Rewinder
        # Si las tasas son iguales, priorizar volumen
        backlog.sort(key=lambda x: (-x['rw_rate'], -x['kg_pendientes']))
        comentario_adicional = "Maximizando flujo de producción (Kg/h) 📈."

    # 3. Configuración de Calendario y Tiempos
    def fmt_h(val):
        h = int(val)
        m = int(round((val - h) * 60))
        if m >= 60: 
            h += 1
            m = 0
        return f"{h:02d}:{m:02d}"

    default_start = datetime.now() + timedelta(days=1)
    current_date = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
    if shifts and len(shifts) > 0:
        try:
            current_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
        except: pass

    shifts_dict = {s['date']: s['working_hours'] for s in shifts} if shifts else {}

    cronograma_final = []
    tabla_finalizacion = {}
    total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)
    total_kg_inicial = total_kg_backlog

    # 4. Simulación Continua
    while total_kg_backlog > 0.1:
        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        
        dia_entry = {
            "fecha": date_str,
            "turnos_asignados": [],
            "requerimiento_abastecimiento": {
                "kg_totales_demandados": 0,
                "detalle_torcedoras": [],
                "balance_por_referencia": [],
                "check_balance": {"suministro_total_kg": 0, "consumo_total_kg": 0, "diferencia_kg": 0, "balance_perfecto": True}
            },
            "metricas_dia": {"operarios_maximos": 0, "puestos_activos": 0}
        }

        if working_hours <= 0:
            current_date += timedelta(days=1)
            continue

        horas_disponibles = working_hours
        while horas_disponibles > 0.01:
            # MIXING ENGINE: Asignación de Slots Balanceados
            puestos_restantes = total_rewinders
            maquinas_restantes = set(all_machines)
            slot_refs = [] 
            
            # Recoger referencias elegibles para este slot
            # Prioridad: Aquellas que no hayan terminado y quepan en el remanente de puestos (28)
            eligibles = [b for b in backlog if b['kg_pendientes'] > 0.1]
            if not eligibles: break
            
            puestos_en_uso = 0
            consumo_total_slot = 0
            suministro_total_slot = 0
            
            for b_ref in eligibles:
                if puestos_en_uso >= total_rewinders: break
                
                # ¿Cuántas máquinas Rewinder puede operar para esta referencia?
                # Forzamos n_optimo como capacidad técnica
                n_ref = min(b_ref['n_optimo'], total_rewinders - puestos_en_uso)
                if n_ref <= 0: continue
                
                # Calcular consumo real de este grupo de rewinder
                # Capacidad = n_ref * rw_rate (kg/h)
                capacidad_h = n_ref * b_ref['rw_rate']
                
                # ¿Cuánto tiempo dura este grupo antes de agotar el backlog o el suministro?
                # Para simplificar la simulación de flujo continuo: asignamos por hora
                duracion_h = min(horas_disponibles, 1.0) # Segmentos de 1 hora
                kg_consumidos = min(b_ref['kg_pendientes'], capacidad_h * duracion_h)
                
                if kg_consumidos <= 0: continue
                
                # REGLA DE ABASTECIMIENTO: ¿Qué torcedoras pueden proveer este denier?
                # Buscamos máquinas configuradas para este denier y con kg/h asignado
                kg_necesarios = kg_consumidos
                maquinas_asignadas_torsion = []
                
                # Intentamos abastecer esta referencia
                for m_id in all_machines:
                    if kg_necesarios <= 0.001: break
                    kgh_m = kgh_lookup.get((m_id, b_ref['denier']), 0)
                    if kgh_m > 0:
                        # Asignación proporcional de suministro
                        # En la realidad, la torsión provee el 100% de lo que el rewinder consume
                        suministro_h = kgh_m * duracion_h
                        aporte = min(kg_necesarios, suministro_h)
                        
                        maquinas_asignadas_torsion.append({
                            "maquina": m_id,
                            "denier": b_ref['denier'],
                            "referencia": b_ref['ref'],
                            "kg_suministrados": round(aporte, 2),
                            "puestos_equivalentes": round(aporte / (b_ref['rw_rate'] * duracion_h), 1) if b_ref['rw_rate'] > 0 else 0
                        })
                        kg_necesarios -= aporte
                        suministro_total_slot += aporte
                
                # Actualizar Backlog
                b_ref['kg_pendientes'] -= kg_consumidos
                puestos_en_uso += n_ref
                consumo_total_slot += kg_consumidos
                
                # Registrar detalle de finalización
                if b_ref['kg_pendientes'] <= 0.1 and b_ref['ref'] not in tabla_finalizacion:
                    tabla_finalizacion[b_ref['ref']] = f"{date_str} {fmt_h(working_hours - horas_disponibles + duracion_h)}"

                # Guardar en el log del día
                dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].extend(maquinas_asignadas_torsion)
            
            # Actualizar tiempos del día
            step = 1.0 # Una hora
            horas_disponibles -= step
            if not eligibles or puestos_en_uso == 0: break

        # Consolidar Métricas del Día
        dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] = round(sum(m['kg_suministrados'] for m in dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"]), 2)
        dia_entry["metricas_dia"]["puestos_activos"] = round(puestos_en_uso)
        dia_entry["metricas_dia"]["operarios_maximos"] = math.ceil(puestos_en_uso / 12) if puestos_en_uso > 0 else 0
        
        cronograma_final.append(dia_entry)
        total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)
        current_date += timedelta(days=1)
        
        # Guardrail
        if len(cronograma_final) > 45: break

    # 5. Formatear Respuesta Final
    resumen_final = []
    for ref, fecha in tabla_finalizacion.items():
        resumen_final.append({"referencia": ref, "fecha_entrega": fecha})

    return {
        "resumen": f"Planificación Completada: {comentario_adicional} {total_kg_inicial:,.1f} kg totales.",
        "finalizaciones": resumen_final,
        "detalles_diarios": cronograma_final,
        "estadisticas": {
            "total_kg": round(total_kg_inicial, 2),
            "dias_estimados": len(cronograma_final),
            "fecha_fin": cronograma_final[-1]['fecha'] if cronograma_final else "N/A"
        }
    }
