from openai import OpenAI
import os
import json
from typing import List, Dict, Any

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

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generate a deterministic operational production schedule in Python.
    No more AI-based math. GPT-4o-mini only used for scenario commentary.
    """
    
    # 1. Prepare Backlog List (Deterministic SPT order)
    backlog_list = []
    if backlog_summary:
        for d_name, data in backlog_summary.items():
            backlog_list.append({
                "ref": d_name,
                "kg_total": data.get('kg_total', 0)
            })
    else:
        # SPT Fallback: Shortest Processing Time roughly approximated by denier/kg
        temp_backlog = {}
        for o in orders:
            d_name = o.get('deniers', {}).get('name', 'Unknown')
            temp_backlog[d_name] = temp_backlog.get(d_name, 0) + (o.get('total_kg', 0) - (o.get('produced_kg', 0) or 0))
        for d_name, kg in temp_backlog.items():
            if kg > 0:
                backlog_list.append({"ref": d_name, "kg_total": kg})
    
    # Sort by Ref name as simple priority for now, or maintain provided order
    # For now, we follow the order as they came or simple ascending Ref
    backlog_list.sort(key=lambda x: str(x['ref']))

    # 2. Master Data Lookup
    # Note: rewinder_capacities is already keyed by denier name from app.py
    
    # 3. Calendar Setup
    default_start_date = datetime.now() + timedelta(days=1)
    current_time = default_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if shifts and len(shifts) > 0:
        # Use first available shift date as start
        try:
            first_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
            current_time = first_date.replace(hour=0, minute=0, second=0, microsecond=0)
        except:
            pass

    # 4. Engine de Mezcla Dinámica (Multitasking)
    cronograma_final = []
    tabla_finalizacion = {}
    
    # Preparar estado del backlog y capacidades dinámicas
    backlog_status = []
    for item in backlog_list:
        ref_name = str(item['ref'])
        cap_rw = rewinder_capacities.get(ref_name, {})
        
        # Calcular Capacidad de Suministro (Torsión)
        compatibles = [m_id for m_id in ["T11", "T12", "T14", "T15", "T16"] if (m_id, ref_name) in {(m['machine_id'], denier): m['kgh'] for denier, data in torsion_capacities.items() for m in data.get('machines', [])}]
        if not compatibles: compatibles = ["T11", "T12", "T14", "T15", "T16"]
        
        suministro_kgh = 0
        for m_id in compatibles:
            # Buscando velocidad específica o fallback 50.0
            found_vel = False
            for denier, d_data in torsion_capacities.items():
                if denier == ref_name:
                    for m in d_data.get('machines', []):
                        if m['machine_id'] == m_id:
                            suministro_kgh += m['kgh']
                            found_vel = True
            if not found_vel: suministro_kgh += 50.0 # Fallback universal
        
        tasa_unit_rw = cap_rw.get('kg_per_hour', 0)
        if tasa_unit_rw <= 0: tasa_unit_rw = 1.0 # Evitar división por cero
        
        puestos_max_supply = math.floor(suministro_kgh / tasa_unit_rw)
        
        backlog_status.append({
            "ref": ref_name,
            "kg_pendientes": item['kg_total'],
            "kg_total_inicial": item['kg_total'],
            "kgh_unitario": tasa_unit_rw,
            "n_optimo": cap_rw.get('n_optimo', 1),
            "puestos_max_supply": min(28, max(0, puestos_max_supply))
        })

    def get_eligible_refs():
        return [b for b in backlog_status if b['kg_pendientes'] > 0.01]

    # Calcular Techo de la Planta (Suma de capacidades reales de T11-T16)
    # Buscamos la capacidad máxima "promedio" para tener un techo estable
    total_plant_kgh = 0
    machine_base_kgh = {} # {m_id: base_kgh}
    for m_id in ["T11", "T12", "T14", "T15", "T16"]:
        max_m = 0
        for denier, d_data in torsion_capacities.items():
            for m in d_data.get('machines', []):
                if m['machine_id'] == m_id: max_m = max(max_m, m['kgh'])
        if max_m == 0: max_m = 50.0 
        machine_base_kgh[m_id] = max_m
        total_plant_kgh += max_m

    # Procesamiento dia a dia
    while any(b['kg_pendientes'] > 0.01 for b in backlog_status):
        fecha_str = current_time.strftime("%Y-%m-%d")
        
        dia_entry = {
            "fecha": fecha_str,
            "turnos_asignados": [],
            "requerimiento_abastecimiento": {"kg_totales_demandados": 0, "horas_produccion_conjunta": 0, "detalle_torcedoras": []},
            "metricas_dia": {"operarios_maximos": 0, "puestos_activos": 0}
        }
        
        horas_disponibles_dia = 24.0
        while horas_disponibles_dia > 0.01:
            eligibles = get_eligible_refs()
            if not eligibles: break
            
            # REGLA ORO: Siempre ocupar 28 puestos si hay backlog
            mezcla_slot = []
            
            # Referencia principal (A) según prioridad SPT
            ref_A = eligibles[0]
            Tasa_A = ref_A['kgh_unitario']
            
            # Intentar primero con la más prioritaria
            consumo_A_28 = 28 * Tasa_A
            
            if consumo_A_28 <= total_plant_kgh:
                # Caso A: La referencia A puede correr sola en los 28 puestos sin saturar Torsión
                mezcla_slot.append({"ref_obj": ref_A, "puestos": 28})
            else:
                # Caso B: Balance por Mezcla. Mezclar con una referencia más ligera (B)
                # Buscamos la referencia disponible con la tasa más baja para maximizar capacidad de mezcla
                eligibles_sorted_tasa = sorted(eligibles, key=lambda x: x['kgh_unitario'])
                ref_B = eligibles_sorted_tasa[0]
                
                if ref_B['ref'] == ref_A['ref'] and len(eligibles) > 1:
                    # Si la más ligera es la misma A, buscamos la siguiente más ligera
                    ref_B = eligibles_sorted_tasa[1]
                
                Tasa_B = ref_B['kgh_unitario']
                
                if Tasa_A > Tasa_B:
                    # Resolver: Pa + Pb = 28  Y  Pa*Tasa_A + Pb*Tasa_B <= GSC
                    # Pa <= (GSC - 28*Tasa_B) / (Tasa_A - Tasa_B)
                    Pa_ideal = (total_plant_kgh - 28 * Tasa_B) / (Tasa_A - Tasa_B)
                    Pa = max(0, min(27, math.floor(Pa_ideal))) # Al menos 1 puesto para B si mezclamos
                    Pb = 28 - Pa
                    
                    mezcla_slot.append({"ref_obj": ref_A, "puestos": int(Pa)})
                    mezcla_slot.append({"ref_obj": ref_B, "puestos": int(Pb)})
                else:
                    # En el raro caso que todas las tasas sean iguales y superen GSC
                    # (Significa que la planta físicamente no puede correr 28 puestos de nada)
                    # Aun así, por regla de "Prohibición de Ociosidad", asignamos 28.
                    # El balance de masa posterior mostrará la "FALTA" de suministro.
                    mezcla_slot.append({"ref_obj": ref_A, "puestos": 28})

            if not mezcla_slot: break
            
            # Calcular duración del slot (Shortest Task first)
            duracion_slot = horas_disponibles_dia
            for item in mezcla_slot:
                b = item['ref_obj']
                vel_slot = b['kgh_unitario'] * item['puestos']
                time_to_empty = b['kg_pendientes'] / vel_slot if vel_slot > 0 else 999
                duracion_slot = min(duracion_slot, time_to_empty)
            
            # Registrar actividad del slot
            inicio_s = (24.0 - horas_disponibles_dia)
            fin_s = inicio_s + duracion_slot
            
            def fmt_h(val):
                h = int(val)
                m = int((val - h) * 60)
                return f"{h:02d}:{m:02d}"

            ops_totales_slot = 0
            for item in mezcla_slot:
                b = item['ref_obj']
                kg_prod = b['kgh_unitario'] * item['puestos'] * duracion_slot
                ops_ref = math.ceil(item['puestos'] / b['n_optimo'])
                
                dia_entry["turnos_asignados"].append({
                    "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                    "referencia": b['ref'],
                    "hora_inicio": fmt_h(inicio_s),
                    "hora_fin": "24:00" if fin_s > 23.98 else fmt_h(fin_s),
                    "puestos_utilizados": item['puestos'],
                    "operarios_calculados": ops_ref,
                    "kg_producidos": round(kg_prod, 2)
                })
                
                b['kg_pendientes'] -= kg_prod
                if b['kg_pendientes'] <= 0.01:
                    tabla_finalizacion[b['ref']] = current_time.replace(hour=0, minute=0) + timedelta(hours=fin_s)
                
                ops_totales_slot += ops_ref


            dia_entry["metricas_dia"]["operarios_maximos"] = max(dia_entry["metricas_dia"]["operarios_maximos"], ops_totales_slot)
            puestos_activos_slot = sum(item['puestos'] for item in mezcla_slot)
            dia_entry["metricas_dia"]["puestos_activos"] = max(dia_entry["metricas_dia"]["puestos_activos"], puestos_activos_slot)
            horas_disponibles_dia -= duracion_slot

        cronograma_final.append(dia_entry)
        current_time += timedelta(days=1)

    # 5. Sincronización JIT de Torcedoras (Mismo día, misma mezcla)
    kgh_lookup_fast = {} # Pre-procesar para velocidad
    for denier, d_data in torsion_capacities.items():
        for m in d_data.get('machines', []):
            kgh_lookup_fast[(m['machine_id'], denier)] = m['kgh']

    for dia in cronograma_final:
        # Sumar demanda del día por referencia
        demanda_dia = {}
        for t in dia["turnos_asignados"]:
            r = t["referencia"]
            demanda_dia[r] = demanda_dia.get(r, 0) + t["kg_producidos"]
        
        if not demanda_dia: continue
        
        detalle_torsion = []
        kg_dia_torsion = 0
        h_max_torsion = 0
        maquinas_usadas = set()
        
        # Aporte de suministro por referencia para el balance posterior
        suministro_por_referencia = {}
        
        for ref, kg_objetivo in demanda_dia.items():
            compatibles = sorted([m_id for m_id in ["T11", "T12", "T14", "T15", "T16"] if (m_id, ref) in kgh_lookup_fast or True]) # Fallback always true
            
            kg_pending = kg_objetivo
            for m_id in compatibles:
                if m_id in maquinas_usadas or kg_pending <= 0.1: continue
                
                vel = kgh_lookup_fast.get((m_id, ref), 50.0)
                if vel <= 0: vel = 50.0
                
                h_req = kg_pending / vel
                h_asig = min(24.0, h_req)
                kg_asig = h_asig * vel
                
                detalle_torsion.append({
                    "maquina": m_id,
                    "ref": ref,
                    "horas": round(h_asig, 2),
                    "kg_aportados": round(kg_asig, 2)
                })
                maquinas_usadas.add(m_id)
                kg_pending -= kg_asig
                kg_dia_torsion += kg_asig
                h_max_torsion = max(h_max_torsion, h_asig)
                
                # Acumular para el balance por referencia
                suministro_por_referencia[ref] = suministro_por_referencia.get(ref, 0) + kg_asig
        
        # Calcular consumo total del rebobinado para este día
        consumo_total_rebobinado = sum(demanda_dia.values())
        
        # Generar detalle de balance por referencia
        balance_refs = []
        for ref in demanda_dia.keys():
            kg_dem = demanda_dia.get(ref, 0)
            kg_sup = suministro_por_referencia.get(ref, 0)
            diff = kg_sup - kg_dem
            balance_refs.append({
                "referencia": ref,
                "kg_suministro": round(kg_sup, 2),
                "kg_consumo": round(kg_dem, 2),
                "balance": round(diff, 2),
                "status": "OK" if abs(diff) < 0.5 else ("EXCESO" if diff > 0 else "FALTA")
            })

        # Verificación de balance de masa global
        diferencia_global = abs(kg_dia_torsion - consumo_total_rebobinado)
        balance_perfecto = diferencia_global < 0.5
        
        dia["requerimiento_abastecimiento"] = {
            "kg_totales_demandados": round(kg_dia_torsion, 2),
            "horas_produccion_conjunta": round(h_max_torsion, 2),
            "detalle_torcedoras": detalle_torsion,
            "balance_por_referencia": balance_refs,
            "check_balance": {
                "suministro_total_kg": round(kg_dia_torsion, 2),
                "consumo_total_kg": round(consumo_total_rebobinado, 2),
                "diferencia_kg": round(diferencia_global, 2),
                "balance_perfecto": balance_perfecto
            }
        }

    # 6. Preparar Retorno
    kg_totales_plan = sum(round(d["requerimiento_abastecimiento"]["kg_totales_demandados"], 2) for d in cronograma_final)
    
    tabla_finalizacion_rows = []

    for b in backlog_status:
        f_date = tabla_finalizacion.get(b['ref'], current_time)
        tabla_finalizacion_rows.append({
            "referencia": b['ref'],
            "fecha_finalizacion": f_date.strftime("%Y-%m-%d %H:%M"),
            "puestos_promedio": "Dinámico (Multitasking)",
            "kg_totales": round(b['kg_total_inicial'], 2)
        })

    graph_labels = [d["fecha"] for d in cronograma_final]
    dataset_ops = [d["metricas_dia"]["operarios_maximos"] for d in cronograma_final]
    dataset_kg = [round(d["requerimiento_abastecimiento"]["kg_totales_demandados"], 2) for d in cronograma_final]

    comentario = "Algoritmo Multitasking: 28 puestos ocupados mediante mezcla dinámica de deniers."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        try:
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume la estrategia de mezcla dinámica de deniers para llenar los 28 puestos en una frase técnica muy corta."},
                    {"role": "user", "content": f"Producción concurrente activada. Operarios max: {max(dataset_ops)}. Mezcla JIT completada."}
                ],
                max_tokens=60
            )
            comentario = ai_res.choices[0].message.content
        except: pass

    return {
        "scenario": {
            "resumen_global": {
                "total_dias_programados": len(cronograma_final),
                "fecha_finalizacion_total": (current_time - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
                "kg_totales_plan": round(kg_totales_plan, 2),
                "comentario_estrategia": comentario
            },
            "tabla_finalizacion_referencias": tabla_finalizacion_rows,
            "cronograma_diario": cronograma_final,
            "datos_para_grafica": {
                "labels": graph_labels,
                "dataset_operarios": dataset_ops,
                "dataset_kg_produccion": dataset_kg
            }
        }
    }
