import logging
from typing import Optional
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)


class SupabaseService:
    _instance: Optional[Client] = None

    @classmethod
    def get_client(cls) -> Optional[Client]:
        """
        Retourne une instance singleton du client Supabase.
        Renvoie None si l'URL ou la clÃ© ne sont pas encore configurÃ©es.
        """
        if cls._instance is not None:
            return cls._instance

        url = settings.SUPABASE_URL
        key = settings.SUPABASE_KEY

        if not url or "your-project.supabase.co" in url or not key or "your-anon" in key:
            logger.warning("Supabase credentials not configured. Running in mock/offline mode.")
            return None

        try:
            cls._instance = create_client(url, key)
            logger.info("Supabase client successfully initialized.")
            return cls._instance
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")
            return None


def get_supabase_client() -> Optional[Client]:
    return SupabaseService.get_client()

