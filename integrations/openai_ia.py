# codigo refactorizado version MaxOutput V1.1
from typing import List, Dict, Any, Tuple, Set, Optional
import math
from datetime import datetime, timedelta
from collections import defaultdict
import logging
from dataclasses import dataclass, field
from copy import deepcopy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# CLASES DE DATOS
# ============================================================================

@dataclass
class TorsionMachine:
    machine_id: str
    denier: int
    kgh: float
    husos: int = 1
    
    def __hash__(self):
        return hash(self.machine_id)
    
    def __eq__(self, other):
        if isinstance(other, TorsionMachine):
            return self.machine_id == other.machine_id
        return False

@dataclass
class RewinderConfig:
    denier: int
    kg_per_hour: float
    n_optimo: int

@dataclass
class BacklogItem:
    ref: str
    description: str
    denier: int
    kg_pending: float
    priority: int = 0
    kg_initial: float = field(default=0.0)
    completed: bool = False
    
    def __post_init__(self):
        if self.kg_initial == 0.0:
            self.kg_initial = self.kg_pending

# ============================================================================
# OPTIMIZADOR PRINCIPAL: MAX OUTPUT
# ============================================================================

class MaxOutputOptimizer:
    """
    Implementa la estrategia 'Max Output' priorizando ocupación de rewinders (28 puestos)
    y continuidad de deniers.
    """
    def __init__(self, 
                 torsion_machines: List[TorsionMachine],
                 rewinder_configs: Dict[int, RewinderConfig],
                 total_rewinders: int = 28,
                 shift_hours: float = 8.0,
                 min_occupation_percent: float = 0.92): # ~26 puestos
        
        self.torsion_machines = torsion_machines
        self.rewinder_configs = rewinder_configs
        self.total_rewinders = total_rewinders
        self.shift_hours = shift_hours
        self.min_occupation_percent = min_occupation_percent
        self.min_rewinders_alert = 26
        
        # Mapa de máquinas por denier
        self.machines_by_denier = defaultdict(list)
        for m in torsion_machines:
            self.machines_by_denier[m.denier].append(m)

        # Mapa invertido: Deniers que soporta cada máquina
        self.machine_compatibility = defaultdict(set)
        for m in torsion_machines:
            self.machine_compatibility[m.machine_id].add(m.denier)

    def calculate_efficiency(self, denier: int) -> float:
        """
        Calcula ratio: Capacidad Total Torsión (kg/h) / Demanda Rewinder unitaria (kg/h)
        Indica cuántos puestos de rewinder puede alimentar la torsión disponible.
        """
        if denier not in self.rewinder_configs:
            return 0.0
        
        rw_config = self.rewinder_configs[denier]
        machines = self.machines_by_denier.get(denier, [])
        total_torsion_kgh = sum(m.kgh for m in machines)
        
        if rw_config.kg_per_hour <= 0:
            return 0.0
            
        return total_torsion_kgh / rw_config.kg_per_hour

    def calculate_posts_valid(self, denier: int, max_available: int) -> int:
        """
        Calcula el número de puestos válido respetando múltiplos de N óptimo
        o ajustando para maximizar uso si no es exacto.
        Intenta acercarse a max_available sin pasarse, pero respetando reglas.
        """
        if denier not in self.rewinder_configs:
            return 0
            
        n_opt = self.rewinder_configs[denier].n_optimo
        
        # Generar posibles múltiplos de n_opt
        candidates = []
        k = 1
        while True:
            val = k * n_opt
            if val > max_available + n_opt: # Un poco de margen para chequear
                break
            candidates.append(val)
            candidates.append(val + 1) # Tolerancia +1
            candidates.append(val - 1) # Tolerancia -1
            k += 1
            
        # Filtrar válidos <= max_available y > 0
        valid = [c for c in candidates if 0 < c <= max_available]
        
        if not valid:
            # Si no cabe ni siquiera un grupo (ej: queda 1 puesto y n_opt=4)
            # Retornamos el max_available si es crítico, o 0
            return max_available if max_available > 0 else 0
            
        return max(valid)

    def select_deniers_for_shift(self, 
                               active_items: List[BacklogItem], 
                               previous_deniers: Set[int]) -> List[Dict]:
        """
        Algoritmo greedy para seleccionar deniers que llenen 28 puestos.
        """
        # Agrupar backlog disponible
        deniers_with_backlog = defaultdict(float)
        for item in active_items:
            deniers_with_backlog[item.denier] += item.kg_pending
            
        candidates = []
        for denier, backlog_kg in deniers_with_backlog.items():
            if denier not in self.rewinder_configs:
                continue
                
            efficiency = self.calculate_efficiency(denier)
            is_continuous = denier in previous_deniers
            
            # Prioridad especial T14/18000/12000
            is_high_cap = denier in [12000, 18000]
            
            candidates.append({
                'denier': denier,
                'backlog_kg': backlog_kg,
                'efficiency': efficiency,
                'is_continuous': is_continuous,
                'is_high_cap': is_high_cap,
                'rw_config': self.rewinder_configs[denier]
            })
            
        # ORDENAMIENTO (CRITERIOS DE ÉXITO)
        # 1. Continuidad
        # 2. Alta Capacidad (T14)
        # 3. Eficiencia (capacidad de alimentar rewinders)
        # 4. Backlog
        candidates.sort(key=lambda x: (
            not x['is_continuous'],
            not x['is_high_cap'],
            -x['efficiency'],
            -x['backlog_kg']
        ))
        
        selected = []
        posts_used = 0
        posts_target = self.total_rewinders
        
        for cand in candidates:
            remaining_space = posts_target - posts_used
            if remaining_space <= 0:
                break
                
            # Calcular puestos ideales basados en eficiencia (supply vs demand)
            # posts_sustainable = cand['efficiency'] (teórico)
            
            # Intentar llenar el espacio restante
            posts_to_take = self.calculate_posts_valid(cand['denier'], remaining_space)
            
            if posts_to_take > 0:
                cand['assigned_posts'] = posts_to_take
                selected.append(cand)
                posts_used += posts_to_take
                
        return selected

    def assign_resources(self, 
                        selected_candidates: List[Dict], 
                        active_items: List[BacklogItem]) -> Dict[str, Any]:
        """
        Asigna máquinas de torsión y calcula balances finales.
        """
        assignments = []
        used_machines = set()
        total_rewinders = 0
        total_kg_planned = 0
        
        # Asignación de Torsión (Greedy por orden de selección)
        for cand in selected_candidates:
            denier = cand['denier']
            posts = cand['assigned_posts']
            rw_config = cand['rw_config']
            
            # Buscar máquinas disponibles para este denier
            my_machines = []
            potential_machines = self.machines_by_denier.get(denier, [])
            
            # Ordenar máquinas por capacidad desc
            potential_machines.sort(key=lambda m: -m.kgh)
            
            for m in potential_machines:
                if m.machine_id not in used_machines:
                    used_machines.add(m.machine_id)
                    my_machines.append(m)
            
            # Calcular capacidades
            torsion_capacity_kgh = sum(m.kgh for m in my_machines)
            rewinder_demand_kgh = posts * rw_config.kg_per_hour
            
            # BALANCEO
            limiting_rate_kgh = min(torsion_capacity_kgh, rewinder_demand_kgh)
            
            if limiting_rate_kgh == 0:
                # Si no hay capacidad, pero tenemos posts asignados, es un problema.
                # Reducimos los posts a 0 para esta asignación real
                consumed = 0
                operators = 0
                actual_posts = 0
            else:
                actual_posts = posts
                # Calcular kilos reales del turno
                kg_production = limiting_rate_kgh * self.shift_hours
                
                # Consumir backlog
                denier_items = [i for i in active_items if i.denier == denier and i.kg_pending > 0]
                consumed = 0
                details = []
                
                for item in denier_items:
                    if consumed >= kg_production:
                        break
                    can_take = min(item.kg_pending, kg_production - consumed)
                    item.kg_pending -= can_take
                    consumed += can_take
                    details.append(item.ref)
                    if item.kg_pending <= 0.1:
                        item.completed = True
                
                operators = math.ceil(actual_posts / rw_config.n_optimo)
                
                assignments.append({
                    'denier': denier,
                    'posts': actual_posts,
                    'operators': operators,
                    'kg_planned': round(consumed, 1),
                    'torsion_machines': [m.machine_id for m in my_machines],
                    'refs': list(set(details)),
                    'balance_ratio': round(rewinder_demand_kgh / torsion_capacity_kgh, 2) if torsion_capacity_kgh > 0 else 0
                })
                
                total_rewinders += actual_posts
                total_kg_planned += consumed
            
        return {
            'assignments': assignments,
            'total_rewinders': total_rewinders,
            'total_kg': total_kg_planned,
            'alert': total_rewinders < self.min_rewinders_alert
        }


