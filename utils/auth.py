import os
import logging
from utils.constants import OWNER_ID

logger = logging.getLogger(__name__)

def is_owner(user_id: int) -> bool:
    """Owner (Mastermind) ဟုတ်/မဟုတ် စစ်ဆေးရန်"""
    return user_id == OWNER_ID

def is_super_admin(user_id: int) -> bool:
    """Super Admin သို့မဟုတ် Owner ဟုတ်/မဟုတ် စစ်ဆေးရန်"""
    # Owner ဆိုရင် အလိုလို Super Admin ဖြစ်ပြီးသားပါ
    if is_owner(user_id):
        return True
    
    sa_str = os.getenv("SUPER_ADMINS", "")
    if sa_str:
        try:
            admins = [int(x.strip()) for x in sa_str.split(",") if x.strip()]
            return user_id in admins
        except ValueError as e:
            logger.error(f"Invalid SUPER_ADMINS format in .env: {e}")
            
    return False