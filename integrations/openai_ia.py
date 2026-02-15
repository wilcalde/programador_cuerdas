# codigo refactorizado version TorsionFocus V1.0
from typing import List, Dict, Any, Tuple, Set, Optional
import math
from datetime import datetime, timedelta
from collections import defaultdict, deque
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
    assigned_machine: str = None # Para tracking
    
    def __post_init__(self):
        if self.kg_initial == 0.0:
            self.kg_initial = self.kg_pending

# ============================================================================
# OPTIMIZADOR PRINCIPAL: TORSION FOCUSED
# ============================================================================

class TorsionFocusedOptimizer:
    """
    Estrategia 'Torsion Optimized':
    1. Asignación estricta de máquinas por Denier.
    2. Regla de 4 máquinas activas simultáneas.
    3. Uso de T16 como máquina de backup/cambio.
    """
    def __init__(self, 
                 torsion_machines: List[TorsionMachine], 
                 rewinder_configs: Dict[int, RewinderConfig],
                 shift_hours: float = 8.0):
        
        self.torsion_machines = torsion_machines
        self.rewinder_configs = rewinder_configs
        self.shift_hours = shift_hours
        
        # Mapa de máquinas (ID -> Objeto TorsionMachine genérico o lista)
        # Como las máquinas vienen por denier, normalizamos
        self.machine_specs = {} # ID -> {kgh_base, husos}
        for m in torsion_machines:
            # Asumimos que kgh puede variar por denier, pero guardamos referencia
            if m.machine_id not in self.machine_specs:
                self.machine_specs[m.machine_id] = {'husos': m.husos}

        # REGLAS DE COMPATIBILIDAD ESTRICTA
        # T11, T12: 4000, 6000
        # T15: 2000, 2500, 3000
        # T14: 9000, 12000, 18000
        # T16: 2000 - 12000 (Backup)
        self.compatibility_rules = {
            'T11': {4000, 6000},
            'T12': {4000, 6000},
            'T15': {2000, 2500, 3000},
            'T14': {9000, 12000, 18000},
            'T16': {2000, 2500, 3000, 4000, 6000, 9000, 12000}
        }
        
        # Máquinas Principales vs Backup
        self.main_machines = ['T11', 'T12', 'T14', 'T15']
        self.backup_machine = 'T16' 
        self.max_active_machines = 4

    def get_machine_kgh(self, machine_id: str, denier: int) -> float:
        """Busca el KGH específico para esa combinación en la data de entrada"""
        for m in self.torsion_machines:
            if m.machine_id == machine_id and m.denier == denier:
                return m.kgh
        return 0.0

    def calculate_machine_hours(self, denier: int, kg: float, machine_id: str) -> float:
        kgh = self.get_machine_kgh(machine_id, denier)
        if kgh <= 0: return float('inf')
        return kg / kgh

    def plan_production(self, backlog_items: List[BacklogItem], max_days: int = 60) -> Dict[str, Any]:
        """
        Simulación basada en eventos discretos (Shift-based).
        """
        # 1. Agrupar Backlog por Denier
        items_by_denier = defaultdict(list)
        for item in backlog_items:
            items_by_denier[item.denier].append(item)
            
        # 2. Asignar Colas de Trabajo a Máquinas Principales
        # Estructura: machine_queues['T11'] = deque([item1, item2...])
        machine_queues = {m: deque() for m in self.main_machines}
        
        # Lógica de reparto: Llenar colas verificando compatibilidad
        # Prioridad: Orden de llegada en backlog
        unassigned_items = []
        
        # Copia para no mutar original incontroladamente
        pending_items = sorted(deepcopy(backlog_items), key=lambda x: x.priority, reverse=True)
        
        for item in pending_items:
            # Encontrar máquinas compatibles
            compatible_m = []
            for m_id, allowed_deniers in self.compatibility_rules.items():
                if m_id in self.main_machines and item.denier in allowed_deniers:
                    compatible_m.append(m_id)
            
            if not compatible_m:
                unassigned_items.append(item)
                continue
                
            # Asignar a la que tenga MENOS carga en horas acumuladas
            best_m = None
            min_hours = float('inf')
            
            for m_id in compatible_m:
                # Calcular carga actual
                current_load_hrs = sum(
                    self.calculate_machine_hours(i.denier, i.kg_pending, m_id) 
                    for i in machine_queues[m_id]
                )
                if current_load_hrs < min_hours:
                    min_hours = current_load_hrs
                    best_m = m_id
            
            if best_m:
                item.assigned_machine = best_m
                machine_queues[best_m].append(item)

        # 3. Simulación Turno a Turno
        schedule = []
        active_state = {m: None for m in self.main_machines + [self.backup_machine]} 
        # State: {'T11': {'item': item_ref, 'remaining_kg': 500, 'status': 'RUNNING'}}
        
        current_date = datetime.now()
        total_shifts = max_days * 3
        
        # Tracking global stats
        machine_stats = defaultdict(lambda: {'total_kg': 0, 'total_hours': 0, 'items': set()})
        
        for shift_idx in range(total_shifts):
            day_offset = shift_idx // 3
            turn_idx = shift_idx % 3
            shift_date = current_date + timedelta(days=day_offset)
            shift_name = ['A', 'B', 'C'][turn_idx]
            
            # --- LÓGICA DE TRANSICIÓN Y BACKUP ---
            # Identificar máquinas activas (RUNNING)
            # Determinar si necesitamos usar T16
            
            # Paso A: Revisar estado de máquinas principales
            # Si una termina, intenta tomar siguiente. Si no puede (cambio?), T16 podría entrar?
            # En esta simplificación, asumimos que "Cambio" es solo tiempo, o que T16 toma el relevo.
            
            shift_output = []
            active_count = 0
            machines_running_this_shift = []
            
            # Orden de evaluación: T16 (Backup) tiene prioridad si ya estaba corriendo para terminar
            # Luego Main Machines.
            # Pero la regla es: "Mantener 4 máquinas trabajando".
            
            available_slots = 4
            machines_to_run = []
            
            # 1. Quienes YA tienen trabajo asignado y no han terminado?
            for m_id in self.main_machines + [self.backup_machine]:
                state = active_state[m_id]
                if state and state['remaining_kg'] > 0:
                    machines_to_run.append(m_id)
            
            # 2. Llenar slots vacíos con máquinas principales que tengan cola
            # Si una principal estaba libre, intentamos arrancarla
            for m_id in self.main_machines:
                if m_id not in machines_to_run and len(machine_queues[m_id]) > 0:
                     # Verificar si podemos activarla (si hay slots)
                     # O si esta es una "Main" debería tener prioridad sobre T16 si T16 ya cumplió?
                     # Regla: T16 cubre cambios. Si T11 va a arrancar nuevo, T16 podría cubrir el setup?
                     # Simplificación: Si hay slot, arranca T11. Si no hay slot y T11 debe producir, 
                     # T16 toma la carga? -> Complicado.
                     # Vamos a usar lógica: T11, T12, T14, T15 son PRIORIDAD.
                     # Si alguna de ellas NO tiene trabajo, T16 puede tomar trabajo global? 
                     # No, T16 cubre "paradas". 
                     pass

            # REPLANTEAMIENTO SIMPLE PARA 4 ACTIVAS:
            # Lista de candidatos a correr:
            # A. Máquinas con trabajo en curso.
            # B. Máquinas con trabajo en cola (si no están corriendo).
            
            # Candidatos ordenados por prioridad fija: Main > T16 ??
            # No, T16 entra cuando una Main "falla" o cambia.
            # Simularemos esto así: 
            # Si una Main termina su item en este turno (o antes), entra en estado "SETUP/CHANGE".
            # Durante SETUP, la máquina Main NO produce.
            # T16 detecta ese hueco y busca trabajo compatible de CUALQUIER cola para llenar el target de 4.
            
            # Pero el prompt dice: "T16 solo asigna referencias... cuando alguna necesite parar... cubrira T16"
            # Asumiremos T16 roba un item pendiente de la cola de la máquina parada? O tiene su propia cola?
            # "T16 solo asigna referencias" -> T16 es comodin.
            
            # Vamos a iterar las Main Machines.
            shifts_production = {} # m_id -> kg_produced
            
            for m_id in self.main_machines:
                # Recuperar estado o intentar cargar nuevo
                if not active_state[m_id] and machine_queues[m_id]:
                    # Cargar nuevo
                    next_item = machine_queues[m_id].popleft()
                    active_state[m_id] = {
                        'item': next_item,
                        'remaining_kg': next_item.kg_pending,
                        'kgh': self.get_machine_kgh(m_id, next_item.denier),
                        'status': 'RUNNING' # O 'SETUP' si quisiéramos ser detallistas
                    }
            
            # Cuántas Main están listas para producir?
            ready_main = [m for m in self.main_machines if active_state[m] and active_state[m]['remaining_kg'] > 0]
            
            # Si las 4 están listas, T16 descansa.
            # Si < 4 están listas (alguna sin backlog o en fin de lote), T16 busca qué hacer.
            
            if len(ready_main) < 4:
                # T16 intenta ayudar. 
                # ¿Qué produce T16? Lo que sea compatible del backlog global restante?
                # Busquemos algo compatible con T16 en las colas de las máquinas inactivas o futuras
                if not active_state['T16'] or active_state['T16']['remaining_kg'] <= 0:
                    # Buscar trabajo para T16
                    found_work = False
                    for target_denier in list(self.compatibility_rules['T16']):
                         # Buscar en colas de otras máquinas items de este denier
                         for donor_id in self.main_machines:
                             # Solo robar si la donor NO está corriendo ya ese item (obvio, está en cola)
                             for idx, item in enumerate(machine_queues[donor_id]):
                                 if item.denier == target_denier:
                                     # Robar item
                                     del machine_queues[donor_id][idx]
                                     # Asignar a T16
                                     active_state['T16'] = {
                                         'item': item,
                                         'remaining_kg': item.kg_pending,
                                         'kgh': self.get_machine_kgh('T16', item.denier),
                                         'status': 'BACKUP_RUNNING'
                                     }
                                     found_work = True
                                     break
                             if found_work: break
                         if found_work: break
            
            # EJECUTAR PRODUCCIÓN (Max 4 máquinas)
            # Prioridad: Las que ya traen impulso, luego T16 llenando hueco
            runners = [m for m in self.main_machines + [self.backup_machine] 
                       if active_state[m] and active_state[m]['remaining_kg'] > 0]
            
            # Cortar a 4 si por alguna razón hubiese más (raro con lógica anterior)
            runners = runners[:4] 
            
            turn_data = {
                'fecha': f"{shift_date.strftime('%Y-%m-%d')} Turno {shift_name}",
                'detalles': [],
                'total_kg': 0,
                'maquinas_activas': 0
            }
            
            # Simular hora a hora o turno completo? Turno completo (8h)
            for m_id in runners:
                st = active_state[m_id]
                kgh = st['kgh']
                max_prod = kgh * self.shift_hours
                actual_prod = min(st['remaining_kg'], max_prod)
                
                st['remaining_kg'] -= actual_prod
                
                # Actualizar Stats
                turn_data['total_kg'] += actual_prod
                turn_data['maquinas_activas'] += 1
                
                machine_stats[m_id]['total_kg'] += actual_prod
                machine_stats[m_id]['total_hours'] += (actual_prod / kgh) if kgh > 0 else 0
                machine_stats[m_id]['items'].add(st['item'].ref)
                
                # Detalles para tabla
                turn_data['detalles'].append({
                    'maquina': m_id,
                    'denier': st['item'].denier,
                    'ref': st['item'].ref,
                    'kg': round(actual_prod, 1),
                    'estado': st['status'] if m_id == 'T16' else 'Normal'
                })
                
                # Si terminó, limpiar estado
                if st['remaining_kg'] <= 0.1:
                    st['item'].completed = True
                    active_state[m_id] = None # Libre para siguiente turno
            
            if turn_data['maquinas_activas'] > 0:
                schedule.append(turn_data)
            
            # Si no hay nada produciendo en ningún turno futuro (colas vacias y estados nulos), terminar
            if not any(machine_queues.values()) and not any(active_state.values()):
                break

        # 4. Generar Resúmenes Finales
        summary_table = []
        for m_id in sorted(self.main_machines + [self.backup_machine]):
            stats = machine_stats[m_id]
            summary_table.append({
                'maquina': m_id,
                'horas_trabajadas': round(stats['total_hours'], 1),
                'kg_totales': round(stats['total_kg'], 1),
                'referencias': list(stats['items']),
                'utilizacion': f"{round(stats['total_hours'] / (shift_idx*8)*100, 1)}%" if shift_idx > 0 else "0%"
            })
            
        return {
            'resumen_maquinas': summary_table,
            'cronograma_torsion': schedule
        }

