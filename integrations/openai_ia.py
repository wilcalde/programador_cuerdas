#codigo version kimi 2.5
from typing import List, Dict, Any, Tuple, Set
import math
from datetime import datetime, timedelta
from collections import defaultdict
import logging
from dataclasses import dataclass, field
from copy import deepcopy
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class TorsionMachine:
    machine_id: str
    denier: int
    kgh: float
    husos: int = 1
    assigned: bool = False
    
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
    client: str = ""
    kg_initial: float = field(default=0.0)
    completed: bool = False
    completion_date: str = None
    
    def __post_init__(self):
        if self.kg_initial == 0.0:
            self.kg_initial = self.kg_pending


# ============================================================================
# MOTOR DE OPTIMIZACIÓN V2 - MÁXIMA OCUPACIÓN
# ============================================================================

class MaxOutputOptimizer:
    """
    Estrategia: 
    1. Ocupar los 28 rewinders SIEMPRE (o máximo posible)
    2. Asignar máquinas de torsión para balancear exactamente esa demanda
    3. Mantener mismas referencias el mayor tiempo posible
    4. Priorizar deniers con mejor ratio (kg/h por rewinder)
    """
    
    def __init__(self, 
                 torsion_machines: List[TorsionMachine],
                 rewinder_configs: Dict[int, RewinderConfig],
                 total_rewinders: int = 28,
                 shift_hours: float = 8.0,
                 strategy: str = 'kg'):
        
        self.total_rewinders = total_rewinders
        self.shift_hours = shift_hours
        self.rewinder_configs = rewinder_configs
        self.strategy = strategy
        
        # Agrupar máquinas por denier
        self.machines_by_denier = defaultdict(list)
        for m in torsion_machines:
            self.machines_by_denier[m.denier].append(m)
        
        # Calcular eficiencia por denier (kg/h por rewinder usado)
        self.denier_efficiency = {}
        for denier, config in rewinder_configs.items():
            torsion_cap = sum(m.kgh for m in self.machines_by_denier.get(denier, []))
            if torsion_cap > 0:
                # Cuántos kg de torsión tengo por cada rewinder que necesito
                max_rewinders_needed = math.ceil(torsion_cap / config.kg_per_hour)
                self.denier_efficiency[denier] = {
                    'torsion_kgh': torsion_cap,
                    'rewinder_rate': config.kg_per_hour,
                    'max_rewinders': min(max_rewinders_needed, total_rewinders),
                    'machines': self.machines_by_denier.get(denier, []),
                    'n_optimo': config.n_optimo
                }
        
        # Reglas de incompatibilidad de deniers
        self.incompatible_deniers = {
            2000: {3000, 4000, 6000, 9000, 12000, 18000},
            3000: {2000, 6000, 12000, 18000},
            4000: {2000, 6000, 12000, 18000},
            6000: {2000, 3000, 4000, 9000, 18000},
            9000: {2000, 6000},
            12000: {2000, 3000, 4000},
            18000: {2000, 3000, 4000, 6000}
        }
        logger.info(f"Optimizer ready: {len(torsion_machines)} machines, "
                   f"{len(rewinder_configs)} denier configs")
    
    def calculate_optimal_rewinders(self, denier: int, target_posts: int = None) -> int:
        """Calcula cuántos rewinders óptimos para un denier"""
        if denier not in self.rewinder_configs:
            return 0
        
        config = self.rewinder_configs[denier]
        eff = self.denier_efficiency.get(denier, {})
        
        if target_posts is None:
            # Usar máximo posible para este denier
            target = eff.get('max_rewinders', config.n_optimo)
        else:
            target = target_posts
        
        # Ajustar a múltiplos válidos de N óptimo
        n_opt = config.n_optimo
        valid = []
        for k in range(1, (self.total_rewinders // max(n_opt - 1, 1)) + 2):
            for p in range(k * n_opt - 1, k * n_opt + 2):  # \u00b11 flexibilidad
                if 1 <= p <= self.total_rewinders:
                    valid.append(p)
        
        valid = sorted(set(valid))
        closest = min(valid, key=lambda x: abs(x - target))
        return closest
    
    def find_best_denier_combination(self, available_posts: int, 
                                    active_items: List[BacklogItem],
                                    current_deniers: Set[int] = None) -> List[Dict]:
        """
        Encuentra la mejor combinación de deniers para llenar exactamente los puestos disponibles.
        Prioriza: 1) Deniers actuales (no cambiar), 2) Mayor eficiencia, 3) Mayor backlog
        """
        current_deniers = current_deniers or set()
        
        # Filtrar deniers con backlog activo
        deniers_with_demand = set(item.denier for item in active_items if item.kg_pending > 0.1)
        
        candidates = []
        for denier in deniers_with_demand:
            if denier not in self.denier_efficiency:
                continue
            
            eff = self.denier_efficiency[denier]
            total_backlog = sum(item.kg_pending for item in active_items if item.denier == denier)
            
            # Calcular posts óptimos para este denier solo
            optimal_posts = self.calculate_optimal_rewinders(denier)
            
            candidates.append({
                'denier': denier,
                'efficiency': eff['torsion_kgh'] / max(optimal_posts * eff['rewinder_rate'], 1),
                'backlog_kg': total_backlog,
                'has_priority': any(item.priority > 0 for item in active_items if item.denier == denier),
                'optimal_posts': optimal_posts,
                'torsion_kgh': eff['torsion_kgh'],
                'machines_available': len(eff['machines']),
                'is_current': denier in current_deniers
            })
        
        if not candidates:
            return []
        
        # Ordenar: 
        # 1. Por estrategia (Prioridad primero si aplica)
        # 2. Por deniers actuales (continuidad)
        # 3. Por mayor volumen de backlog
        # 4. Por eficiencia
        candidates.sort(key=lambda x: (
            -(x['has_priority'] if self.strategy == 'priority' else 0),
            not x['is_current'],  # False (actuales) primero
            -x['backlog_kg'],     # Mayor backlog primero
            -x['efficiency']      # Mayor eficiencia
        ))
        
        # Seleccionar combinación que llene los puestos
        combination = []
        # Aplicar lógica de repartición equitativa (SHARES)
        # Si hay más de 2 deniers candidatos, limitar el espacio proporcionalmente
        num_candidates = len([c for c in candidates if c['backlog_kg'] > 0.1])
        max_posts_per_denier = available_posts
        if num_candidates >= 3:
            max_posts_per_denier = math.floor(available_posts / 2) + 2 # Max ~16 posts per denier
        elif num_candidates == 2:
            max_posts_per_denier = math.floor(available_posts * 0.7) # Max ~19 posts per denier

        posts_used = 0
        
        # Primero intentar mantener deniers actuales si tienen demanda
        for cand in candidates:
            is_incompatible = any(cand['denier'] in self.incompatible_deniers.get(used['denier'], set()) for used in combination)
            if cand['is_current'] and not is_incompatible and posts_used + cand['optimal_posts'] <= available_posts:
                combination.append(cand)
                posts_used += cand['optimal_posts']
        
        # Luego llenar con nuevos deniers de mayor eficiencia
        for cand in candidates:
            if cand not in combination:
                is_incompatible = any(cand['denier'] in self.incompatible_deniers.get(used['denier'], set()) for used in combination)
                if not is_incompatible and posts_used + min(cand['optimal_posts'], max_posts_per_denier) <= available_posts:
                    cand['optimal_posts'] = min(cand['optimal_posts'], max_posts_per_denier)
                    combination.append(cand)
                    posts_used += cand['optimal_posts']
                elif available_posts - posts_used >= 2:  # Espacio para mínimo útil
                    # Ajustar a espacio disponible
                    adjusted_posts = self.calculate_optimal_rewinders(
                        cand['denier'], 
                        available_posts - posts_used
                    )
                    if adjusted_posts > 0:
                        cand['optimal_posts'] = adjusted_posts
                        combination.append(cand)
                        posts_used += adjusted_posts
        
        # Si aún sobran puestos, expandir deniers existentes
        remaining = available_posts - posts_used
        if remaining > 0 and combination:
            # Agregar más rewinders al denier con mayor backlog
            biggest = max(combination, key=lambda x: x['backlog_kg'])
            extra_posts_needed = self.calculate_optimal_rewinders(biggest['denier'], biggest['optimal_posts'] + remaining)
            actual_extra = extra_posts_needed - biggest['optimal_posts']
            if actual_extra <= remaining:
                biggest['optimal_posts'] = extra_posts_needed
                remaining -= actual_extra
        
        return combination
    
    def assign_shift_max_occupation(self, 
                                   active_items: List[BacklogItem],
                                   previous_assignments: List[Dict] = None) -> Dict[str, Any]:
        """
        Asigna un turno ocupando el 100% de rewinders posible.
        """
        previous_assignments = previous_assignments or []
        current_deniers = set(a['denier'] for a in previous_assignments)
        
        # Encontrar combinación óptima para llenar 28 puestos
        combination = self.find_best_denier_combination(
            self.total_rewinders, 
            active_items,
            current_deniers
        )
        
        if not combination:
            return None
        
        assignments = []
        machines_used = set()
        total_kg = 0
        
        for combo in combination:
            denier = combo['denier']
            num_rewinders = combo['optimal_posts']
            
            # Calcular capacidad de producción
            rw_rate = self.rewinder_configs[denier].kg_per_hour
            rw_capacity_kgh = num_rewinders * rw_rate
            
            # Asignar TODAS las máquinas de torsión disponibles para este denier
            available_machines = [
                m for m in self.machines_by_denier.get(denier, [])
                if m.machine_id not in machines_used
            ]
            
            if not available_machines:
                continue
            
            total_torsion_kgh = sum(m.kgh for m in available_machines)
            
            # Calcular kg a producir (limitado por lo que necesita el backlog)
            items_for_denier = [item for item in active_items 
                              if item.denier == denier and item.kg_pending > 0.1]
            max_needed = sum(item.kg_pending for item in items_for_denier)
            
            # Producción = mínimo entre capacidad rewinder y demanda
            production_capacity = rw_capacity_kgh * self.shift_hours
            production_kg = min(production_capacity, max_needed)
            
            # Si no hay demanda suficiente, ajustar rewinders hacia abajo
            if production_kg < production_capacity * 0.5:
                # Reducir rewinders proporcionalmente
                ratio = max_needed / (production_capacity + 0.1)
                adjusted_posts = max(1, math.floor(num_rewinders * ratio))
                num_rewinders = self.calculate_optimal_rewinders(denier, adjusted_posts)
                rw_capacity_kgh = num_rewinders * rw_rate
                production_kg = min(rw_capacity_kgh * self.shift_hours, max_needed)
            
            # Detalle de máquinas
            torsion_details = []
            for m in available_machines:
                machines_used.add(m.machine_id)
                torsion_details.append({
                    'machine_id': m.machine_id,
                    'kgh': m.kgh,
                    'kg_shift': m.kgh * self.shift_hours,
                    'husos': m.husos
                })
            
            # Referencias involucradas
            refs = [item.ref for item in items_for_denier[:5]]  # Top 5
            
            assignment = {
                'denier': denier,
                'references': refs,
                'rewinder_posts': num_rewinders,
                'rewinder_operators': math.ceil(num_rewinders / combo['n_optimo']),
                'rewinder_rate_kgh': rw_capacity_kgh,
                'torsion_machines': [m.machine_id for m in available_machines],
                'torsion_rate_kgh': total_torsion_kgh,
                'torsion_details': torsion_details,
                'balance_ratio': round(rw_capacity_kgh / max(total_torsion_kgh, 1), 2),
                'kg_planned': round(production_kg, 1),
                'efficiency_score': round(combo.get('efficiency', 0), 3)
            }
            
            assignments.append(assignment)
            total_kg += production_kg
        
        # Calcular ocupación real
        total_posts_used = sum(a['rewinder_posts'] for a in assignments)
        
        return {
            'assignments': assignments,
            'machines_used': list(machines_used),
            'posts_used': total_posts_used,
            'posts_remaining': self.total_rewinders - total_posts_used,
            'occupation_rate': round(total_posts_used / self.total_rewinders * 100, 1),
            'total_kg_planned': round(total_kg, 1),
            'deniers_used': list(set(a['denier'] for a in assignments))
        }


# ============================================================================
# GENERADOR DE CRONOGRAMA CON CONTINUIDAD
# ============================================================================

def generate_optimized_schedule(
    backlog_summary: Dict[str, Any],
    torsion_capacities_db: Dict[str, Any],
    rewinder_capacities_db: Dict[str, Any],
    total_rewinders: int = 28,
    shift_hours: float = 8.0,
    shifts_per_day: int = 3,
    max_days: int = 60,
    start_date: datetime = None,
    min_occupation: float = 0.90,  # 90% mínimo de ocupación
    strategy: str = 'kg'
) -> Dict[str, Any]:
    """
    Genera cronograma optimizado para máxima ocupación y output.
    """
    
    # Cargar configuraciones
    rewinder_configs = {}
    for denier_str, config in rewinder_capacities_db.items():
        try:
            denier_val = int(re.search(r'\d+', denier_str).group())
            rewinder_configs[denier_val] = RewinderConfig(
                denier=denier_val,
                kg_per_hour=float(config.get('kg_per_hour', 0)),
                n_optimo=int(config.get('n_optimo', 1))
            )
        except Exception as e:
            logger.warning(f"Error parsing rewinder config {denier_str}: {e}")
    
    torsion_machines = []
    for denier_str, config in torsion_capacities_db.items():
        try:
            denier_val = int(re.search(r'\d+', denier_str).group())
            for m in config.get('machines', []):
                torsion_machines.append(TorsionMachine(
                    machine_id=m.get('machine_id', f'UNKNOWN_{denier_val}'),
                    denier=denier_val,
                    kgh=float(m.get('kgh', 0)),
                    husos=int(m.get('husos', 1))
                ))
        except Exception as e:
            logger.warning(f"Error parsing torsion config {denier_str}: {e}")
    
    # Cargar backlog
    backlog_items = []
    for code, data in backlog_summary.items():
        try:
            kg = float(data.get('kg_total', 0))
            if kg > 0.1:
                denier_val = int(re.search(r'\d+', str(data.get('denier', '0'))).group())
                backlog_items.append(BacklogItem(
                    ref=code,
                    description=data.get('description', ''),
                    denier=denier_val,
                    kg_pending=kg,
                    priority=1 if data.get('is_priority') else 0
                ))
        except Exception as e:
            logger.warning(f"Error parsing backlog item {code}: {e}")
    
    if not backlog_items:
        return {"error": "No backlog items to process"}
    
    # Inicializar optimizador
    optimizer = MaxOutputOptimizer(
        torsion_machines=torsion_machines,
        rewinder_configs=rewinder_configs,
        total_rewinders=total_rewinders,
        shift_hours=shift_hours,
        strategy=strategy
    )
    
    # Generar cronograma
    if start_date is None:
        start_date = datetime.now() + timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    cronograma = []
    completion_tracking = {}
    total_kg_initial = sum(item.kg_initial for item in backlog_items)
    
    # Tracking de continuidad
    previous_shift_assignments = []
    previous_day_deniers = set()
    
    current_date = start_date
    
    for day in range(max_days):
        active_items = [item for item in backlog_items if item.kg_pending > 0.1]
        if not active_items:
            break
        
        date_str = current_date.strftime("%Y-%m-%d")
        day_entry = {
            "fecha": date_str,
            "turnos": [],
            "turnos_torsion": [],
            "resumen_dia": {
                "kg_producidos": 0,
                "ocupacion_promedio": 0,
                "cambios_denier": 0,
                "items_completados": []
            }
        }
        
        day_deniers = set()
        
        for shift_num in range(shifts_per_day):
            shift_name = chr(ord('A') + shift_num)
            
            # Ejecutar optimización manteniendo continuidad
            shift_result = optimizer.assign_shift_max_occupation(
                active_items,
                previous_shift_assignments
            )
            
            if not shift_result:
                continue
            
            # Verificar ocupación mínima
            if shift_result['occupation_rate'] < min_occupation * 100:
                logger.warning(f"Low occupation on {date_str} Turno {shift_name}: "
                               f"{shift_result['occupation_rate']}%")
            
            # Actualizar backlog
            kg_produced = 0
            for assignment in shift_result['assignments']:
                denier = assignment['denier']
                kg_to_assign = assignment['kg_planned']
                
                items_for_denier = [item for item in backlog_items 
                                   if item.denier == denier and item.kg_pending > 0.1]
                
                for item in items_for_denier:
                    if kg_to_assign <= 0:
                        break
                    actual = min(item.kg_pending, kg_to_assign)
                    item.kg_pending -= actual
                    kg_to_assign -= actual
                    kg_produced += actual
                    
                    # Track usage for averages (summary table)
                    if item.ref not in completion_tracking:
                        if "_temp_tracking" not in locals(): _temp_tracking = {}
                        if item.ref not in _temp_tracking: 
                            _temp_tracking[item.ref] = {"puestos_acum": 0, "turnos_cont": 0}
                        _temp_tracking[item.ref]["puestos_acum"] += assignment['rewinder_posts']
                        _temp_tracking[item.ref]["turnos_cont"] += 1

                    if item.kg_pending <= 0.1 and item.ref not in completion_tracking:
                        item.completed = True
                        item.completion_date = f"{date_str} Turno {shift_name}"
                        # Finalize mapping from temp or current
                        p_acum = _temp_tracking[item.ref]["puestos_acum"] if "_temp_tracking" in locals() and item.ref in _temp_tracking else assignment['rewinder_posts']
                        t_cont = _temp_tracking[item.ref]["turnos_cont"] if "_temp_tracking" in locals() and item.ref in _temp_tracking else 1
                        
                        completion_tracking[item.ref] = {
                            "referencia": item.ref,
                            "descripcion": item.description,
                            "fecha_finalizacion": item.completion_date,
                            "kg_totales": item.kg_initial,
                            "denier": item.denier,
                            "puestos_promedio": round(p_acum / t_cont, 1)
                        }
                        day_entry["resumen_dia"]["items_completados"].append(item.ref)
                
                day_deniers.add(denier)
            
            # Formatear turno Rewinder
            turno_formatted = {
                "nombre": shift_name,
                "horario": f"{6 + shift_num*8:02d}:00 - {14 + shift_num*8:02d}:00",
                "asignaciones": [],
                "operarios_requeridos": 0,
                "estadisticas": {
                    "posts_ocupados": shift_result['posts_used'],
                    "posts_libres": shift_result['posts_remaining'],
                    "ocupacion_porcentaje": shift_result['occupation_rate'],
                    "kg_producidos": round(kg_produced, 1),
                    "maquinas_usadas": len(shift_result['machines_used']),
                    "deniers_activos": len(shift_result['deniers_used'])
                }
            }
            
            # Formatear turno Torsi\u00f3n
            turno_torsion = {
                "nombre": shift_name,
                "horario": turno_formatted["horario"],
                "asignaciones": [],
                "operarios_requeridos": 0
            }
            
            total_rw_ops = 0
            for assignment in shift_result['assignments']:
                # Rewinder assign
                turno_formatted["asignaciones"].append({
                    "referencia": f"Denier {assignment['denier']}",
                    "descripcion": ", ".join(assignment['references']),
                    "puestos": assignment['rewinder_posts'],
                    "operarios": assignment['rewinder_operators'],
                    "kg_producidos": assignment['kg_planned']
                })
                total_rw_ops += assignment['rewinder_operators']
                
                # Torsion assign
                for m_detail in assignment['torsion_details']:
                    turno_torsion["asignaciones"].append({
                        "maquina": m_detail['machine_id'],
                        "referencia": ", ".join(assignment['references']),
                        "denier": assignment['denier'],
                        "husos_asignados": m_detail['husos'],
                        "husos_totales": m_detail['husos'], # Simplificado
                        "kgh_maquina": m_detail['kgh'],
                        "kg_turno": m_detail['kg_shift'],
                        "operarios": 1
                    })
            
            turno_formatted["operarios_requeridos"] = total_rw_ops
            turno_torsion["operarios_requeridos"] = len(turno_torsion["asignaciones"])
            
            day_entry["turnos"].append(turno_formatted)
            day_entry["turnos_torsion"].append(turno_torsion)
            day_entry["resumen_dia"]["kg_producidos"] += kg_produced
            previous_shift_assignments = shift_result['assignments']
        
        # Calcular cambios de denier respecto al d\u00eda anterior
        if previous_day_deniers:
            changes = len(day_deniers - previous_day_deniers)
            day_entry["resumen_dia"]["cambios_denier"] = changes
        
        previous_day_deniers = day_deniers
        # Populate debug info for UI transparency
        daily_denier_stats = defaultdict(lambda: {"supply": 0, "demand": 0})
        for t_idx, t_rew in enumerate(day_entry["turnos"]):
            t_torsion = day_entry["turnos_torsion"][t_idx]
            for asig_torsion in t_torsion["asignaciones"]:
                daily_denier_stats[asig_torsion["denier"]]["supply"] += asig_torsion["kg_turno"]
            for asig_rew in t_rew["asignaciones"]:
                d_match = re.search(r'\d+', asig_rew["referencia"])
                if d_match:
                    d_key = int(d_match.group())
                    daily_denier_stats[d_key]["demand"] += asig_rew["kg_producidos"]
        
        balance_logs = []
        for d_key, stats in daily_denier_stats.items():
            balance_logs.append({
                "denier": d_key,
                "suministro_kg": round(stats["supply"], 1),
                "demanda_kg": round(stats["demand"], 1),
                "balance_ratio": round((stats["supply"] / max(stats["demand"], 1)) * 100, 1)
            })
            
        avg_posts_free = sum(t['estadisticas']['posts_libres'] for t in day_entry["turnos"]) / max(len(day_entry["turnos"]), 1)
        day_entry["debug_info"] = {
            "balance_torsion": balance_logs,
            "ocupacion_rewinder_avg": f"{round(sum(t['estadisticas']['ocupacion_porcentaje'] for t in day_entry['turnos'])/max(len(day_entry['turnos']),1), 1)}%",
            "puestos_libres_promedio": round(avg_posts_free, 1)
        }
        day_entry["resumen_dia"]["ocupacion_promedio"] = round(sum(t['estadisticas']['ocupacion_porcentaje'] for t in day_entry['turnos'])/max(len(day_entry['turnos']),1), 1)

        cronograma.append(day_entry)
        current_date += timedelta(days=1)
    
    # An\u00e1lisis final
    total_kg_produced = total_kg_initial - sum(item.kg_pending for item in backlog_items)
    
    # Alertas de eficiencia
    efficiency_alerts = []
    
    # Detectar d\u00edas con baja ocupaci\u00f3n
    low_occupation_days = [
        day for day in cronograma 
        if day["resumen_dia"]["ocupacion_promedio"] < min_occupation * 100
    ]
    
    if low_occupation_days:
        efficiency_alerts.append({
            "tipo": "OCUPACION_BAJA",
            "mensaje": f"{len(low_occupation_days)} d\u00edas con ocupaci\u00f3n < {min_occupation*100}%",
            "dias_afectados": [d["fecha"] for d in low_occupation_days[:5]],
            "recomendacion": "Agregar pedidos de deniers con capacidad disponible o consolidar turnos"
        })
    
    # Detectar capacidad ociosa
    idle_capacity = []
    for denier, eff in optimizer.denier_efficiency.items():
        used_in_plan = any(
            assignment.get('denier') == denier 
            for day in cronograma 
            for turno in day["turnos"] 
            for assignment in turno["asignaciones"]
        )
        if not used_in_plan:
            idle_capacity.append({
                'denier': denier,
                'capacidad_kgh': eff['torsion_kgh'],
                'maquinas_disponibles': eff['machines_available']
            })
    
    if idle_capacity:
        idle_capacity.sort(key=lambda x: x['capacidad_kgh'], reverse=True)
        efficiency_alerts.append({
            "tipo": "CAPACIDAD_OCIOSA",
            "mensaje": f"Se detectaron {len(idle_capacity)} deniers sin utilizar",
            "deniers_disponibles": idle_capacity[:3],
            "kg_adicionales_posibles": round(
                sum(d['capacidad_kgh'] for d in idle_capacity) * shift_hours * shifts_per_day * 7, 0
            ),
            "fecha_sugerida_inclusion": (start_date + timedelta(days=len(cronograma)//3)).strftime("%Y-%m-%d")
        })
    
    # Calculate averages for items that didn't finish
    if "_temp_tracking" in locals():
        for ref, data in _temp_tracking.items():
            if ref not in completion_tracking:
                item = next((i for i in backlog_items if i.ref == ref), None)
                if item:
                    completion_tracking[ref] = {
                        "referencia": item.ref,
                        "descripcion": item.description,
                        "fecha_finalizacion": "En proceso...",
                        "kg_totales": item.kg_initial,
                        "denier": item.denier,
                        "puestos_promedio": round(data["puestos_acum"] / data["turnos_cont"], 1)
                    }

    # Preparar datos de gr\u00e1ficas
    chart_data = {
        "labels": [day["fecha"] for day in cronograma],
        "dataset_kg_produccion": [day["resumen_dia"]["kg_producidos"] for day in cronograma],
        "dataset_operarios": [max((t["operarios_requeridos"] for t in day["turnos"]), default=0) for day in cronograma]
    }
    
    return {
        "scenario": {
            "resumen_global": {
                "comentario_estrategia": f"Estrategia: {optimizer.strategy} + M\u00e1xima Ocupaci\u00f3n + Continuidad",
                "fecha_inicio": start_date.strftime("%Y-%m-%d"),
                "fecha_finalizacion_total": cronograma[-1]["fecha"] if cronograma else None,
                "total_dias_programados": len(cronograma),
                "kg_totales_plan": round(total_kg_initial, 1),
                "kg_producidos": round(total_kg_produced, 1),
                "kg_pendientes": round(sum(item.kg_pending for item in backlog_items), 1),
                "eficiencia_promedio": round(
                    sum(day["resumen_dia"]["ocupacion_promedio"] for day in cronograma) / max(len(cronograma), 1), 1
                ),
                "alerta_capacidad": "✅ Plan optimizado para m\u00e1xima ocupaci\u00f3n" if not efficiency_alerts else "⚠️ Revisar alertas de eficiencia"
            },
            "alertas_eficiencia": efficiency_alerts,
            "tabla_finalizacion_referencias": list(completion_tracking.values()),
            "cronograma_diario": cronograma,
            "datos_para_grafica": chart_data
        }
    }


# ============================================================================
# FUNCIONES DE COMPATIBILIDAD CON API EXISTENTE
# ============================================================================

def generate_production_schedule(
    orders: List[Dict[str, Any]] = None,
    rewinder_capacities: Dict[str, Dict] = None,
    shifts: List[Dict[str, Any]] = None,
    torsion_capacities: Dict[str, Dict] = None,
    backlog_summary: Dict[str, Any] = None,
    strategy: str = 'kg'
) -> Dict[str, Any]:
    """
    Funci\u00f3n principal compatible con la API existente.
    Redirige a la nueva l\u00f3gica de optimizaci\u00f3n v2.
    """
    if not rewinder_capacities or not backlog_summary:
        return {
            "scenario": {
                "resumen_global": {
                    "comentario_estrategia": "Error: Datos insuficientes para la programaci\u00f3n.",
                    "alerta_capacidad": "\u274c Error de Datos"
                },
                "cronograma_diario": []
            }
        }
    
    # Mapeo de argumentos a la nueva funci\u00f3n de generaci\u00f3n
    return generate_optimized_schedule(
        backlog_summary=backlog_summary,
        torsion_capacities_db=torsion_capacities or {},
        rewinder_capacities_db=rewinder_capacities or {},
        total_rewinders=28,
        shift_hours=8.0,
        shifts_per_day=3,
        max_days=60,
        strategy=strategy
    )


def run_optimized_production(
    db_backlog: Dict[str, Any],
    db_torsion_config: Dict[str, Any],
    db_rewinder_config: Dict[str, Any],
    app_config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Funci\u00f3n principal para integraci\u00f3n con la APP (v2).
    """
    app_config = app_config or {}
    
    # Transformar backlog
    backlog_summary = {}
    for item in db_backlog.get('items', []):
        code = item.get('code') or item.get('referencia')
        if code:
            backlog_summary[code] = {
                'kg_total': item.get('kg_pendientes', item.get('cantidad', 0)),
                'description': item.get('descripcion', ''),
                'denier': item.get('denier', item.get('titulo', 0)),
                'priority': item.get('prioridad', 0)
            }
    
    return generate_optimized_schedule(
        backlog_summary=backlog_summary,
        torsion_capacities_db=db_torsion_config,
        rewinder_capacities_db=db_rewinder_config,
        total_rewinders=app_config.get('total_rewinders', 28),
        shift_hours=app_config.get('shift_hours', 8.0),
        shifts_per_day=app_config.get('shifts_per_day', 3),
        max_days=app_config.get('max_days', 60),
        start_date=app_config.get('start_date'),
        min_occupation=app_config.get('min_occupation', 0.90)
    )
