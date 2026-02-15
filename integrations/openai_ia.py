#codigo version Kimi 2.5 V2.0
from typing import List, Dict, Any, Tuple, Set, Optional
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


@dataclass
class MissingReference:
    """Representa una referencia que falta para optimizar la producción"""
    denier: int
    kg_recommended: float
    reason: str
    priority: int = 1
    estimated_completion_days: int = 0


# ============================================================================
# MOTOR DE OPTIMIZACIÓN V3 - SMART STOP + MISSING REFERENCE DETECTOR
# ============================================================================

class SmartStopOptimizer:
    """
    Estrategia V3:
    1. Programar SOLO mientras se mantenga ocupación >= min_occupation (ej: 90%)
    2. Detectar automáticamente qué referencias faltan para continuar
    3. Generar "shopping list" de pedidos necesarios
    4. Parar cuando no se pueda mantener eficiencia, no cuando se acabe el backlog
    """
    
    def __init__(self, 
                 torsion_machines: List[TorsionMachine],
                 rewinder_configs: Dict[int, RewinderConfig],
                 total_rewinders: int = 28,
                 shift_hours: float = 8.0,
                 strategy: str = 'kg',
                 min_occupation: float = 0.90):
        
        self.total_rewinders = total_rewinders
        self.shift_hours = shift_hours
        self.rewinder_configs = rewinder_configs
        self.strategy = strategy
        self.min_occupation = min_occupation
        
        # Agrupar máquinas por denier
        self.machines_by_denier = defaultdict(list)
        for m in torsion_machines:
            self.machines_by_denier[m.denier].append(m)
        
        # Calcular capacidades
        self.denier_capacity = {}
        for denier, machines in self.machines_by_denier.items():
            self.denier_capacity[denier] = {
                'total_kgh': sum(m.kgh for m in machines),
                'machines': machines,
                'count': len(machines)
            }
        
        # Reglas de incompatibilidad
        self.incompatible_deniers = {
            2000: {3000, 4000, 6000, 9000, 12000, 18000},
            3000: {2000, 6000, 12000, 18000},
            4000: {2000, 6000, 12000, 18000},
            6000: {2000, 3000, 4000, 9000, 18000},
            9000: {2000, 6000},
            12000: {2000, 3000, 4000},
            18000: {2000, 3000, 4000, 6000}
        }
        
        # Tracking de uso
        self.used_machines_history = set()
        
        logger.info(f"SmartStopOptimizer ready: {len(torsion_machines)} machines, "
                   f"{len(rewinder_configs)} configs, min_occ={min_occupation}")
    
    def calculate_optimal_rewinders(self, denier: int, target_posts: int = None) -> int:
        """Calcula posts óptimos respetando N óptimo"""
        if denier not in self.rewinder_configs:
            return 0
        
        config = self.rewinder_configs[denier]
        n_opt = config.n_optimo
        
        if target_posts is None:
            # Calcular para balancear con torsión
            torsion_kgh = self.denier_capacity.get(denier, {}).get('total_kgh', 0)
            if torsion_kgh > 0:
                needed = math.ceil(torsion_kgh / config.kg_per_hour)
                target_posts = min(needed, self.total_rewinders)
            else:
                target_posts = n_opt
        
        # Generar valores válidos (múltiplos de N óptimo con ±1 flexibilidad)
        valid_values = set()
        for k in range(1, (self.total_rewinders // max(n_opt - 1, 1)) + 2):
            base = k * n_opt
            for offset in [-1, 0, 1]:
                val = base + offset
                if 1 <= val <= self.total_rewinders:
                    valid_values.add(val)
        
        valid_values.add(n_opt)  # Siempre incluir mínimo
        valid_values = sorted(valid_values)
        
        return min(valid_values, key=lambda x: abs(x - target_posts))
    
    def find_best_combination_for_full_occupation(self, 
                                                  active_items: List[BacklogItem],
                                                  current_deniers: Set[int] = None) -> Tuple[List[Dict], float, List[MissingReference]]:
        """
        Encuentra combinación que logre ocupación >= min_occupation.
        Retorna: (combinación, ocupación_alcanzada, referencias_faltantes)
        """
        current_deniers = current_deniers or set()
        
        # Agrupar items por denier
        denier_items = defaultdict(list)
        for item in active_items:
            if item.kg_pending > 0.1:
                denier_items[item.denier].append(item)
        
        if not denier_items:
            return [], 0.0, []
        
        # Calcular capacidad de cada denier disponible
        candidates = []
        for denier, items in denier_items.items():
            if denier not in self.denier_capacity:
                continue
            
            total_backlog = sum(i.kg_pending for i in items)
            has_priority = any(i.priority > 0 for i in items)
            
            # Posts necesarios para agotar este backlog en 1 turno
            config = self.rewinder_configs.get(denier)
            if not config:
                continue
            
            # Cuántos posts necesito para producir todo el backlog
            kg_per_turn = config.kg_per_hour * self.shift_hours
            posts_needed_for_backlog = math.ceil(total_backlog / kg_per_turn)
            
            # Posts óptimos para balancear con torsión
            posts_for_balance = self.calculate_optimal_rewinders(denier)
            
            # Tomar el menor (no podemos producir más del backlog ni más del balance)
            optimal_posts = min(posts_for_balance, max(posts_needed_for_backlog, 1))
            
            # Ajustar a valor válido
            optimal_posts = self.calculate_optimal_rewinders(denier, optimal_posts)
            
            candidates.append({
                'denier': denier,
                'posts': optimal_posts,
                'backlog_kg': total_backlog,
                'has_priority': has_priority,
                'is_current': denier in current_deniers,
                'capacity_kgh': self.denier_capacity[denier]['total_kgh'],
                'machines_count': self.denier_capacity[denier]['count']
            })
        
        if not candidates:
            return [], 0.0, []
        
        # Ordenar: continuos primero, luego por backlog, luego por capacidad
        candidates.sort(key=lambda x: (
            not x['is_current'],
            -x['has_priority'] if self.strategy == 'priority' else 0,
            -x['backlog_kg'],
            -x['capacity_kgh']
        ))
        
        # Intentar llenar hasta min_occupation
        combination = []
        posts_used = 0
        used_deniers = set()
        
        # Fase 1: Agregar candidatos hasta alcanzar ocupación mínima
        for cand in candidates:
            if posts_used >= self.total_rewinders * self.min_occupation:
                break
            
            # Verificar incompatibilidades
            is_incompatible = any(
                cand['denier'] in self.incompatible_deniers.get(used_d, set())
                for used_d in used_deniers
            )
            
            if is_incompatible:
                continue
            
            # Calcular cuántos posts podemos agregar
            available_posts = self.total_rewinders - posts_used
            posts_to_add = min(cand['posts'], available_posts)
            
            if posts_to_add < 2:  # Mínimo útil
                continue
            
            # Ajustar a valor válido
            posts_to_add = self.calculate_optimal_rewinders(cand['denier'], posts_to_add)
            
            if posts_to_add > 0:
                cand['posts'] = posts_to_add
                combination.append(cand)
                posts_used += posts_to_add
                used_deniers.add(cand['denier'])
        
        occupation = posts_used / self.total_rewinders
        
        # Fase 2: Si no alcanzamos ocupación mínima, identificar qué falta
        missing_refs = []
        if occupation < self.min_occupation:
            missing_refs = self._identify_missing_references(
                candidates, combination, posts_used, active_items
            )
        
        return combination, occupation, missing_refs
    
    def _identify_missing_references(self, 
                                     all_candidates: List[Dict], 
                                     current_combination: List[Dict],
                                     posts_used: int,
                                     active_items: List[BacklogItem]) -> List[MissingReference]:
        """
        Identifica qué referencias faltan para alcanzar ocupación óptima
        """
        missing = []
        posts_needed = math.ceil(self.total_rewinders * self.min_occupation) - posts_used
        
        if posts_needed <= 0:
            return missing
        
        # Deniers ya usados o con backlog
        used_deniers = {c['denier'] for c in current_combination}
        deniers_with_backlog = {c['denier'] for c in all_candidates}
        
        # Deniers con capacidad disponible pero SIN backlog
        available_deniers = set(self.denier_capacity.keys()) - deniers_with_backlog
        
        # Priorizar deniers de alta capacidad que no están siendo usados
        high_capacity_candidates = []
        for denier in available_deniers:
            cap = self.denier_capacity[denier]
            config = self.rewinder_configs.get(denier)
            if not config:
                continue
            
            # Verificar incompatibilidad con deniers actuales
            is_incompatible = any(
                denier in self.incompatible_deniers.get(used_d, set())
                for used_d in used_deniers
            )
            
            if is_incompatible:
                continue
            
            # Calcular cuántos posts podríamos usar de este denier
            optimal_posts = self.calculate_optimal_rewinders(denier)
            
            # Cuántos kg necesitamos para ocupar estos posts
            kg_needed = optimal_posts * config.kg_per_hour * self.shift_hours
            
            high_capacity_candidates.append({
                'denier': denier,
                'posts': optimal_posts,
                'kg_needed': kg_needed,
                'capacity': cap['total_kgh'],
                'machines': cap['count']
            })
        
        # Ordenar por capacidad (mayor primero)
        high_capacity_candidates.sort(key=lambda x: -x['capacity'])
        
        # Generar recomendaciones
        remaining_posts_needed = posts_needed
        for cand in high_capacity_candidates:
            if remaining_posts_needed <= 0:
                break
            
            posts_can_use = min(cand['posts'], remaining_posts_needed)
            if posts_can_use >= 2:
                kg_recommended = posts_can_use * self.rewinder_configs[cand['denier']].kg_per_hour * self.shift_hours
                
                missing.append(MissingReference(
                    denier=cand['denier'],
                    kg_recommended=math.ceil(kg_recommended),
                    reason=f"Necesario para alcanzar {self.min_occupation*100}% ocupación. "
                           f"Capacidad torsión disponible: {cand['capacity']:.1f} kg/h en {cand['machines']} máquinas",
                    priority=3 if cand['capacity'] > 150 else 2,  # Priorizar alta capacidad
                    estimated_completion_days=math.ceil(kg_recommended / (cand['capacity'] * self.shift_hours))
                ))
                
                remaining_posts_needed -= posts_can_use
        
        return missing
    
    def assign_shift_smart(self, 
                          active_items: List[BacklogItem],
                          previous_assignments: List[Dict] = None,
                          day_number: int = 0) -> Dict[str, Any]:
        """
        Asigna un turno con lógica de parada inteligente
        """
        previous_assignments = previous_assignments or []
        current_deniers = set(a['denier'] for a in previous_assignments)
        
        combination, occupation, missing_refs = self.find_best_combination_for_full_occupation(
            active_items, current_deniers
        )
        
        if not combination or occupation < self.min_occupation * 0.8:  # Tolerancia 80% del mínimo
            return {
                'assignments': [],
                'occupation_rate': 0.0,
                'should_stop': True,
                'missing_references': missing_refs,
                'reason': f'Ocupación {occupation*100:.1f}% < {self.min_occupation*80}% mínimo aceptable'
            }
        
        # Verificar si debemos parar por baja ocupación futura
        if occupation < self.min_occupation and not missing_refs:
            # Tenemos combinación pero no alcanzamos ocupación mínima y no hay forma de mejorar
            return {
                'assignments': [],
                'occupation_rate': occupation * 100,
                'should_stop': True,
                'missing_references': [],
                'reason': f'Ocupación {occupation*100:.1f}% insuficiente y no hay deniers compatibles disponibles'
            }
        
        # Construir asignaciones
        assignments = []
        machines_used = set()
        total_kg = 0
        
        for combo in combination:
            denier = combo['denier']
            num_rewinders = combo['posts']
            
            config = self.rewinder_configs[denier]
            rw_capacity_kgh = num_rewinders * config.kg_per_hour
            
            # Asignar máquinas de torsión
            available_machines = [
                m for m in self.machines_by_denier.get(denier, [])
                if m.machine_id not in machines_used
            ]
            
            if not available_machines:
                continue
            
            total_torsion_kgh = sum(m.kgh for m in available_machines)
            
            # Calcular producción
            items_for_denier = [item for item in active_items 
                              if item.denier == denier and item.kg_pending > 0.1]
            max_needed = sum(item.kg_pending for item in items_for_denier)
            
            production_capacity = rw_capacity_kgh * self.shift_hours
            production_kg = min(production_capacity, max_needed)
            
            # Detalle
            torsion_details = []
            for m in available_machines:
                machines_used.add(m.machine_id)
                self.used_machines_history.add(m.machine_id)
                torsion_details.append({
                    'machine_id': m.machine_id,
                    'kgh': m.kgh,
                    'kg_shift': m.kgh * self.shift_hours,
                    'husos': m.husos
                })
            
            refs = [item.ref for item in items_for_denier[:3]]
            
            assignment = {
                'denier': denier,
                'references': refs,
                'rewinder_posts': num_rewinders,
                'rewinder_operators': math.ceil(num_rewinders / config.n_optimo),
                'rewinder_rate_kgh': rw_capacity_kgh,
                'torsion_machines': [m.machine_id for m in available_machines],
                'torsion_rate_kgh': total_torsion_kgh,
                'torsion_details': torsion_details,
                'balance_ratio': round(rw_capacity_kgh / max(total_torsion_kgh, 1), 2),
                'kg_planned': round(production_kg, 1)
            }
            
            assignments.append(assignment)
            total_kg += production_kg
        
        total_posts_used = sum(a['rewinder_posts'] for a in assignments)
        
        return {
            'assignments': assignments,
            'machines_used': list(machines_used),
            'posts_used': total_posts_used,
            'posts_remaining': self.total_rewinders - total_posts_used,
            'occupation_rate': round(total_posts_used / self.total_rewinders * 100, 1),
            'total_kg_planned': round(total_kg, 1),
            'deniers_used': list(set(a['denier'] for a in assignments)),
            'should_stop': False,
            'missing_references': missing_refs
        }


# ============================================================================
# GENERADOR DE CRONOGRAMA CON PARADA INTELIGENTE
# ============================================================================

def generate_smart_schedule(
    backlog_summary: Dict[str, Any],
    torsion_capacities_db: Dict[str, Any],
    rewinder_capacities_db: Dict[str, Any],
    total_rewinders: int = 28,
    shift_hours: float = 8.0,
    shifts_per_day: int = 3,
    max_days: int = 60,
    start_date: datetime = None,
    min_occupation: float = 0.90,
    strategy: str = 'kg'
) -> Dict[str, Any]:
    """
    Genera cronograma que se DETIENE cuando no se puede mantener ocupación óptima
    e identifica qué referencias faltan.
    """
    
    # Cargar configuraciones
    rewinder_configs = {}
    for denier_str, config in rewinder_capacities_db.items():
        try:
            denier = int(denier_str)
            rewinder_configs[denier] = RewinderConfig(
                denier=denier,
                kg_per_hour=float(config.get('kg_per_hour', 0)),
                n_optimo=int(config.get('n_optimo', 1))
            )
        except Exception as e:
            logger.warning(f"Error parsing rewinder config: {e}")
    
    torsion_machines = []
    for denier_str, config in torsion_capacities_db.items():
        try:
            denier = int(denier_str)
            for m in config.get('machines', []):
                torsion_machines.append(TorsionMachine(
                    machine_id=m.get('machine_id', f'UNKNOWN_{denier}'),
                    denier=denier,
                    kgh=float(m.get('kgh', 0)),
                    husos=int(m.get('husos', 1))
                ))
        except Exception as e:
            logger.warning(f"Error parsing torsion config: {e}")
    
    # Cargar backlog
    backlog_items = []
    for code, data in backlog_summary.items():
        try:
            kg = float(data.get('kg_total', 0))
            if kg > 0.1:
                backlog_items.append(BacklogItem(
                    ref=code,
                    description=data.get('description', ''),
                    denier=int(data.get('denier', 0)),
                    kg_pending=kg,
                    priority=1 if data.get('is_priority') else 0
                ))
        except Exception as e:
            logger.warning(f"Error parsing backlog item {code}: {e}")
    
    if not backlog_items:
        return {
            "error": "No backlog items to process",
            "recommendations": _generate_capacity_recommendations(torsion_machines, rewinder_configs)
        }
    
    # Inicializar optimizador
    optimizer = SmartStopOptimizer(
        torsion_machines=torsion_machines,
        rewinder_configs=rewinder_configs,
        total_rewinders=total_rewinders,
        shift_hours=shift_hours,
        strategy=strategy,
        min_occupation=min_occupation
    )
    
    # Generar cronograma
    if start_date is None:
        start_date = datetime.now() + timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    cronograma = []
    completion_tracking = {}
    total_kg_initial = sum(item.kg_initial for item in backlog_items)
    
    previous_shift_assignments = []
    current_date = start_date
    stop_reason = None
    final_missing_refs = []
    
    for day in range(max_days):
        active_items = [item for item in backlog_items if item.kg_pending > 0.1]
        
        date_str = current_date.strftime("%Y-%m-%d")
        day_entry = {
            "fecha": date_str,
            "turnos": [],
            "turnos_torsion": [],
            "resumen_dia": {
                "kg_producidos": 0,
                "ocupacion_promedio": 0,
                "items_completados": []
            }
        }
        
        day_has_production = False
        
        for shift_num in range(shifts_per_day):
            shift_name = chr(ord('A') + shift_num)
            
            shift_result = optimizer.assign_shift_smart(
                active_items,
                previous_shift_assignments,
                day
            )
            
            # Verificar si debemos parar
            if shift_result.get('should_stop'):
                stop_reason = shift_result.get('reason', 'Ocupación insuficiente')
                final_missing_refs = shift_result.get('missing_references', [])
                
                # Si es el primer turno del día, no agregamos este día
                if shift_num == 0 and not day_has_production:
                    day_entry = None
                break
            
            day_has_production = True
            
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
                    
                    if item.kg_pending <= 0.1 and item.ref not in completion_tracking:
                        item.completed = True
                        item.completion_date = f"{date_str} Turno {shift_name}"
                        completion_tracking[item.ref] = {
                            "referencia": item.ref,
                            "descripcion": item.description,
                            "fecha_finalizacion": item.completion_date,
                            "kg_totales": item.kg_initial,
                            "denier": item.denier
                        }
                        day_entry["resumen_dia"]["items_completados"].append(item.ref)
            
            # Formatear salida
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
            
            turno_torsion = {
                "nombre": shift_name,
                "horario": turno_formatted["horario"],
                "asignaciones": [],
                "operarios_requeridos": 0
            }
            
            total_rw_ops = 0
            for assignment in shift_result['assignments']:
                turno_formatted["asignaciones"].append({
                    "referencia": f"Denier {assignment['denier']}",
                    "descripcion": ", ".join(assignment['references']),
                    "puestos": assignment['rewinder_posts'],
                    "operarios": assignment['rewinder_operators'],
                    "kg_producidos": assignment['kg_planned']
                })
                total_rw_ops += assignment['rewinder_operators']
                
                for m_detail in assignment['torsion_details']:
                    turno_torsion["asignaciones"].append({
                        "maquina": m_detail['machine_id'],
                        "referencia": ", ".join(assignment['references']),
                        "denier": assignment['denier'],
                        "husos_asignados": m_detail['husos'],
                        "husos_totales": m_detail['husos'],
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
        
        if day_entry and day_has_production:
            day_entry["resumen_dia"]["ocupacion_promedio"] = round(
                sum(t['estadisticas']['ocupacion_porcentaje'] for t in day_entry["turnos"]) / 
                max(len(day_entry["turnos"]), 1), 1
            )
            cronograma.append(day_entry)
        
        # Si debemos parar, salir del loop de días
        if stop_reason:
            break
        
        current_date += timedelta(days=1)
    
    # Análisis final
    total_kg_produced = total_kg_initial - sum(item.kg_pending for item in backlog_items)
    
    # Generar recomendaciones de pedidos faltantes
    shopping_list = _generate_shopping_list(
        final_missing_refs, 
        backlog_items,
        optimizer,
        start_date,
        len(cronograma)
    )
    
    # Preparar respuesta
    chart_data = {
        "labels": [day["fecha"] for day in cronograma],
        "dataset_kg_produccion": [day["resumen_dia"]["kg_producidos"] for day in cronograma],
        "dataset_operarios": [max((t["operarios_requeridos"] for t in day["turnos"]), default=0) 
                             for day in cronograma]
    }
    
    return {
        "scenario": {
            "resumen_global": {
                "comentario_estrategia": f"SmartStop: Programación óptima hasta agotar eficiencia",
                "fecha_inicio": start_date.strftime("%Y-%m-%d"),
                "fecha_fin_programada": cronograma[-1]["fecha"] if cronograma else None,
                "dias_programados": len(cronograma),
                "turnos_totales": sum(len(d["turnos"]) for d in cronograma),
                "kg_totales_inicial": round(total_kg_initial, 1),
                "kg_producidos": round(total_kg_produced, 1),
                "kg_remanentes": round(sum(item.kg_pending for item in backlog_items), 1),
                "eficiencia_promedio": round(
                    sum(day["resumen_dia"]["ocupacion_promedio"] for day in cronograma) / 
                    max(len(cronograma), 1), 1
                ),
                "ocupacion_minima_objetivo": f"{min_occupation*100}%",
                "motivo_parada": stop_reason
            },
            "alertas_y_recomendaciones": {
                "tipo": "PROGRAMA_INCOMPLETO",
                "mensaje": "Se detuvo la programación por baja ocupación. Se requieren más pedidos.",
                "shopping_list": shopping_list,
                "referencias_faltantes_detalle": [
                    {
                        "denier": m.denier,
                        "kg_recomendados": m.kg_recommended,
                        "razon": m.reason,
                        "prioridad": m.priority,
                        "dias_estimados": m.estimated_completion_days
                    } for m in final_missing_refs
                ]
            },
            "tabla_finalizacion_referencias": list(completion_tracking.values()),
            "cronograma_diario": cronograma,
            "datos_para_grafica": chart_data
        }
    }


def _generate_shopping_list(missing_refs: List[MissingReference],
                           current_backlog: List[BacklogItem],
                           optimizer: SmartStopOptimizer,
                           start_date: datetime,
                           days_programmed: int) -> Dict[str, Any]:
    """
    Genera lista de compras/recomendaciones de pedidos necesarios
    """
    if not missing_refs:
        return {
            "mensaje": "No se detectaron referencias faltantes. El backlog actual permite ocupación óptima.",
            "accion": "Continuar con programa actual"
        }
    
    # Agrupar por denier
    by_denier = defaultdict(list)
    for ref in missing_refs:
        by_denier[ref.denier].append(ref)
    
    recommendations = []
    total_additional_kg = 0
    
    for denier, refs in sorted(by_denier.items(), key=lambda x: -sum(r.kg_recommended for r in x[1])):
        total_kg = sum(r.kg_recommended for r in refs)
        total_additional_kg += total_kg
        
        # Calcular fecha sugerida
        completion_time = max(r.estimated_completion_days for r in refs)
        suggested_date = (start_date + timedelta(days=days_programmed + completion_time))
        
        recommendations.append({
            "denier": denier,
            "kg_necesarios": math.ceil(total_kg),
            "fecha_ideal_ingreso": suggested_date.strftime("%Y-%m-%d"),
            "beneficio": f"Permite ocupar {sum(r.kg_recommended for r in refs) / (28 * 5.6 * 8) * 100:.0f}% más de capacidad",
            "detalle": refs[0].reason
        })
    
    return {
        "mensaje": f"Se requieren {len(recommendations)} deniers adicionales para mantener {optimizer.min_occupation*100}% ocupación",
        "kg_totales_adicionales": math.ceil(total_additional_kg),
        "fecha_limite_ingreso": (start_date + timedelta(days=days_programmed)).strftime("%Y-%m-%d"),
        "recomendaciones_por_denier": recommendations,
        "impacto_estimado": f"Incremento de {total_additional_kg / max(sum(item.kg_pending for item in current_backlog), 1) * 100:.1f}% en producción total"
    }


def _generate_capacity_recommendations(torsion_machines, rewinder_configs):
    """Genera recomendaciones cuando no hay backlog inicial"""
    return {
        "mensaje": "No hay pedidos en backlog. Capacidad total disponible:",
        "capacidad_por_denier": [
            {
                "denier": denier,
                "maquinas": len([m for m in torsion_machines if m.denier == denier]),
                "capacidad_diaria_kg": sum(m.kgh for m in torsion_machines if m.denier == denier) * 24
            }
            for denier in sorted(set(m.denier for m in torsion_machines))
        ]
    }


# ============================================================================
# FUNCIONES DE COMPATIBILIDAD
# ============================================================================

def generate_production_schedule(
    orders: List[Dict[str, Any]] = None,
    rewinder_capacities: Dict[str, Dict] = None,
    shifts: List[Dict[str, Any]] = None,
    torsion_capacities: Dict[str, Dict] = None,
    backlog_summary: Dict[str, Any] = None,
    strategy: str = 'kg',
    min_occupation: float = 0.90
) -> Dict[str, Any]:
    """
    API compatible con la versión anterior
    """
    if not rewinder_capacities or not backlog_summary:
        return {
            "scenario": {
                "resumen_global": {
                    "comentario_estrategia": "Error: Datos insuficientes",
                    "alerta_capacidad": "❌ Error"
                },
                "cronograma_diario": []
            }
        }
    
    return generate_smart_schedule(
        backlog_summary=backlog_summary,
        torsion_capacities_db=torsion_capacities or {},
        rewinder_capacities_db=rewinder_capacities or {},
        total_rewinders=28,
        shift_hours=8.0,
        shifts_per_day=3,
        max_days=60,
        min_occupation=min_occupation,
        strategy=strategy
    )


def run_smart_production(
    db_backlog: Dict[str, Any],
    db_torsion_config: Dict[str, Any],
    db_rewinder_config: Dict[str, Any],
    app_config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Función principal para la APP
    """
    app_config = app_config or {}
    
    backlog_summary = {}
    for item in db_backlog.get('items', []):
        code = item.get('code') or item.get('referencia')
        if code:
            backlog_summary[code] = {
                'kg_total': item.get('kg_pendientes', item.get('cantidad', 0)),
                'description': item.get('descripcion', ''),
                'denier': item.get('denier', item.get('titulo', 0)),
                'priority': item.get('prioridad', 0),
                'is_priority': item.get('prioridad', 0) > 0
            }
    
    return generate_smart_schedule(
        backlog_summary=backlog_summary,
        torsion_capacities_db=db_torsion_config,
        rewinder_capacities_db=db_rewinder_config,
        total_rewinders=app_config.get('total_rewinders', 28),
        shift_hours=app_config.get('shift_hours', 8.0),
        shifts_per_day=app_config.get('shifts_per_day', 3),
        max_days=app_config.get('max_days', 60),
        start_date=app_config.get('start_date'),
        min_occupation=app_config.get('min_occupation', 0.90),
        strategy=app_config.get('strategy', 'kg')
    )


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    # Configuración
    torsion_db = {
        "2000": {"machines": [
            {"machine_id": "T11", "kgh": 26.08, "husos": 1},
            {"machine_id": "T12", "kgh": 26.08, "husos": 1},
            {"machine_id": "T15", "kgh": 28.0, "husos": 1},
            {"machine_id": "T16", "kgh": 32.7, "husos": 1}
        ]},
        "3000": {"machines": [
            {"machine_id": "T11", "kgh": 39.11, "husos": 1},
            {"machine_id": "T12", "kgh": 39.11, "husos": 1},
            {"machine_id": "T15", "kgh": 41.5, "husos": 1},
            {"machine_id": "T16", "kgh": 49.0, "husos": 1}
        ]},
        "4000": {"machines": [
            {"machine_id": "T11", "kgh": 52.15, "husos": 1},
            {"machine_id": "T12", "kgh": 52.15, "husos": 1}
        ]},
        "6000": {"machines": [
            {"machine_id": "T11", "kgh": 78.23, "husos": 1},
            {"machine_id": "T12", "kgh": 78.23, "husos": 1}
        ]},
        "12000": {"machines": [
            {"machine_id": "T14", "kgh": 160.0, "husos": 1},
            {"machine_id": "T15", "kgh": 166.0, "husos": 1},
            {"machine_id": "T16", "kgh": 196.0, "husos": 1}
        ]},
        "18000": {"machines": [
            {"machine_id": "T14", "kgh": 240.0, "husos": 1}
        ]}
    }
    
    rewinder_db = {
        "2000": {"kg_per_hour": 5.6, "n_optimo": 9},
        "3000": {"kg_per_hour": 9.1, "n_optimo": 5},
        "4000": {"kg_per_hour": 11.4, "n_optimo": 4},
        "6000": {"kg_per_hour": 17.0, "n_optimo": 3},
        "12000": {"kg_per_hour": 21.1, "n_optimo": 2},
        "18000": {"kg_per_hour": 31.0, "n_optimo": 2}
    }
    
    # Backlog reducido (simulando escasez)
    backlog_db = {
        "CAB00588": {"kg_total": 2000, "description": "CABUYA ECO 4X1 NEGRO", "denier": 4000},
        "CAB00629": {"kg_total": 1500, "description": "RAFIA PP ECO 6x1K NEGRA", "denier": 6000},
        "CAB04132": {"kg_total": 1000, "description": "CABUYA TOMAT BLANCA UV", "denier": 2000},
    }
    
    resultado = run_smart_production(
        db_backlog={'items': [{'code': k, **v} for k, v in backlog_db.items()]},
        db_torsion_config=torsion_db,
        db_rewinder_config=rewinder_db,
        app_config={'min_occupation': 0.90}
    )
    
    print("\n" + "="*70)
    print("RESULTADO SMART STOP OPTIMIZER")
    print("="*70)
    
    resumen = resultado['scenario']['resumen_global']
    print(f"Días programados: {resumen['dias_programados']}")
    print(f"Motivo parada: {resumen['motivo_parada']}")
    print(f"Eficiencia promedio: {resumen['eficiencia_promedio']}%")
    
    print("\n📋 SHOPPING LIST - REFERENCIAS FALTANTES:")
    shopping = resultado['scenario']['alertas_y_recomendaciones']['shopping_list']
    print(f"Total kg adicionales necesarios: {shopping.get('kg_totales_adicionales', 0)}")
    
    for rec in shopping.get('recomendaciones_por_denier', []):
        print(f"\n  Denier {rec['denier']}:")
        print(f"    - Kg necesarios: {rec['kg_necesarios']}")
        print(f"    - Ingresar antes de: {rec['fecha_ideal_ingreso']}")
        print(f"    - Beneficio: {rec['beneficio']}")
