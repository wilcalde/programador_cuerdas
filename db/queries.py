from .client import get_supabase_client
from typing import List, Dict, Any
from supabase import create_client, Client
from logic.formulas import get_n_optimo_rew, get_kgh_torsion

class DBQueries:
    def __init__(self):
        self.supabase = get_supabase_client()

    def get_deniers(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("deniers").select("*").execute()
        return response.data

    def create_denier(self, name: str, cycle_time: float):
        data = {"name": name, "cycle_time_standard": cycle_time}
        return self.supabase.table("deniers").insert(data).execute()

    def get_machines_torsion(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("machines_torsion").select("*").execute()
        return response.data

    def get_orders(self) -> List[Dict[str, Any]]:
        # Simplified to avoid potential join issues, as it's not currently used in the backlog view
        response = self.supabase.table("orders").select("*, deniers(name)").execute()
        return response.data

    def create_order(self, denier_id: str, kg: float, required_date: str, cabuya_codigo: str = None):
        data = {
            "denier_id": denier_id,
            "total_kg": kg,
            "priority": 3,
            "required_date": required_date,
            "cabuya_codigo": cabuya_codigo
        }
        return self.supabase.table("orders").insert(data).execute()
    
    def update_order(self, order_id: str, denier_id: str, kg: float, required_date: str, cabuya_codigo: str = None):
        data = {
            "denier_id": denier_id,
            "total_kg": kg,
            "required_date": required_date,
            "cabuya_codigo": cabuya_codigo
        }
        return self.supabase.table("orders").update(data).eq("id", order_id).execute()
    
    def delete_order(self, order_id: str):
        return self.supabase.table("orders").delete().eq("id", order_id).execute()

    def get_inventarios_cabuyas(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("inventarios_cabuyas").select("*").order("codigo").execute()
        return response.data
    
    def get_pending_requirements(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("inventarios_cabuyas").select("*").lt("requerimientos", 0).order("requerimientos", desc=False).execute()
        return response.data if response.data else []

    def update_cabuya_priority(self, codigo: str, prioridad: bool):
        return self.supabase.table("inventarios_cabuyas").update({"prioridad": prioridad}).eq("codigo", codigo).execute()

    def get_machine_denier_configs(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("machine_denier_config").select("*").execute()
        return response.data if response.data else []
    
    def upsert_machine_denier_config(self, machine_id: str, denier: str, rpm: int, torsiones_metro: int, husos: int):
        data = {
            "machine_id": machine_id,
            "denier": denier,
            "rpm": rpm,
            "torsiones_metro": torsiones_metro,
            "husos": husos
        }
        return self.supabase.table("machine_denier_config").upsert(data, on_conflict="machine_id,denier").execute()
    
    def get_rewinder_denier_configs(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("rewinder_denier_config").select("*").execute()
        return response.data if response.data else []
    
    def upsert_rewinder_denier_config(self, denier: str, mp_segundos: float, tm_minutos: float):
        data = {
            "denier": denier,
            "mp_segundos": mp_segundos,
            "tm_minutos": tm_minutos
        }
        return self.supabase.table("rewinder_denier_config").upsert(data, on_conflict="denier").execute()
    
    def get_shifts(self, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        query = self.supabase.table("shifts").select("*")
        if start_date:
            query = query.gte("date", start_date)
        if end_date:
            query = query.lte("date", end_date)
        response = query.order("date").execute()
        return response.data if response.data else []

    def upsert_shift(self, date: str, working_hours: int):
        data = {
            "date": date,
            "working_hours": working_hours
        }
        return self.supabase.table("shifts").upsert(data, on_conflict="date").execute()

    def update_cabuya_inventory_security(self, codigo: str, security_value: float):
        return self.supabase.table("inventarios_cabuyas").update({"inventario_seguridad": security_value}).eq("codigo", codigo).execute()
