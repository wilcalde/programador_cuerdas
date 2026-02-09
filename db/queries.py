from .client import get_supabase_client
from typing import List, Dict, Any
from supabase import create_client, Client
from logic.formulas import get_n_optimo_rew, get_kgh_torsion

class DBQueries:
    def __init__(self):
        self.supabase = get_supabase_client()

    # --- Denier Catalog ---
    def get_deniers(self) -> List[Dict[str, Any]]:
        response = self.supabase.table('deniers').select('*').order('name').execute()
        return response.data

    def create_denier(self, name: str, cycle_time: float) -> Dict[str, Any]:
        response = self.supabase.table('deniers').insert({"name": name, "cycle_time": cycle_time}).execute()
        return response.data[0] if response.data else {}

    # --- Machines & Production ---
    def get_machines_torsion(self) -> List[Dict[str, Any]]:
        response = self.supabase.table('machines_torsion').select('*').order('id').execute()
        return response.data

    def update_machine_torsion(self, machine_id: str, rpm: int, torsions: int, husos: int) -> Dict[str, Any]:
        response = self.supabase.table('machines_torsion').update({
            "rpm": rpm, "torsiones": torsions, "husos": husos
        }).eq('id', machine_id).execute()
        return response.data[0] if response.data else {}

    # --- Orders / Backlog ---
    def get_orders(self) -> List[Dict[str, Any]]:
        # Select orders along with their denier name
        response = self.supabase.table('orders').select('*, deniers(name)').order('required_date').execute()
        return response.data

    def create_order(self, denier_id: str, kg: float, required_date: str) -> Dict[str, Any]:
        response = self.supabase.table('orders').insert({
            "denier_id": denier_id,
            "total_kg": kg,
            "produced_kg": 0,
            "required_date": required_date,
            "status": 'backlog'
        }).execute()
        return response.data[0] if response.data else {}

    # Update an existing order
    def update_order(self, order_id: str, denier_id: str, kg: float, required_date: str) -> Dict[str, Any]:
        response = self.supabase.table('orders').update({
            "denier_id": denier_id,
            "total_kg": kg,
            "required_date": required_date
        }).eq('id', order_id).execute()
        return response.data[0] if response.data else {}

    # Delete an order by ID
    def delete_order(self, order_id: str) -> Dict[str, Any]:
        response = self.supabase.table('orders').delete().eq('id', order_id).execute()
        return response.data

    def update_produced_kg(self, order_id: str, produced_kg: float) -> Dict[str, Any]:
        response = self.supabase.table('orders').update({"produced_kg": produced_kg}).eq('id', order_id).execute()
        return response.data[0] if response.data else {}

    # --- Reports & Maintenance ---
    def create_report(self, machine_id: str, report_type: str, description: str, impact_hours: float) -> Dict[str, Any]:
        response = self.supabase.table('reports').insert({
            "machine_id": machine_id,
            "type": report_type,
            "description": description,
            "impact_hours": impact_hours,
            "timestamp": datetime.now().isoformat()
        }).execute()
        return response.data[0] if response.data else {}

    # Get all machine-denier configurations with calculated Kg/h
    def get_machine_denier_configs(self) -> List[Dict[str, Any]]:
        response = self.supabase.table('machine_denier_config').select('*').execute()
        return response.data

    # Create or update machine-denier configuration
    def upsert_machine_denier_config(self, machine_id: str, denier: str, rpm: int, torsiones_metro: int, husos: int) -> Dict[str, Any]:
        # Simple upsert logic using a unique constraint on (machine_id, denier) if available, 
        # or manual delete/insert as fallback.
        data = {
            "machine_id": machine_id,
            "denier": denier,
            "rpm": rpm,
            "torsiones_metro": torsiones_metro,
            "husos": husos
        }
        response = self.supabase.table('machine_denier_config').upsert(data, on_conflict='machine_id,denier').execute()
        return response.data[0] if response.data else {}

    # Get all denier configurations for a specific machine
    def get_config_for_machine(self, machine_id: str) -> List[Dict[str, Any]]:
        response = self.supabase.table('machine_denier_config').select('*').eq('machine_id', machine_id).execute()
        return response.data

    # Get all rewinder denier configurations
    def get_rewinder_denier_configs(self) -> List[Dict[str, Any]]:
        response = self.supabase.table('rewinder_denier_config').select('*').execute()
        return response.data

    # Create or update rewinder denier configuration
    def upsert_rewinder_denier_config(self, denier: str, mp_segundos: float, tm_minutos: float) -> Dict[str, Any]:
        data = {
            "denier": denier,
            "mp_segundos": mp_segundos,
            "tm_minutos": tm_minutos
        }
        response = self.supabase.table('rewinder_denier_config').upsert(data, on_conflict='denier').execute()
        return response.data[0] if response.data else {}

    # Get shifts for a date range
    def get_shifts(self, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        query = self.supabase.table('shifts').select('*')
        if start_date:
            query = query.gte('date', start_date)
        if end_date:
            query = query.lte('date', end_date)
        response = query.order('date').execute()
        return response.data

    # Create or update a shift for a specific date
    def upsert_shift(self, date: str, working_hours: int) -> Dict[str, Any]:
        data = {
            "date": date,
            "working_hours": working_hours
        }
        response = self.supabase.table('shifts').upsert(data, on_conflict='date').execute()
        return response.data[0] if response.data else {}

    # Get all data needed for production scheduling
    def get_all_scheduling_data(self) -> Dict[str, Any]:
        """Get all data needed for production scheduling"""
        orders = self.get_orders()
        rewinder_configs = self.get_rewinder_denier_configs()
        torsion_configs = self.get_machine_denier_configs()
        
        # Convert rewinder configs to a dict keyed by denier
        rewinder_dict = {}
        for config in rewinder_configs:
            denier = config['denier']
            tm_min = config['tm_minutos']
            # Calculate Kg per hour at 80% productivity
            kg_per_hour = (60 / tm_min) * 0.8 if tm_min > 0 else 0
            # Calculate N (machines per operator)
            n_optimo = get_n_optimo_rew(tm_min, config['mp_segundos'])
            
            rewinder_dict[denier] = {
                "kg_per_hour": round(kg_per_hour, 1),
                "mp_segundos": config['mp_segundos'],
                "tm_minutos": tm_min,
                "n_optimo": n_optimo
            }
        
        # Calculate Torsion capacities per denier
        torsion_capacities = {}
        # Backlog deniers (from orders)
        backlog_deniers = {o.get('deniers', {}).get('name') for o in orders if o.get('deniers')}
        
        for denier_name in backlog_deniers:
            if not denier_name: continue
            
            # Find all machines that can produce this denier
            compatible_torsion = [c for c in torsion_configs if c['denier'] == denier_name]
            
            # Sum capacities
            total_kgh = 0
            machines_details = []
            
            for config in compatible_torsion:
                # Try to get numeric denier value from name (e.g., '12000' -> 12000, '6000 expo' -> 6000)
                try:
                    # Use split to get the first numeric part
                    denier_val = float(denier_name.split(' ')[0])
                    kgh = get_kgh_torsion(
                        denier=denier_val,
                        rpm=config['rpm'],
                        torsiones_metro=config['torsiones_metro'],
                        husos=config['husos']
                    )
                    
                    if kgh <= 0:
                        continue

                    total_kgh += kgh
                    machines_details.append({
                        "machine_id": config['machine_id'],
                        "kgh": round(kgh, 2)
                    })
                except ValueError:
                    continue
            
            torsion_capacities[denier_name] = {
                "total_kgh": round(total_kgh, 2),
                "machines": machines_details
            }
        
        return {
            "orders": orders,
            "rewinder_capacities": rewinder_dict,
            "torsion_capacities": torsion_capacities,
            "shifts": self.get_shifts() # Fetch all defined shifts
        }

    def save_scheduling_scenario(self, name: str, plan_data: Dict[str, Any]) -> Dict[str, Any]:
        response = self.supabase.table('saved_schedules').insert({
            "name": name,
            "plan_data": plan_data,
            "timestamp": datetime.now().isoformat()
        }).execute()
        return response.data[0] if response.data else {}

    def get_saved_schedules(self, limit: int = 10) -> List[Dict[str, Any]]:
        response = self.supabase.table('saved_schedules').select('*').order('timestamp', descending=True).limit(limit).execute()
        return response.data