# ============================================================================
# FUNCIONES DE INTERFAZ
# ============================================================================

def generate_max_output_schedule(
    backlog_summary: Dict[str, Any],
    torsion_capacities: Dict[str, Any],
    rewinder_capacities: Dict[str, Any],
    max_days: int = 60
) -> Dict[str, Any]:
    
    # 1. Parsear Inputs
    torsion_machines = []
    for d_str, data in torsion_capacities.items():
        try:
            d = int(d_str)
            for m in data.get('machines', []):
                torsion_machines.append(TorsionMachine(
                    machine_id=m['machine_id'],
                    denier=d,
                    kgh=float(m['kgh']),
                    husos=int(m.get('husos', 1))
                ))
        except: pass
        
    rewinder_configs = {}
    for d_str, data in rewinder_capacities.items():
        try:
            d = int(d_str)
            rewinder_configs[d] = RewinderConfig(
                denier=d,
                kg_per_hour=float(data['kg_per_hour']),
                n_optimo=int(data.get('n_optimo', 1))
            )
        except: pass
        
    backlog_items = []
    for code, data in backlog_summary.items():
        backlog_items.append(BacklogItem(
            ref=code,
            description=data.get('description', ''),
            denier=int(data['denier']),
            kg_pending=float(data['kg_total']),
            priority=int(data.get('priority', 0))
        ))
        
    # 2. Inicializar Optimizador
    optimizer = MaxOutputOptimizer(torsion_machines, rewinder_configs)
    
    # 3. Simulación Dia a Dia
    
    # IMPORTANTE: Clonamos los items al inicio de cada "simulación" si quisiéramos inmutabilidad,
    # pero aquí modificamos el estado para progresar.
    
    schedule = []
    current_date = datetime.now()
    previous_deniers = set()
    total_kg = 0
    days_count = 0
    
    # Alert memory
    low_occ_alerts = []

    for day in range(max_days):
        active_backlog = [i for i in backlog_items if i.kg_pending > 1.0]
        if not active_backlog:
            break
            
        days_count += 1
        day_date = current_date + timedelta(days=day)
        date_str = day_date.strftime("%Y-%m-%d")
        
        # 3 Turnos por día
        turnos_dia = []
        
        for shift_idx, shift_name in enumerate(['A', 'B', 'C']):
            # Seleccionar
            candidates = optimizer.select_deniers_for_shift(active_backlog, previous_deniers)
            
            # Asignar
            result = optimizer.assign_resources(candidates, active_backlog)
            
            # Registrar
            total_ops = sum(a['operators'] for a in result['assignments'])
            
            # Alerta
            if result['alert']:
                low_occ_alerts.append(f"{date_str} T-{shift_name}: {result['total_rewinders']} puestos")
                
            turnos_dia.append({
                'fecha': date_str,
                'turno': shift_name,
                'hora_inicio': f"{6 + shift_idx*8:02d}:00",
                'kg_salida': round(result['total_kg'], 1),
                'num_rewinders': result['total_rewinders'],
                'operarios': total_ops,
                'alerta_ocupacion': result['alert'],
                'detalles': [
                    f"D{a['denier']} ({a['posts']} posts)" for a in result['assignments']
                ]
            })
            
            total_kg += result['total_kg']
            previous_deniers = set(c['denier'] for c in candidates)
            
            # Recalcular backlog activo para siguiente turno
            active_backlog = [i for i in backlog_items if i.kg_pending > 1.0]

        schedule.extend(turnos_dia)
        
        if not active_backlog:
            break

    # 4. Construir Respuesta Final
    # Estructura ajustada para ser compatible pero más simple
    
    resumen = {
        "total_kg_programados": round(total_kg, 1),
        "total_dias": days_count,
        "promedio_ocupacion": round(
            sum(t['num_rewinders'] for t in schedule) / max(len(schedule), 1), 1
        ),
        "alertas": "; ".join(low_occ_alerts[:5]) + ("..." if len(low_occ_alerts)>5 else "")
    }
    
    return {
        "resumen_programa": resumen,
        "tabla_turnos": schedule,
        # Mantener legacy wrappers si es necesario
        "scenario": {
            "resumen_global": {
                "kg_producidos": resumen['total_kg_programados'],
                "dias_programados": days_count,
                "eficiencia_promedio": resumen['promedio_ocupacion'],
                "alerta_capacidad": resumen['alertas']
            },
            "cronograma_diario": [] # Frontend debe usar tabla_turnos
        }
    }