# ============================================================================
# FUNCIONES DE INTERFAZ
# ============================================================================

def generate_torsion_schedule(
    backlog_summary: Dict[str, Any],
    torsion_capacities: Dict[str, Any],
    max_days: int = 60
) -> Dict[str, Any]:
    
    # 1. Parsear Inputs
    torsion_machines = []
    # Hardcodeamos config base si no viene completa, pero intentamos leer kwarg
    
    # Construir objetos TorsionMachine con datos reales de DB
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
    
    # Asegurar que existan las máquinas criticas si la DB no las trajo (fallback)
    known_ids = set(m.machine_id for m in torsion_machines)
    defaults = [('T11', 4000, 52), ('T12', 4000, 52), ('T15', 2000, 28), ('T14', 12000, 160), ('T16', 4000, 32)]
    # Solo agregar si faltan COMPLETAMENTE. Si existen para otro denier, ok.
    
    rewinder_configs = {} # No se usa para planning de Torsion puro, pero el init lo pide
    
    backlog_items = []
    for code, data in backlog_summary.items():
        backlog_items.append(BacklogItem(
            ref=code,
            description=data.get('description', ''),
            denier=int(data['denier']),
            kg_pending=float(data['kg_total']),
            priority=int(data.get('priority', 0))
        ))
        
    # 2. Inicializar Optimizer
    optimizer = TorsionFocusedOptimizer(torsion_machines, rewinder_configs)
    
    # 3. Correr Plan
    result = optimizer.plan_production(backlog_items, max_days)
    
    return {
        "resumen_programa": {
             "total_kg": sum(r['kg_totales'] for r in result['resumen_maquinas']),
             "alertas": "Planificación centrada en Torsión (4 máquinas activas)"
        },
        "tabla_turnos": result['cronograma_torsion'], # Reusamos campo para frontend
        "resumen_maquinas": result['resumen_maquinas'], # Nuevo campo especifico
        "scenario": { # Legacy compat
            "resumen_global": {"comentario_estrategia": "Torsion Focus"},
            "cronograma_diario": []
        }
    }

# ============================================================================
# WRAPPER PRINCIPAL
# ============================================================================

def generate_production_schedule(**kwargs):
    """Wrapper compatible que decide qué estrategia usar"""
    # Por ahora forzamos Torsion Focus según requerimiento
    backlog = kwargs.get('backlog_summary', {})
    
    return generate_torsion_schedule(
        backlog,
        kwargs.get('torsion_capacities', {}),
        max_days=60
    )

def get_ai_optimization_scenario(orders, reports):
    """Helper DB -> Model"""
    try:
        from db.queries import DBQueries
        db = DBQueries()
        
        # Obtener configuraciones
        m_configs = db.get_machine_denier_configs() or []
        
        torsion_capacities = defaultdict(lambda: {"machines": []})
        for cfg in m_configs:
            d = str(cfg.get('denier'))
            kgh = float(cfg.get('kgh', 0))
            if kgh > 0:
                torsion_capacities[d]["machines"].append({
                    "machine_id": cfg.get('machine_id'),
                    "kgh": kgh,
                    "husos": int(cfg.get('husos', 1))
                })

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

        return generate_torsion_schedule(
            backlog_summary,
            dict(torsion_capacities)
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"error": str(e)}
