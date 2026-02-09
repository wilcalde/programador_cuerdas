from openai import OpenAI
import os
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
import math

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

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generate a deterministic operational production schedule in Python.
    Uses a deterministic mathematical algorithm to mix deniers and ensure zero Ghost Kilograms.
    Implements Global Supply Capacity (GSC) constraint with linear equation solving.
    Includes explicit check_balance field for mass balance verification.
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
        # SPT Fallback
        temp_backlog = {}
        for o in orders:
            d_name = o.get('deniers', {}).get('name', 'Unknown')
            temp_backlog[d_name] = temp_backlog.get(d_name, 0) + (o.get('total_kg', 0) - (o.get('produced_kg', 0) or 0))
        for d_name, kg in temp_backlog.items():
            if kg > 0:
                backlog_list.append({"ref": d_name, "kg_total": kg})
    
    backlog_list.sort(key=lambda x: str(x['ref']))

    # 3. Calendar Setup
    default_start_date = datetime.now() + timedelta(days=1)
    current_time = default_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if shifts and len(shifts) > 0:
        try:
            first_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
            current_time = first_date.replace(hour=0, minute=0, second=0, microsecond=0)
        except:
            pass

    # 4. Engine de Mezcla Determinística (Mathematical Mixing Algorithm)
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

    # Calcular Techo de la Planta (GSC - Global Supply Capacity)
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
            
            # ALGORITMO DETERMINISTA DE MEZCLA DE DENIERS (Zero Ghost Kilos)
            # Restricción: SUM(Puestos_i * Tasa_i) <= GSC (Global Supply Capacity)
            # Objetivo: SUM(Puestos_i) = 28 (o máximo posible)
            
            mezcla_slot = []
            
            # Ordenar referencias por prioridad SPT (ya están en orden en eligibles)
            ref_A = eligibles[0]
            
            # Caso 1: Si una sola referencia puede llenar los 28 puestos sin exceder GSC
            consumo_A_solo = 28 * ref_A['kgh_unitario']
            
            if consumo_A_solo <= total_plant_kgh:
                # Caso simple: una sola referencia
                puestos_A = min(28, ref_A['puestos_max_supply'])
                mezcla_slot.append({"ref_obj": ref_A, "puestos": puestos_A})
            else:
                # Caso 2: Necesitamos mezclar con una referencia de menor consumo
                # Encontrar la referencia con menor consumo (menor kgh_unitario)
                ref_B = None
                for candidate in eligibles[1:]:
                    if candidate['kgh_unitario'] < ref_A['kgh_unitario']:
                        ref_B = candidate
                        break
                
                if ref_B is None and len(eligibles) > 1:
                    # Si no hay una más ligera, usar la siguiente disponible
                    ref_B = eligibles[1]
                
                if ref_B:
                    # Resolver ecuación lineal: Pa + Pb = 28, (Pa * Tasa_A) + (Pb * Tasa_B) <= GSC
                    # Pa = (GSC - 28*Tasa_B) / (Tasa_A - Tasa_B)
                    Tasa_A = ref_A['kgh_unitario']
                    Tasa_B = ref_B['kgh_unitario']
                    
                    if abs(Tasa_A - Tasa_B) > 0.01:  # Evitar división por cero
                        # Intentar llenar completamente los 28 puestos respetando GSC
                        Pa_ideal = (total_plant_kgh - 28 * Tasa_B) / (Tasa_A - Tasa_B)
                        
                        # Limitar por las restricciones de suministro individual
                        Pa = max(0, min(28, ref_A['puestos_max_supply'], math.floor(Pa_ideal)))
                        Pb = 28 - Pa
                        
                        # Verificar que Pb no exceda su propio límite de suministro
                        if Pb > ref_B['puestos_max_supply']:
                            Pb = ref_B['puestos_max_supply']
                            Pa = 28 - Pb
                            # Re-verificar que Pa no exceda su límite
                            if Pa > ref_A['puestos_max_supply']:
                                Pa = ref_A['puestos_max_supply']
                                Pb = min(28 - Pa, ref_B['puestos_max_supply'])
                        
                        # Verificación final: asegurar que no excedemos GSC
                        consumo_total = (Pa * Tasa_A) + (Pb * Tasa_B)
                        if consumo_total > total_plant_kgh:
                            # Reducir proporcionalmente hasta que quepa
                            factor = total_plant_kgh / consumo_total
                            Pa = math.floor(Pa * factor)
                            Pb = math.floor(Pb * factor)
                        
                        if Pa > 0:
                            mezcla_slot.append({"ref_obj": ref_A, "puestos": int(Pa)})
                        if Pb > 0:
                            mezcla_slot.append({"ref_obj": ref_B, "puestos": int(Pb)})
                    else:
                        # Tasas idénticas, dividir equitativamente
                        Pa = min(14, ref_A['puestos_max_supply'])
                        Pb = min(14, ref_B['puestos_max_supply'])
                        mezcla_slot.append({"ref_obj": ref_A, "puestos": Pa})
                        mezcla_slot.append({"ref_obj": ref_B, "puestos": Pb})
                else:
                    # Solo hay una referencia disponible, usar lo máximo posible sin exceder GSC
                    puestos_max_gsc = math.floor(total_plant_kgh / ref_A['kgh_unitario'])
                    puestos_A = min(puestos_max_gsc, ref_A['puestos_max_supply'])
                    if puestos_A > 0:
                        mezcla_slot.append({"ref_obj": ref_A, "puestos": puestos_A})

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

    # 5. Sincronización JIT de Torcedoras (Mismo día, misma mezcla) + Check Balance
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
        
        
        # Calcular consumo total del rebobinado para este día
        consumo_total_rebobinado = sum(demanda_dia.values())
        
        # Verificación de balance de masa
        diferencia = abs(kg_dia_torsion - consumo_total_rebobinado)
        balance_perfecto = diferencia < 0.5  # Tolerancia de 0.5 kg por redondeos
        
        dia["requerimiento_abastecimiento"] = {
            "kg_totales_demandados": round(kg_dia_torsion, 2),
            "horas_produccion_conjunta": round(h_max_torsion, 2),
            "detalle_torcedoras": detalle_torsion,
            "check_balance": {
                "suministro_total_kg": round(kg_dia_torsion, 2),
                "consumo_total_kg": round(consumo_total_rebobinado, 2),
                "diferencia_kg": round(diferencia, 2),
                "balance_perfecto": balance_perfecto
            }
        }

    # 6. Preparar Retorno
    tabla_finalizacion_rows = []
    for b in backlog_status:
        f_date = tabla_finalizacion.get(b['ref'], current_time)
        tabla_finalizacion_rows.append({
            "referencia": b['ref'],
            "fecha_finalizacion": f_date.strftime("%Y-%m-%d %H:%M"),
            "puestos_promedio": "Dinámico (Matemático)",
            "kg_totales": round(b['kg_total_inicial'], 2)
        })

    graph_labels = [d["fecha"] for d in cronograma_final]
    dataset_ops = [d["metricas_dia"]["operarios_maximos"] for d in cronograma_final]
    dataset_kg = [round(d["requerimiento_abastecimiento"]["kg_totales_demandados"], 2) for d in cronograma_final]

    comentario = f"Algoritmo Determinístico: Máx {round(total_plant_kgh, 1)} kg/h. Balance exacto Torsión/Rebobinado."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        try:
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume el algoritmo de mezcla matemática de deniers en una frase técnica muy corta."},
                    {"role": "user", "content": f"GSC={round(total_plant_kgh, 1)}kg/h. Mezcla determinística. Ops max: {max(dataset_ops)}."}
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