# ============================================================================
# WRAPPERS DE COMPATIBILIDAD
# ============================================================================

def run_smart_production(
    db_backlog: Dict[str, Any],
    db_torsion_config: Dict[str, Any],
    db_rewinder_config: Dict[str, Any],
    app_config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Compatible entry point"""
    
    # Adaptar db_backlog al formato summary
    backlog_summary = {}
    items = db_backlog.get('items', [])
    for i in items:
        # Manejar claves variadas de la BD
        code = i.get('code') or i.get('referencia')
        kg = i.get('kg_pendientes') or i.get('cantidad') or 0
        denier = i.get('denier') or i.get('titulo') or 0
        
        if code and kg > 0:
            backlog_summary[code] = {
                'kg_total': kg,
                'description': i.get('descripcion', ''),
                'denier': denier,
                'priority': i.get('prioridad', 0)
            }
            
    return generate_max_output_schedule(
        backlog_summary,
        db_torsion_config,
        db_rewinder_config,
        max_days=60
    )

def generate_production_schedule(**kwargs):
    """Legacy wrapper"""
    backlog = kwargs.get('backlog_summary', {})
    
    # Si viene vacio, intentar construirlo de orders si existen (caso raro)
    if not backlog and 'orders' in kwargs:
         # Logica de fallback omitida por simplicidad, se asume backlog_summary construido en app.py
         pass

    return generate_max_output_schedule(
        backlog_summary=backlog,
        torsion_capacities=kwargs.get('torsion_capacities', {}),
        rewinder_capacities=kwargs.get('rewinder_capacities', {}),
        max_days=60
    )

def get_ai_optimization_scenario(orders, reports):
    """
    Función helper que obtiene datos de la BD y corre la optimización.
    """
    try:
        from db.queries import DBQueries
        db = DBQueries()
        
        # Obtener configuraciones
        machines = db.get_machines_torsion() or []
        m_configs = db.get_machine_denier_configs() or []
        r_configs = db.get_rewinder_denier_configs() or []
        
        # Construir torsion_capacities
        # Estructura: "2000": {"machines": [{"machine_id": "T11", "kgh": 26, "husos": 1}]}
        torsion_capacities = defaultdict(lambda: {"machines": []})
        
        # Mapa de configs por ID+Denier para acceso rápido
        # Asumimos que get_machine_denier_configs devuelve [{machine_id, denier, rpm, torsiones, husos, efficencia...}, ...]
        # Y necesitamos KGH.
        # Si no esta KGH, lo estimamos o fallamos.
        
        for cfg in m_configs:
            d = str(cfg.get('denier'))
            # Intentar obtener KGH calculado o calcularlo
            kgh = float(cfg.get('kgh', 0))
            if kgh <= 0:
                # Intento de cálculo basico si faltan datos (Formula: RPM / Torsiones * Denier ...)
                # Por ahora usamos 0 si no hay dato
                pass
            
            if kgh > 0:
                torsion_capacities[d]["machines"].append({
                    "machine_id": cfg.get('machine_id'),
                    "kgh": kgh,
                    "husos": int(cfg.get('husos', 1))
                })

        # Construir rewinder_capacities
        rewinder_capacities = {}
        for rc in r_configs:
            d = str(rc.get('denier'))
            rewinder_capacities[d] = {
                "kg_per_hour": float(rc.get('mp', 0) or rc.get('mp_kgh', 0)),
                "n_optimo": int(rc.get('tm', 1) or rc.get('tm_optimo', 1))
            }
            
        # Construir Backlog Summary
        backlog_summary = {}
        for o in orders:
             code = o.get('id_cabuya') or o.get('code')
             if code:
                 backlog_summary[code] = {
                     'kg_total': float(o.get('kg_pendientes', 0)),
                     'description': o.get('descripcion', ''),
                     'denier': int(o.get('denier_obj', {}).get('name', '0') or 0),
                     'priority': 0
                 }

        return generate_max_output_schedule(
            backlog_summary,
            dict(torsion_capacities),
            rewinder_capacities
        )
        
    except Exception as e:
        logger.error(f"Error in get_ai_optimization_scenario: {e}")
        return {"error": str(e)}
