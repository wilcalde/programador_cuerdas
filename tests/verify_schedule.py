import sys
import os
import json
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from integrations.openai_ia import generate_production_schedule

def test_schedule_generation():
    print("Running Schedule Verification...")

    # 1. Mock Data
    orders = [] # Not used directly if backlog_summary is provided, but passed for structure
    
    rewinder_capacities = {
        "6000": {"kg_per_hour": 10.0, "n_optimo": 7},
        "12000": {"kg_per_hour": 20.0, "n_optimo": 5}
    }
    
    torsion_capacities = {
        "6000": {
            "total_kgh": 100.0,
            "machines": [
                {"machine_id": "T14", "kgh": 50.0, "husos": 100},
                {"machine_id": "T15", "kgh": 50.0, "husos": 100}
            ]
        },
        "12000": {
            "total_kgh": 150.0,
            "machines": [
                {"machine_id": "T16", "kgh": 150.0, "husos": 120}
            ]
        }
    }
    
    shifts = [
        {"date": "2023-10-27", "working_hours": 24},
        {"date": "2023-10-28", "working_hours": 24},
        {"date": "2023-10-29", "working_hours": 24}
    ]
    
    # Backlog Summary (The logic input)
    # Ref 1: High pending, requires 6000 denier
    # Ref 2: Medium pending, requires 12000 denier
    backlog_summary = {
        "REF001": {
            "description": "Cabuya 6000",
            "kg_total": 5000.0,
            "is_priority": True,
            "denier": "6000",
            "h_proceso": 0 # Calculated inside if 0
        },
        "REF002": {
            "description": "Cabuya 12000",
            "kg_total": 8000.0,
            "is_priority": False,
            "denier": "12000",
            "h_proceso": 0
        }
    }
    
    # 2. Run Scheduling
    try:
        result = generate_production_schedule(
            orders=orders,
            rewinder_capacities=rewinder_capacities,
            total_rewinders=28,
            shifts=shifts,
            torsion_capacities=torsion_capacities,
            backlog_summary=backlog_summary,
            strategy='priority'
        )
        
        # 3. Analyze Result
        scenario = result.get('scenario', {})
        daily = scenario.get('cronograma_diario', [])
        
        print(f"\nGenerado {len(daily)} dias.")
        print(f"Comentario: {scenario.get('resumen_global', {}).get('comentario_estrategia')}")
        
        for d in daily:
            print(f"\nFecha: {d['fecha']}")
            print("  Rewinder Shifts:")
            for t in d['turnos']:
                ops = t['operarios_requeridos']
                kg = sum(a['kg_producidos'] for a in t['asignaciones'])
                posts = sum(a['puestos'] for a in t['asignaciones'])
                print(f"    {t['nombre']}: {posts} puestos, {ops} ops, {kg:.1f} Kg")
                for a in t['asignaciones']:
                    print(f"      - {a['referencia']} ({a['denier']}): {a['puestos']} puestos -> {a['kg_producidos']:.1f} Kg")
            
            print("  Torsion Shifts:")
            for t in d['turnos_torsion']:
                ops = t['operarios_requeridos']
                kg = sum(a['kg_turno'] for a in t['asignaciones'])
                print(f"    {t['nombre']}: {len(t['asignaciones'])} asignaciones, {kg:.1f} Kg")
                for a in t['asignaciones']:
                    print(f"      - {a['maquina']} -> {a['referencia']}: {a['kg_turno']:.1f} Kg ({a['husos_asignados']}/{a['husos_totales']} husos)")
                    
        print("\nTest Passed Successfully.")
        return True
        
    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_schedule_generation()
