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

    def get_orders(self) -> List[Dict[str, Any]]:
        # Modified to join with inventarios_cabuyas to get details
        response = self.supabase.table("orders").select("*, deniers(name), inventarios_cabuyas(descripcion)").execute()
        return response.data

    def add_order(self, denier_id: str, total_kg: float, required_date: str, cabuya_codigo: str = None) -> Dict[str, Any]:
        data = {
            "denier_id": denier_id,
            "total_kg": total_kg,
            "required_date": required_date,
            "cabuya_codigo": cabuya_codigo
        }
        response = self.supabase.table("orders").insert(data).execute()
        return response.data[0] if response.data else {}

    def update_order(self, order_id: str, denier_id: str, total_kg: float, required_date: str, cabuya_codigo: str = None) -> Dict[str, Any]:
        data = {
            "denier_id": denier_id,
            "total_kg": total_kg,
            "required_date": required_date,
            "cabuya_codigo": cabuya_codigo
        }
        response = self.supabase.table("orders").update(data).eq("id", order_id).execute()
        return response.data[0] if response.data else {}

    def delete_order(self, order_id: str) -> bool:
        response = self.supabase.table("orders").delete().eq("id", order_id).execute()
        return True

    def get_inventarios_cabuyas(self) -> List[Dict[str, Any]]:
        response = self.supabase.table("inventarios_cabuyas").select("*").order("codigo").execute()
        return response.data
    
    def get_pending_requirements(self) -> List[Dict[str, Any]]:
        """Get all cabuyas inventory records with negative requirements"""
        response = self.supabase.table("inventarios_cabuyas").select("*").lt("requerimientos", 0).order("requerimientos", desc=False).execute()
        return response.data if response.data else []

    def update_cabuya_priority(self, codigo: str, prioridad: bool):
        """Update the priority status for a specific cabuya"""
        return self.supabase.table("inventarios_cabuyas").update({"prioridad": prioridad}).eq("codigo", codigo).execute()
