import sys
import os
import json
from datetime import datetime, timedelta

# Add parent directory to path to import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from integrations.openai_ia import generate_production_schedule

def run_test():
    print("Running Schedule Verification...")
    
    # Mock Data
    orders = [] # Not used in greedy V4 directly (uses backlog_summary)
    
    # Rewinder Capabilities (Kg/h per post, N_optimo)
    # Ref 1: 6000 Denier -> 10 Kg/h per post, 7 posts/op
    # Ref 2: 12000 Denier -> 20 Kg/h per post, 5 posts/op
    rewinder_capacities = {
        "6000": {"kg_per_hour": 10.0, "n_optimo": 7},
        "12000": {"kg_per_hour": 20.0, "n_optimo": 5}
    }
    
    # Torsion Capacities (Total available Kg/h for that denier)
    # Ref 1: 6000 -> Max 100 Kg/h (e.g. 2 machines * 50)
    # Ref 2: 12000 -> Max 150 Kg/h (e.g. 3 machines * 50)
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
                {"machine_id": "T01", "kgh": 50.0, "husos": 100},
                {"machine_id": "T02", "kgh": 50.0, "husos": 100},
                {"machine_id": "T03", "kgh": 50.0, "husos": 100}
            ]
        }
    }
    
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
        shifts = [
            {"date": "2023-10-27", "working_hours": 24},
            {"date": "2023-10-28", "working_hours": 24},
            {"date": "2023-10-29", "working_hours": 24}
        ]
        
        result = generate_production_schedule(
            orders, 
            rewinder_capacities,
            total_rewinders=28,
            shifts=shifts,
            torsion_capacities=torsion_capacities,
            backlog_summary=backlog_summary,
            strategy='greedy'
        )
        
        scenario = result.get('scenario', {})
        daily = scenario.get('cronograma_diario', [])
        
        # 3. Analyze First Day/Shift
        if not daily:
            print("FAILED: No schedule generated.")
            return

        print(f"Total Days: {len(daily)}")
        
        for d in daily:
            print(f"\nFecha: {d['fecha']}")
            print("  Rewinder Shifts:")
            for t in d['turnos']:
                print(f"    Shift {t['nombre']} ({t['horario']}): {t['operarios_requeridos']} Ops")
                for a in t['asignaciones']:
                    print(f"      - {a['referencia']} ({a['denier']}): {a['puestos']} posts, {a['kg_producidos']} Kg")
            
            print("  Torsion Shifts:")
            for t in d['turnos_torsion']:
                 print(f"    Shift {t['nombre']}: {len(t['asignaciones'])} asignaciones")
                 for a in t['asignaciones']:
                     print(f"      - {a['maquina']} -> {a['referencia']} ({a['kgh_maquina']} Kg/h)")

        print("\nTest Passed Successfully.")
        
    except Exception as e:
        print(f"FAILED with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
